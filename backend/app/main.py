import os
from datetime import datetime, timezone
from typing import Optional
from fastapi import FastAPI, Depends, HTTPException, Header, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, String, ForeignKey, DateTime, Text, select, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, Session, sessionmaker
from jwt import PyJWKClient, decode, PyJWTError
from app.tasks import ticket_created

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./local.db")
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(engine)
class Base(DeclarativeBase): pass
class Ticket(Base):
    __tablename__ = "tickets"
    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(30), default="new")
    priority: Mapped[str] = mapped_column(String(20), default="medium")
    department: Mapped[str] = mapped_column(String(100), default="General")
    requester: Mapped[str] = mapped_column(String(200))
    assignee: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
class Comment(Base):
    __tablename__ = "comments"
    id: Mapped[int] = mapped_column(primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id"))
    author: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text)
class Audit(Base):
    __tablename__ = "audit_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id"))
    actor: Mapped[str] = mapped_column(String(200))
    action: Mapped[str] = mapped_column(String(80))
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
Base.metadata.create_all(engine)  # MVP only: replace with Alembic migrations before production

app = FastAPI(title="Energuate Service Desk API", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=os.getenv("CORS_ORIGINS", "http://localhost:3000").split(","), allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
def db():
    with SessionLocal() as session: yield session

def principal(authorization: Optional[str] = Header(default=None), x_dev_user: Optional[str] = Header(default=None)):
    if os.getenv("AUTH_MODE", "dev") == "dev":
        return {"sub": x_dev_user or "local-demo", "roles": ["admin"]}
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Bearer token required")
    issuer = os.environ["OIDC_ISSUER"]
    try:
        jwk = PyJWKClient(f"{issuer}/protocol/openid-connect/certs").get_signing_key_from_jwt(authorization[7:])
        claims = decode(authorization[7:], jwk.key, algorithms=["RS256"], audience=os.environ["OIDC_AUDIENCE"], issuer=issuer)
        roles = claims.get("realm_access", {}).get("roles", [])
        return {"sub": claims["sub"], "roles": roles}
    except (PyJWTError, Exception):
        raise HTTPException(401, "Invalid token")

def require_agent(user=Depends(principal)):
    if not set(user["roles"]).intersection({"admin", "agent"}): raise HTTPException(403, "Agent role required")
    return user

class TicketIn(BaseModel):
    title: str = Field(min_length=3, max_length=200)
    description: str = ""
    priority: str = Field(default="medium", pattern="^(low|medium|high|critical)$")
    department: str = "General"
class TicketPatch(BaseModel):
    status: Optional[str] = Field(default=None, pattern="^(new|triage|assigned|in_progress|pending|resolved|closed)$")
    priority: Optional[str] = Field(default=None, pattern="^(low|medium|high|critical)$")
    assignee: Optional[str] = None
class CommentIn(BaseModel):
    body: str = Field(min_length=1)
def ticket_dict(t):
    return {k: getattr(t,k) for k in ["id","title","description","status","priority","department","requester","assignee","created_at"]}
@app.get("/health")
def health(): return {"status":"ok"}
@app.get("/tickets")
def tickets(session:Session=Depends(db), user=Depends(principal)):
    q = select(Ticket).order_by(Ticket.id.desc())
    if not set(user["roles"]).intersection({"admin", "agent"}): q=q.where(Ticket.requester==user["sub"])
    return [ticket_dict(t) for t in session.scalars(q).all()]
@app.post("/tickets", status_code=201)
def create_ticket(data:TicketIn, session:Session=Depends(db), user=Depends(principal)):
    t=Ticket(**data.model_dump(),requester=user["sub"])
    session.add(t); session.flush(); session.add(Audit(ticket_id=t.id,actor=user["sub"],action="created")); session.commit();session.refresh(t)
    try: ticket_created.delay(t.id)
    except Exception: pass  # queue failure must be logged and retried in production
    return ticket_dict(t)
@app.patch("/tickets/{ticket_id}")
def update_ticket(ticket_id:int, data:TicketPatch, session:Session=Depends(db), user=Depends(require_agent)):
    t=session.get(Ticket,ticket_id)
    if not t: raise HTTPException(404,"Ticket not found")
    for k,v in data.model_dump(exclude_unset=True).items(): setattr(t,k,v)
    session.add(Audit(ticket_id=t.id,actor=user["sub"],action="updated"));session.commit();session.refresh(t)
    return ticket_dict(t)
@app.post("/tickets/{ticket_id}/comments", status_code=201)
def add_comment(ticket_id:int, data:CommentIn, session:Session=Depends(db), user=Depends(principal)):
    t=session.get(Ticket,ticket_id)
    if not t: raise HTTPException(404,"Ticket not found")
    if not set(user["roles"]).intersection({"admin","agent"}) and t.requester!=user["sub"]: raise HTTPException(403)
    c=Comment(ticket_id=ticket_id,author=user["sub"],body=data.body);session.add(c);session.commit();session.refresh(c)
    return {"id":c.id,"ticket_id":c.ticket_id,"author":c.author,"body":c.body}
@app.get("/stats")
def stats(session:Session=Depends(db), user=Depends(principal)):
    q=select(Ticket.status,func.count()).group_by(Ticket.status)
    if not set(user["roles"]).intersection({"admin","agent"}):q=q.where(Ticket.requester==user["sub"])
    return dict(session.execute(q).all())
