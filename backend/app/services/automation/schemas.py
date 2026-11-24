"""Esquemas Pydantic de reglas de automatización (validación estructural)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

TRIGGERS: dict[str, str] = {
    "ticket.created": "Ticket creado",
    "ticket.updated": "Ticket actualizado",
    "ticket.assigned": "Ticket asignado",
    "ticket.transitioned": "Cambio de estado",
    "ticket.commented": "Nuevo comentario",
    "sla.warning": "SLA en riesgo",
    "sla.breached": "SLA incumplido",
    "approval.requested": "Aprobación solicitada",
    "approval.decided": "Aprobación decidida",
    "attachment.added": "Adjunto agregado",
}

Operator = Literal[
    "eq",
    "neq",
    "in",
    "not_in",
    "contains",
    "not_contains",
    "starts_with",
    "gt",
    "gte",
    "lt",
    "lte",
    "is_empty",
    "is_not_empty",
]

OPERATORS: dict[str, str] = {
    "eq": "es igual a",
    "neq": "es distinto de",
    "in": "es uno de",
    "not_in": "no es ninguno de",
    "contains": "contiene",
    "not_contains": "no contiene",
    "starts_with": "comienza con",
    "gt": "es mayor que",
    "gte": "es mayor o igual que",
    "lt": "es menor que",
    "lte": "es menor o igual que",
    "is_empty": "está vacío",
    "is_not_empty": "no está vacío",
}

# campo → (etiqueta, tipo de valor para la UI)
FIELDS: dict[str, tuple[str, str]] = {
    "ticket.priority": ("Prioridad", "priority"),
    "ticket.priority_rank": ("Rango de prioridad (1-4)", "number"),
    "ticket.status": ("Estado", "state"),
    "ticket.status_category": ("Categoría de estado", "category"),
    "ticket.type": ("Tipo de ticket", "ticket_type"),
    "ticket.department_id": ("Departamento", "department"),
    "ticket.team_id": ("Equipo", "team"),
    "ticket.assignee_id": ("Asignado (id de usuario)", "user"),
    "ticket.requester_id": ("Solicitante (id de usuario)", "user"),
    "ticket.title": ("Título", "text"),
    "ticket.description": ("Descripción", "text"),
    "ticket.text": ("Título o descripción", "text"),
    "event.actor_id": ("Actor del evento (id de usuario)", "user"),
    "event.to_state": ("Estado destino (cambio de estado)", "state"),
    "event.from_state": ("Estado origen (cambio de estado)", "state"),
    "event.visibility": ("Visibilidad del comentario", "text"),
    "event.changes.priority.to": ("Nueva prioridad (actualización)", "priority"),
    "event.decision": ("Decisión de aprobación", "text"),
}

ActionType = Literal[
    "assign_user",
    "assign_team",
    "set_priority",
    "transition",
    "add_comment",
    "notify",
    "request_approval",
]

ACTION_TYPES: dict[str, str] = {
    "assign_user": "Asignar a usuario",
    "assign_team": "Asignar a equipo",
    "set_priority": "Cambiar prioridad",
    "transition": "Cambiar estado",
    "add_comment": "Agregar comentario",
    "notify": "Notificar",
    "request_approval": "Solicitar aprobación",
}

NOTIFY_TARGETS: dict[str, str] = {
    "requester": "Solicitante",
    "assignee": "Asignado",
    "team_leads": "Líderes del equipo",
    "team_members": "Miembros del equipo",
    "department_agents": "Agentes del departamento",
    "role:approver": "Todos los aprobadores",
    "role:admin": "Administradores",
}

class ConditionIn(BaseModel):
    field: str = Field(min_length=3, max_length=120)
    op: Operator
    value: Any = None

    @field_validator("field")
    @classmethod
    def _check_field(cls, value: str) -> str:
        value = value.strip()
        if value in FIELDS:
            return value
        if value.startswith("event.") and all(part.isidentifier() for part in value.split(".")):
            return value
        raise ValueError(f"Campo de condición desconocido: {value}")

    @model_validator(mode="after")
    def _check_value(self) -> "ConditionIn":
        if self.op in ("is_empty", "is_not_empty"):
            return self
        if self.value is None or (isinstance(self.value, str) and not self.value.strip()):
            raise ValueError(f"La condición sobre '{self.field}' requiere un valor")
        if isinstance(self.value, str) and len(self.value) > 500:
            raise ValueError("El valor de la condición es demasiado largo")
        if isinstance(self.value, list) and len(self.value) > 50:
            raise ValueError("Demasiados valores en la condición")
        return self


class ConditionsIn(BaseModel):
    match: Literal["all", "any"] = "all"
    rules: list[ConditionIn] = Field(default_factory=list, max_length=20)


class ActionIn(BaseModel):
    type: ActionType
    user_id: Optional[int] = None
    team_id: Optional[int] = None
    priority_key: Optional[str] = Field(default=None, max_length=30)
    to_state: Optional[str] = Field(default=None, max_length=50)
    body: Optional[str] = Field(default=None, max_length=4000)
    visibility: Literal["public", "internal"] = "internal"
    targets: list[str] = Field(default_factory=list, max_length=20)
    title: Optional[str] = Field(default=None, max_length=250)

    @model_validator(mode="after")
    def _check_fields(self) -> "ActionIn":
        required = {
            "assign_user": ("user_id",),
            "assign_team": ("team_id",),
            "set_priority": ("priority_key",),
            "transition": ("to_state",),
            "request_approval": ("to_state",),
            "add_comment": ("body",),
            "notify": ("title",),
        }[self.type]
        for name in required:
            value = getattr(self, name)
            if value is None or (isinstance(value, str) and not value.strip()):
                raise ValueError(f"La acción '{ACTION_TYPES[self.type]}' requiere '{name}'")
        if self.type == "notify":
            if not self.targets:
                raise ValueError("La acción 'Notificar' requiere al menos un destinatario")
            for target in self.targets:
                if target in NOTIFY_TARGETS:
                    continue
                kind, _, rest = target.partition(":")
                if kind == "role" and rest in ("admin", "agent", "approver"):
                    continue
                if kind == "user" and rest.isdigit():
                    continue
                raise ValueError(f"Destinatario desconocido: {target}")
        return self

    def compact(self) -> dict[str, Any]:
        """Representación JSON con solo los campos relevantes para su tipo."""
        keep = {
            "assign_user": ("user_id",),
            "assign_team": ("team_id",),
            "set_priority": ("priority_key",),
            # ``body`` en un cambio de estado = solución (si la transición resuelve el ticket).
            "transition": ("to_state", "body"),
            "request_approval": ("to_state", "body"),
            "add_comment": ("body", "visibility"),
            "notify": ("targets", "title", "body"),
        }[self.type]
        data: dict[str, Any] = {"type": self.type}
        for name in keep:
            value = getattr(self, name)
            if value is not None:
                data[name] = value.strip() if isinstance(value, str) else value
        return data


class RuleIn(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    description: str = Field(default="", max_length=2000)
    active: bool = True
    trigger: str
    conditions: ConditionsIn = Field(default_factory=ConditionsIn)
    actions: list[ActionIn] = Field(min_length=1, max_length=10)
    order: int = Field(default=100, ge=0, le=100000)
    stop_processing: bool = False

    @field_validator("trigger")
    @classmethod
    def _check_trigger(cls, value: str) -> str:
        if value not in TRIGGERS:
            raise ValueError(f"Disparador desconocido: {value}")
        return value

    @field_validator("name")
    @classmethod
    def _strip(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("El nombre es obligatorio")
        return value


class RulePatch(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=150)
    description: Optional[str] = Field(default=None, max_length=2000)
    active: Optional[bool] = None
    trigger: Optional[str] = None
    conditions: Optional[ConditionsIn] = None
    actions: Optional[list[ActionIn]] = Field(default=None, min_length=1, max_length=10)
    order: Optional[int] = Field(default=None, ge=0, le=100000)
    stop_processing: Optional[bool] = None

    @field_validator("trigger")
    @classmethod
    def _check_trigger(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and value not in TRIGGERS:
            raise ValueError(f"Disparador desconocido: {value}")
        return value


class RuleOut(BaseModel):
    id: int
    name: str
    description: str
    active: bool
    trigger: str
    conditions: dict[str, Any]
    actions: list[dict[str, Any]]
    order: int
    stop_processing: bool
    created_at: datetime
    updated_at: datetime
    last_execution_at: Optional[datetime] = None
    execution_count: int = 0


class DryRunIn(BaseModel):
    ticket_id: int
    event_type: Optional[str] = None
    event_data: dict[str, Any] = Field(default_factory=dict)
    rule: Optional[RuleIn] = None


class ExecutionOut(BaseModel):
    id: int
    rule: dict[str, Any]
    ticket: Optional[dict[str, Any]] = None
    event_type: str
    depth: int
    status: str
    result: list[dict[str, Any]]
    error: Optional[str] = None
    created_at: datetime


class ExecutionPage(BaseModel):
    items: list[ExecutionOut]
    total: int
    page: int
    page_size: int
