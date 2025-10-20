from typing import List, Optional
from uuid import UUID
from pydantic import BaseModel, Field
from .enums import SetType
from app.game.schemas import EndGameResult

class SetIn (BaseModel):
    player_id: UUID
    game_id: UUID
    cards: List[UUID]

class SetOut (BaseModel):
    id: UUID
    type: SetType
    game_id: UUID
    owner_player_id: UUID

class Set_target (BaseModel):
    id: UUID 
    type: SetType
    secret_id: UUID
    # owner_player_id: UUID

class SetPlayIn(BaseModel):
    player_id: UUID
    cards: List[UUID]
    target_player_id: UUID
    # Solo se usa para sets tipo MM y HP
    chosen_secret_id: UUID 

class SetPlayResult(BaseModel):
    set_out: SetOut
    end_game_result: EndGameResult | None = None