from uuid import UUID
from typing import Optional, List
from pydantic import BaseModel, Field, model_validator, ConfigDict

from .enums import SetType

class SetPlayIn(BaseModel):
    player_id: UUID
    cards: List[UUID]
    secret_id: Optional[UUID | None] = None
    target_player_id: UUID

class SetElectionPlayer(BaseModel):
    set_id: UUID
    player_id: UUID
    secret_id: UUID

class SetOut(BaseModel):
    id: UUID
    type: SetType
    owner_player_id: UUID
