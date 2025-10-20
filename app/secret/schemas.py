from uuid import UUID
from typing import List
from pydantic import BaseModel, Field, model_validator
from app.secret.enums import SecretType


class SecretOut(BaseModel):
    id: UUID
    game_id: UUID
    type: SecretType
    name: str
    description: str
    owner_player_id: UUID | None  # por si el secreto no está asignado todavía
    revealed: bool

class SecretMove(BaseModel):
    game_id: UUID
    secret_id: UUID
    from_player: UUID
    to_player: UUID

class SecretQuery(BaseModel):
    """Representa los parámetros aceptados por GET /secrets.
    - Siempre se debe indicar `player_id`.
    - Si se agrega `secret_id`, también debe enviarse `player_id`.
    """
    game_id: UUID
    player_id: UUID | None
    secret_id: UUID | None

    @model_validator(mode="after")
    def _validate_query_rules(self):
        if self.player_id is None:
            raise ValueError("player_id is required")
        return self


class SecretReveal(BaseModel):
    game_id: UUID
    secret_id: UUID
