from dataclasses import dataclass
from typing import Optional
from uuid import UUID
from app.secret.enums import SecretType

@dataclass
class SecretInDTO:
    name: str
    role: SecretType
    description: str
    revealed: bool = False

@dataclass
class SecretOutDTO:
    id: UUID
    name: str
    role: SecretType
    description: str
    owner_player_id: Optional[UUID] = None
    revealed: bool = False
    game_id: UUID | None = None