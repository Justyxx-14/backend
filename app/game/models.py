import uuid
from sqlalchemy import Column, String, Integer, Boolean, UUID, ForeignKey
from sqlalchemy.orm import relationship, validates, object_session, Mapped, mapped_column
from sqlalchemy.types import UUID

from app.db import Base
from app.player.models import Player

# import app.card.models

class Game(Base):
    __tablename__ = "games"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String, nullable=False)
    host_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("players.id"), nullable=False)
    min_players: Mapped[int] = mapped_column(Integer, nullable=False)
    max_players: Mapped[int] = mapped_column(Integer, nullable=False)
    ready: Mapped[bool] = mapped_column(Boolean, default=False)
    current_turn: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("players.id"), nullable = True)

    # Relación con Player
    players = relationship(
        "Player", 
        back_populates="game",
        cascade="all, delete-orphan",
        foreign_keys="Player.game_id")
    
    cards = relationship(
        "Card",
        back_populates="game",
        cascade="all, delete-orphan"
    )
    
    secrets = relationship(
        "Secrets",
        back_populates="game",
        cascade="all, delete-orphan"
    )
    
    @validates('min_players', 'max_players')
    def validate_players(self, key, value):
        if key == 'min_players' and value < 0:
            raise ValueError("min_players cannot be negative")
        if key == 'max_players' and value < self.min_players:
            raise ValueError("max_players cannot be less than min_players")
        return value
    
    @validates("host_id")
    def validate_host(self, key, value):
        """
        Valida que el host_id pertenezca a esta misma partida.
        """
        session = object_session(self)
        if session is None:
            # Si el objeto aún no está asociado a una sesión, no podemos validar
            return value  

        # Buscar el jugador en la DB
        player = session.get(Player, value)
        if not player:
            raise ValueError("El host_id no corresponde a un jugador válido")

        # Chequear que pertenezca a esta partida
        if player.game_id is None or str(player.game_id) != str(self.id):
            raise ValueError("El host debe pertenecer a la misma partida")

        return value
