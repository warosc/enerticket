"""Esquemas Pydantic de la API core (fundación)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

Visibility = Literal["public", "internal"]
TeamRole = Literal["member", "lead"]


def _strip(value: Any) -> Any:
    return value.strip() if isinstance(value, str) else value


# --------------------------------------------------------------------------- referencias


class IdName(BaseModel):
    id: int
    name: str


class UserRef(BaseModel):
    id: int
    display_name: str


class StatusRef(BaseModel):
    id: int
    key: str
    name: str
    category: str


class PriorityRef(BaseModel):
    id: int
    key: str
    name: str
    rank: int


class TypeRef(BaseModel):
    id: int
    key: str
    name: str


class ProjectRef(BaseModel):
    id: int
    code: str
    name: str


# --------------------------------------------------------------------------- tickets


class TicketOut(BaseModel):
    """Ticket serializado. Admite campos extra añadidos por enriquecedores (p.ej. ``sla``)."""

    model_config = ConfigDict(extra="allow")

    id: int
    key: str
    title: str
    description: str
    status: StatusRef
    priority: PriorityRef
    type: TypeRef
    department: IdName
    team: Optional[IdName] = None
    project: Optional[ProjectRef] = None
    requester: UserRef
    assignee: Optional[UserRef] = None
    created_at: datetime
    updated_at: datetime
    first_response_at: Optional[datetime] = None
    resolved_at: Optional[datetime] = None
    closed_at: Optional[datetime] = None


class TicketPage(BaseModel):
    items: list[TicketOut]
    total: int
    page: int
    page_size: int


class TicketCreate(BaseModel):
    title: str = Field(min_length=3, max_length=200)
    description: str = Field(default="", max_length=20000)
    type_key: str = "incident"
    priority_key: str = "medium"
    department_id: int
    project_id: Optional[int] = None

    _strip_title = field_validator("title", "description", mode="before")(_strip)


class TicketPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: Optional[str] = Field(default=None, min_length=3, max_length=200)
    description: Optional[str] = Field(default=None, max_length=20000)
    priority_key: Optional[str] = None
    assignee_id: Optional[int] = None
    team_id: Optional[int] = None
    department_id: Optional[int] = None

    _strip_title = field_validator("title", "description", mode="before")(_strip)


class TransitionIn(BaseModel):
    to_state_key: str = Field(min_length=1, max_length=50)
    comment: Optional[str] = Field(default=None, max_length=20000)


class CommentIn(BaseModel):
    body: str = Field(min_length=1, max_length=20000)
    visibility: Visibility = "public"

    _strip_body = field_validator("body", mode="before")(_strip)


class CommentOut(BaseModel):
    id: int
    ticket_id: int
    author: UserRef
    body: str
    visibility: Visibility
    created_at: datetime


class HistoryOut(BaseModel):
    id: int
    ticket_id: int
    event_type: str
    actor: Optional[UserRef] = None
    before: Optional[Any] = None
    after: Optional[Any] = None
    timestamp: datetime


class StatsOut(BaseModel):
    total: int
    by_status: dict[str, int]
    by_priority: dict[str, int]
    by_category: dict[str, int]


# --------------------------------------------------------------------------- identidad / catálogo


class TeamRef(BaseModel):
    id: int
    name: str
    department_id: int


class MeOut(BaseModel):
    id: int
    sub: str
    username: str
    display_name: str
    email: Optional[str] = None
    roles: list[str]
    teams: list[TeamRef]
    department_ids: list[int]


class UserOut(BaseModel):
    id: int
    username: str
    display_name: str
    email: Optional[str] = None
    roles: list[str]
    active: bool


class DepartmentOut(BaseModel):
    id: int
    name: str
    parent_id: Optional[int] = None


class TeamOut(BaseModel):
    id: int
    department_id: int
    name: str
    external_group: Optional[str] = None


class PriorityOut(PriorityRef):
    pass


class TicketTypeOut(TypeRef):
    pass


class ProjectOut(BaseModel):
    id: int
    code: str
    name: str
    owner_id: Optional[int] = None
    team_id: Optional[int] = None


class WorkflowStateOut(BaseModel):
    id: int
    key: str
    name: str
    category: str
    order: int
    is_initial: bool
    is_final: bool


class WorkflowTransitionOut(BaseModel):
    id: int
    from_state_id: int
    to_state_id: int
    from_key: str
    to_key: str
    name: str
    requires_approval: bool


class WorkflowOut(BaseModel):
    id: Optional[int] = None
    key: Optional[str] = None
    name: Optional[str] = None
    states: list[WorkflowStateOut]
    transitions: list[WorkflowTransitionOut]


class CatalogOut(BaseModel):
    departments: list[DepartmentOut]
    teams: list[TeamOut]
    priorities: list[PriorityOut]
    ticket_types: list[TicketTypeOut]
    projects: list[ProjectOut]
    workflow: WorkflowOut


# --------------------------------------------------------------------------- administración


class DepartmentIn(BaseModel):
    name: str = Field(min_length=2, max_length=150)
    parent_id: Optional[int] = None

    _strip_name = field_validator("name", mode="before")(_strip)


class DepartmentPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Optional[str] = Field(default=None, min_length=2, max_length=150)
    parent_id: Optional[int] = None

    _strip_name = field_validator("name", mode="before")(_strip)


class TeamIn(BaseModel):
    department_id: int
    name: str = Field(min_length=2, max_length=150)
    external_group: Optional[str] = Field(default=None, max_length=255)

    _strip_fields = field_validator("name", "external_group", mode="before")(_strip)


class TeamPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    department_id: Optional[int] = None
    name: Optional[str] = Field(default=None, min_length=2, max_length=150)
    external_group: Optional[str] = Field(default=None, max_length=255)

    _strip_fields = field_validator("name", "external_group", mode="before")(_strip)


class TeamMemberIn(BaseModel):
    user_id: int
    role: TeamRole = "member"


class TeamMemberPatch(BaseModel):
    role: TeamRole


class TeamMemberOut(BaseModel):
    team_id: int
    user_id: int
    role: TeamRole
    username: str
    display_name: str
    email: Optional[str] = None


class ProjectIn(BaseModel):
    code: str = Field(min_length=2, max_length=30, pattern=r"^[A-Za-z0-9_-]+$")
    name: str = Field(min_length=2, max_length=200)
    owner_id: Optional[int] = None
    team_id: Optional[int] = None

    _strip_fields = field_validator("code", "name", mode="before")(_strip)


class ProjectPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: Optional[str] = Field(default=None, min_length=2, max_length=30, pattern=r"^[A-Za-z0-9_-]+$")
    name: Optional[str] = Field(default=None, min_length=2, max_length=200)
    owner_id: Optional[int] = None
    team_id: Optional[int] = None

    _strip_fields = field_validator("code", "name", mode="before")(_strip)
