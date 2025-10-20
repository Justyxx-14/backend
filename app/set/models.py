from uuid import UUID, uuid4
from sqlalchemy import ForeignKey, Enum
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import Uuid

from app.db import Base
from .enums import SetType


class Set(Base):
    __tablename__ = "sets"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    game_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("games.id"), nullable=False, index=True)
    type: Mapped[SetType] = mapped_column(Enum(SetType), nullable=False)
    owner_player_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("players.id"), nullable=False, index=True)
