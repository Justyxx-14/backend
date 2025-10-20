from typing import List
from datetime import date
from uuid import UUID
from pydantic import BaseModel, Field


class GameInDTO(BaseModel):
    name: str
    host_name: str
    birthday: date 
    min_players: int = Field(..., ge=2, le=6)
    max_players: int = Field(..., ge=2, le=6)

class GameOutDTO(BaseModel):
    id: UUID
    name: str 
    host_id: UUID
    min_players: int = Field(..., ge=2, le=6)
    max_players: int = Field(..., ge=2, le=6)
    ready: bool
    players_ids: List[UUID]
