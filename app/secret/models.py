from uuid import UUID
from sqlalchemy import String, ForeignKey, Enum
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates
from sqlalchemy.types import Uuid
from .enums import SecretType

from app.db import Base

class Secrets(Base):
    __tablename__ = "secrets"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    game_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("games.id"), nullable=False, index=True)
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    owner_player_id: Mapped[UUID | None] = mapped_column(Uuid, ForeignKey("players.id"), nullable=True, index=True)
    revealed: Mapped[bool] = mapped_column(nullable=False, default=False)
    role: Mapped[SecretType] = mapped_column(Enum(SecretType), nullable=False, index=True)
    description: Mapped[str] = mapped_column(String(255), nullable=False)  
    

    players = relationship(
        "Player", 
        back_populates="secrets",
        foreign_keys=[owner_player_id]) 
    
    game = relationship(
        "Game", 
        back_populates="secrets"
    )
    
    @validates('role')
    def validate_role(self, key, value):
            if not isinstance(value, SecretType):
                raise ValueError(f"Invalid role: {value}")
            return value