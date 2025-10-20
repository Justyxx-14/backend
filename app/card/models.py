from uuid import UUID, uuid4
from sqlalchemy import String, Enum, ForeignKey, Integer
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import Uuid

from app.db import Base
from .enums import CardType, CardOwner
from app.game.models import Game  # noqa: F401  # ensure mapper is registered before Card

class Card(Base):
    __tablename__ = "cards"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    game_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("games.id"), nullable=False, index=True)
    type: Mapped[CardType] = mapped_column(Enum(CardType), nullable=False)
    name: Mapped[str] = mapped_column(String(80), nullable=False) # Verificar que puede tener hasta 80 caracteres
    description: Mapped[str] = mapped_column(String(255), nullable=False) # Verificar lo mismo para 255
    owner: Mapped[CardOwner] = mapped_column(Enum(CardOwner), nullable=False, index=True)
    owner_player_id: Mapped[UUID | None] = mapped_column(Uuid, ForeignKey("players.id"), index=True, nullable=True)
    order: Mapped[int | None] = mapped_column(Integer, nullable=True)
    
    game = relationship(
        "Game", 
        back_populates="cards"
    )
