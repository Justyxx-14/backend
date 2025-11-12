from enum import Enum
from pydantic import BaseModel, Field, model_validator, ConfigDict
from uuid import UUID
from typing import List, Optional, Dict
from datetime import date
from app.game.dtos import GameInDTO, GameOutDTO
from .enums import GameEndReason, WinningTeam, PlayerRole, TurnState

class GameIn(BaseModel):
    name: str = Field(..., min_length=1)
    host: UUID = Field(...)
    password: str | None = Field(default = None)
    birthday: date = Field(...)
    min_players: int = Field(..., ge=2, le=6)
    max_players: int = Field(..., ge=2, le=6)

    def to_dto(self) -> GameInDTO:
        return GameInDTO(
            name=self.name,
            password=self.password,
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
    password: str | None = Field(default=None)
    host_id: UUID = Field(...)
    min_players: int = Field(..., ge=2, le=6)
    max_players: int = Field(..., ge=2, le=6)
    ready: bool = Field(default=False)
    player_ids: List[UUID] = Field(default_factory=list)

    def to_dto(self) -> GameOutDTO:
        return GameOutDTO(
            id=self.id,
            name=self.name,
            password=self.password,
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

class GameTurnStateOut(BaseModel):
    turn_state : TurnState
    target_player_id: UUID | None = None
    current_event_card_id: UUID | None = None
    card_trade_offered_card_id: UUID | None = None
    passing_direction: str | None = None
    is_cancelled: bool | None = None
    last_is_canceled_card: bool | None = None
    vote_data: Dict[str, str] | None = None
    sfp_players: List[str] | None = None

class CurrentTurnResponse(BaseModel):
    current_turn: UUID
    turn_state : TurnState
    remaining_time : float
    timer_is_paused: bool
    target_player_id: UUID | None = None
    current_event_card_id: UUID | None = None
    card_trade_offered_card_id: UUID | None = None
    players_who_selected_card: List[UUID] | None = None,
    passing_direction: str | None = None
    players_who_voted: List[UUID] | None = None,
    sfp_players: List[str] | None = None

class PlayerNeighborInfo(BaseModel):
    id: UUID
    name: str

class NeighborsOut(BaseModel):
    previous_player: PlayerNeighborInfo
    next_player: PlayerNeighborInfo
