"""Automatizaciones (solo admin): CRUD de reglas, historial de ejecuciones y simulación (dry-run)."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from app.core.db import get_db, utcnow
from app.core.security import Principal, require_roles
from app.models.automation import AutomationExecution, AutomationRule
from app.models.core import Ticket
from app.services.automation import engine
from app.services.automation.schemas import (
    ACTION_TYPES,
    FIELDS,
    NOTIFY_TARGETS,
    OPERATORS,
    TRIGGERS,
    ActionIn,
    DryRunIn,
    ExecutionPage,
    RuleIn,
    RuleOut,
    RulePatch,
)

router = APIRouter(prefix="/automations", tags=["automatizaciones"])

admin_only = require_roles("admin")


def _rule_not_found() -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, detail="Regla no encontrada")


def _stats(session: Session, rule_ids: list[int]) -> dict[int, tuple[int, object]]:
    if not rule_ids:
        return {}
    rows = session.execute(
        select(
            AutomationExecution.rule_id,
            func.count(AutomationExecution.id),
            func.max(AutomationExecution.created_at),
        )
        .where(AutomationExecution.rule_id.in_(rule_ids))
        .group_by(AutomationExecution.rule_id)
    ).all()
    return {rule_id: (count, last) for rule_id, count, last in rows}


def _rule_out(rule: AutomationRule, stats: dict[int, tuple[int, object]]) -> dict:
    count, last = stats.get(rule.id, (0, None))
    return {
        "id": rule.id,
        "name": rule.name,
        "description": rule.description or "",
        "active": rule.active,
        "trigger": rule.trigger,
        "conditions": rule.conditions or {"match": "all", "rules": []},
        "actions": list(rule.actions or []),
        "order": rule.order,
        "stop_processing": rule.stop_processing,
        "created_at": rule.created_at,
        "updated_at": rule.updated_at,
        "last_execution_at": last,
        "execution_count": count,
    }


def _check_refs(session: Session, actions: list[ActionIn]) -> None:
    errors = engine.validate_references(session, actions)
    if errors:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail="; ".join(errors))


@router.get("/meta")
def automation_meta(_: Principal = Depends(admin_only)) -> dict:
    """Catálogo para el constructor de reglas: disparadores, campos, operadores y acciones."""
    return {
        "triggers": [{"key": k, "label": v} for k, v in TRIGGERS.items()],
        "fields": [{"key": k, "label": label, "kind": kind} for k, (label, kind) in FIELDS.items()],
        "operators": [{"key": k, "label": v} for k, v in OPERATORS.items()],
        "actions": [{"key": k, "label": v} for k, v in ACTION_TYPES.items()],
        "notify_targets": [{"key": k, "label": v} for k, v in NOTIFY_TARGETS.items()],
        "max_depth": engine.MAX_DEPTH,
        "template_help": "Use {{ticket.key}}, {{ticket.title}}, {{ticket.priority_name}}, "
        "{{ticket.status_name}}, {{ticket.department}}, {{ticket.assignee}} o {{event.<campo>}}.",
    }


@router.get("/rules", response_model=list[RuleOut])
def list_rules(
    _: Principal = Depends(admin_only), session: Session = Depends(get_db)
) -> list[dict]:
    rules = session.scalars(
        select(AutomationRule).order_by(AutomationRule.order, AutomationRule.id)
    ).all()
    stats = _stats(session, [r.id for r in rules])
    return [_rule_out(r, stats) for r in rules]


@router.post("/rules", response_model=RuleOut, status_code=status.HTTP_201_CREATED)
def create_rule(
    payload: RuleIn,
    principal: Principal = Depends(admin_only),
    session: Session = Depends(get_db),
) -> dict:
    _check_refs(session, payload.actions)
    now = utcnow()
    rule = AutomationRule(
        name=payload.name,
        description=payload.description.strip(),
        active=payload.active,
        trigger=payload.trigger,
        conditions=payload.conditions.model_dump(),
        actions=[a.compact() for a in payload.actions],
        order=payload.order,
        stop_processing=payload.stop_processing,
        created_by_id=principal.user_id,
        created_at=now,
        updated_at=now,
    )
    session.add(rule)
    session.commit()
    return _rule_out(rule, {})


@router.get("/rules/{rule_id}", response_model=RuleOut)
def get_rule(
    rule_id: int, _: Principal = Depends(admin_only), session: Session = Depends(get_db)
) -> dict:
    rule = session.get(AutomationRule, rule_id)
    if rule is None:
        raise _rule_not_found()
    return _rule_out(rule, _stats(session, [rule.id]))


@router.patch("/rules/{rule_id}", response_model=RuleOut)
def update_rule(
    rule_id: int,
    payload: RulePatch,
    _: Principal = Depends(admin_only),
    session: Session = Depends(get_db),
) -> dict:
    rule = session.get(AutomationRule, rule_id)
    if rule is None:
        raise _rule_not_found()
    changes = payload.model_dump(exclude_unset=True)
    if payload.actions is not None:
        _check_refs(session, payload.actions)
        rule.actions = [a.compact() for a in payload.actions]
    if payload.conditions is not None:
        rule.conditions = payload.conditions.model_dump()
    for field in ("active", "trigger", "order", "stop_processing"):
        if changes.get(field) is not None:
            setattr(rule, field, changes[field])
    if changes.get("name") is not None:
        name = changes["name"].strip()
        if not name:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail="El nombre es obligatorio")
        rule.name = name
    if changes.get("description") is not None:
        rule.description = changes["description"].strip()
    rule.updated_at = utcnow()
    session.commit()
    return _rule_out(rule, _stats(session, [rule.id]))


@router.delete("/rules/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_rule(
    rule_id: int, _: Principal = Depends(admin_only), session: Session = Depends(get_db)
) -> Response:
    rule = session.get(AutomationRule, rule_id)
    if rule is None:
        raise _rule_not_found()
    session.delete(rule)
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _load_ticket(session: Session, ticket_id: int) -> Ticket:
    ticket = session.get(Ticket, ticket_id)
    if ticket is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Ticket no encontrado")
    return ticket


@router.post("/rules/{rule_id}/dry-run")
def dry_run_rule(
    rule_id: int,
    payload: DryRunIn,
    _: Principal = Depends(admin_only),
    session: Session = Depends(get_db),
) -> dict:
    """Evalúa una regla guardada contra un ticket sin ejecutar acciones."""
    rule = session.get(AutomationRule, rule_id)
    if rule is None:
        raise _rule_not_found()
    ticket = _load_ticket(session, payload.ticket_id)
    result = engine.dry_run(
        session,
        rule.conditions or {},
        list(rule.actions or []),
        ticket,
        payload.event_type or rule.trigger,
        payload.event_data,
    )
    session.rollback()
    return result


@router.post("/dry-run")
def dry_run_draft(
    payload: DryRunIn,
    _: Principal = Depends(admin_only),
    session: Session = Depends(get_db),
) -> dict:
    """Evalúa una regla aún no guardada (``rule``) contra un ticket."""
    if payload.rule is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Falta la regla a simular")
    ticket = _load_ticket(session, payload.ticket_id)
    result = engine.dry_run(
        session,
        payload.rule.conditions.model_dump(),
        [a.compact() for a in payload.rule.actions],
        ticket,
        payload.event_type or payload.rule.trigger,
        payload.event_data,
    )
    session.rollback()
    return result


@router.get("/executions", response_model=ExecutionPage)
def list_executions(
    rule_id: Optional[int] = None,
    ticket_id: Optional[int] = None,
    status_filter: Optional[str] = Query(default=None, alias="status", pattern="^(success|partial|error)$"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=200),
    _: Principal = Depends(admin_only),
    session: Session = Depends(get_db),
) -> dict:
    conditions = []
    if rule_id is not None:
        conditions.append(AutomationExecution.rule_id == rule_id)
    if ticket_id is not None:
        conditions.append(AutomationExecution.ticket_id == ticket_id)
    if status_filter:
        conditions.append(AutomationExecution.status == status_filter)
    count_stmt = select(func.count()).select_from(AutomationExecution)
    stmt = (
        select(AutomationExecution)
        .options(joinedload(AutomationExecution.rule), joinedload(AutomationExecution.ticket))
        .order_by(AutomationExecution.created_at.desc(), AutomationExecution.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    if conditions:
        count_stmt = count_stmt.where(*conditions)
        stmt = stmt.where(*conditions)
    total = session.scalar(count_stmt) or 0
    items = [
        {
            "id": e.id,
            "rule": {"id": e.rule.id, "name": e.rule.name},
            "ticket": {"id": e.ticket.id, "key": e.ticket.key, "title": e.ticket.title}
            if e.ticket
            else None,
            "event_type": e.event_type,
            "depth": e.depth,
            "status": e.status,
            "result": list(e.result or []),
            "error": e.error,
            "created_at": e.created_at,
        }
        for e in session.scalars(stmt).unique().all()
    ]
    return {"items": items, "total": total, "page": page, "page_size": page_size}
