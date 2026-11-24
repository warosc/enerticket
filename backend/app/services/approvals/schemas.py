"""Esquemas Pydantic de aprobaciones (entrada/salida de la API y validación de políticas)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

ApproverType = Literal["user", "role", "team", "team_lead"]
StepRule = Literal["all", "any"]
RoleScope = Literal["department", "global"]
ApproverRole = Literal["approver", "agent", "admin"]


class StepSpec(BaseModel):
    """Definición de una etapa de aprobación dentro de una política."""

    name: str = Field(min_length=1, max_length=150)
    approver_type: ApproverType
    user_id: Optional[int] = None
    role: Optional[ApproverRole] = None
    team_id: Optional[int] = None
    scope: RoleScope = "department"
    rule: StepRule = "any"

    @field_validator("name")
    @classmethod
    def _strip_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("El nombre de la etapa es obligatorio")
        return value

    @model_validator(mode="after")
    def _check_target(self) -> "StepSpec":
        if self.approver_type == "user" and not self.user_id:
            raise ValueError("Una etapa de tipo 'user' requiere user_id")
        if self.approver_type == "role" and not self.role:
            raise ValueError("Una etapa de tipo 'role' requiere role")
        if self.approver_type == "team" and not self.team_id:
            raise ValueError("Una etapa de tipo 'team' requiere team_id")
        return self


class PolicyIn(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    active: bool = True
    transition_id: int
    ticket_type_keys: list[str] = Field(default_factory=list, max_length=20)
    department_ids: list[int] = Field(default_factory=list, max_length=50)
    steps: list[StepSpec] = Field(min_length=1, max_length=10)
    order: int = Field(default=100, ge=0, le=100000)


class PolicyPatch(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=150)
    active: Optional[bool] = None
    transition_id: Optional[int] = None
    ticket_type_keys: Optional[list[str]] = Field(default=None, max_length=20)
    department_ids: Optional[list[int]] = Field(default=None, max_length=50)
    steps: Optional[list[StepSpec]] = Field(default=None, min_length=1, max_length=10)
    order: Optional[int] = Field(default=None, ge=0, le=100000)


class PolicyOut(BaseModel):
    id: int
    name: str
    active: bool
    transition_id: int
    transition: dict
    ticket_type_keys: list[str]
    department_ids: list[int]
    steps: list[dict]
    order: int
    created_at: datetime
    updated_at: datetime


class DecisionIn(BaseModel):
    decision: Literal["approve", "reject", "approved", "rejected"]
    comment: Optional[str] = Field(default=None, max_length=4000)

    @property
    def normalized(self) -> str:
        return "approved" if self.decision in ("approve", "approved") else "rejected"


class CancelIn(BaseModel):
    reason: Optional[str] = Field(default=None, max_length=2000)


class UserRefOut(BaseModel):
    id: int
    display_name: str


class DecisionOut(BaseModel):
    id: int
    approver: UserRefOut
    decision: str
    comment: Optional[str] = None
    is_override: bool
    decided_at: Optional[datetime] = None


class StepOut(BaseModel):
    id: int
    order: int
    name: str
    approver_type: str
    approver_label: str
    rule: str
    status: str
    decided_at: Optional[datetime] = None
    decisions: list[DecisionOut]


class ApprovalRequestOut(BaseModel):
    id: int
    status: str
    ticket: dict
    transition: dict
    policy: Optional[dict] = None
    reason: Optional[str] = None
    requested_by: Optional[UserRefOut] = None
    created_at: datetime
    decided_at: Optional[datetime] = None
    executed_at: Optional[datetime] = None
    execution_error: Optional[str] = None
    current_step: Optional[int] = None
    steps: list[StepOut]
    can_decide: bool = False
    can_cancel: bool = False


class ApprovalPage(BaseModel):
    items: list[ApprovalRequestOut]
    total: int
    page: int
    page_size: int
