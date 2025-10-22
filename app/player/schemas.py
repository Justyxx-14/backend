from uuid import UUID
from datetime import date
from pydantic import BaseModel, Field
from app.player.dtos import PlayerInDTO, PlayerOutDTO

class PlayerIn(BaseModel):
    name: str = Field(..., min_length=1)
    birthday: date = Field(...)

    def to_dto(self) -> PlayerInDTO:
        return PlayerInDTO(
            name=self.name,
            birthday=self.birthday
        )

class PlayerOut(BaseModel):
    id: UUID = Field(...)
    name: str = Field(..., min_length=1, max_length=40)
    birthday: date = Field(...)
    game_id: UUID | None = Field(None)
    social_disgrace: bool = Field(False)

    def to_dto(self) -> PlayerOutDTO:
        return PlayerOutDTO(
            id=self.id,
            name=self.name,
            birthday=self.birthday,
            game_id=self.game_id,
            social_disgrace=self.social_disgrace
        )
    
class PlayerResponse(BaseModel):
    id: UUID = Field(...)
