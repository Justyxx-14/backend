from dataclasses import dataclass
from typing import Optional
from uuid import UUID
from datetime import date

@dataclass
class PlayerInDTO:
    name: str
    birthday: date

@dataclass
class PlayerOutDTO:
    id: UUID
    name: str
    birthday: date
    game_id: Optional[UUID] = None
    social_disgrace: bool = False
