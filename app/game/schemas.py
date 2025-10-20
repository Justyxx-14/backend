from enum import Enum
from pydantic import BaseModel, Field, model_validator, ConfigDict
from uuid import UUID
from typing import List, Optional
from datetime import date
from app.game.dtos import GameInDTO, GameOutDTO
from .enums import GameEndReason, WinningTeam, PlayerRole

class GameIn(BaseModel):
    name: str = Field(..., min_length=1)
    host: UUID = Field(...)
    birthday: date = Field(...)
    min_players: int = Field(..., ge=2, le=6)
    max_players: int = Field(..., ge=2, le=6)

    def to_dto(self) -> GameInDTO:
        return GameInDTO(
            name=self.name,
            host=self.host,
            birthday=self.birthday,
            min_players=self.min_players,
            max_players=self.max_players
        )

    @model_validator(mode="after")
    def validate_game(self):
        if self.max_players < self.min_players:
            raise ValueError("max_players debe ser mayor o igual que min_players")
        return self
    
class GameOut(BaseModel):
    id: UUID = Field(...)
    name: str = Field(..., min_length=1)
    host_id: UUID = Field(...)
    min_players: int = Field(..., ge=2, le=6)
    max_players: int = Field(..., ge=2, le=6)
    ready: bool = Field(default=False)
    player_ids: List[UUID] = Field(default_factory=list)

    def to_dto(self) -> GameOutDTO:
        return GameOutDTO(
            id=self.id,
            name=self.name,
            host_id=self.host_id,
            min_players=self.min_players,
            max_players=self.max_players,
            ready=self.ready,
            players_ids=self.player_ids
        )

    @model_validator(mode="after")
    def validate_game(self):
        # max_players >= min_players
        if self.max_players < self.min_players:
            raise ValueError("max_players debe ser mayor o igual que min_players")
        # host siempre incluido en player_ids
        if self.host_id not in self.player_ids:
            self.player_ids.insert(0, self.host_id)

        if self.host_id not in self.player_ids:
            raise ValueError("host_id debe estar incluido en player_ids")
    
        return self
    

class PlayerSummary(BaseModel):
    id: UUID = Field(...)
    name: str = Field(...)

class PlayerRoleInfo(PlayerSummary):
    role: PlayerRole

class EndGameResult(BaseModel):
    reason: GameEndReason
    winning_team: WinningTeam
    winners: List[PlayerSummary]
    player_roles: List[PlayerRoleInfo]

    model_config = ConfigDict(from_attributes=True)