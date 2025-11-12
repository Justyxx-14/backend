import uuid
from sqlalchemy import String, Integer, Boolean, UUID, ForeignKey, Enum 
from sqlalchemy.orm import relationship, validates, object_session, Mapped, mapped_column
from sqlalchemy.types import UUID, JSON

from app.db import Base
from app.player.models import Player
from app.game.enums import TurnState

class Game(Base):
    __tablename__ = "games"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String, nullable=False)
    password: Mapped[str | None] = mapped_column(String, nullable = True) # Not secure but good enough
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

    turn_state: Mapped["GameTurnState | None"] = relationship(
        back_populates="game",
        cascade="all, delete-orphan",
        uselist=False,
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

class GameTurnState(Base):
    __tablename__ = "game_turn_states"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    game_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), 
        ForeignKey("games.id", ondelete="CASCADE"), 
        nullable=False,
        unique=True
    )
    state: Mapped["TurnState"] = mapped_column(Enum(TurnState), nullable=False)
    target_player_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("players.id", ondelete="SET NULL"),
        nullable=True
    )
    
    passing_direction: Mapped[str | None] = mapped_column(String(10), nullable=True)
    
    current_event_card_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), 
        ForeignKey("cards.id", ondelete="SET NULL"),
        nullable=True
    )
    card_trade_offered_card_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cards.id", ondelete="SET NULL"),
        nullable=True
    )
    is_canceled_card:Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    last_is_canceled_card:Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    vote_data: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    game: Mapped["Game"] = relationship(back_populates="turn_state")
    target_player: Mapped["Player"] = relationship("Player")
    sfp_players: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
