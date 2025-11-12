from uuid import UUID
from typing import Optional, List
from pydantic import BaseModel, Field, model_validator, ConfigDict
from .enums import CardType, CardOwner

# INPUTS 

class CardIn(BaseModel):
    """
    Schema para crear/sembrar cartas dentro de una partida.
    El game_id viene por ruta (/games/{game_id}/cards).
    """
    type: CardType
    name: str = Field(..., min_length=1, max_length=80)
    description: str = Field(..., min_length=1, max_length=255)


class CardBatchIn(BaseModel):
    """Creación de cartas por lote."""
    items: List[CardIn] = Field(default_factory=list)


# OUTPUTS 

class CardResponse(BaseModel):
    """Respuesta de creación (id generado en el servidor)."""
    id: UUID

class CardOut(BaseModel):
    """
    Representación de una carta en el juego.
    """
    model_config = ConfigDict(from_attributes=True)  # permite parsear desde ORM

    id: UUID
    game_id: UUID
    type: CardType
    name: str
    description: str
    owner: CardOwner  # DECK, DISCARD_PILE, PLAYER
    owner_player_id: Optional[UUID] = None


# MOVIMIENTOS 

class CardMoveIn(BaseModel):
    """
    Información para mover una carta a otro dueño.
    """
    to_owner: CardOwner
    player_id: UUID | None = None

    @model_validator(mode="after")
    def _check_player_if_owner(self):
        if self.to_owner == CardOwner.PLAYER and self.player_id is None:
            raise ValueError("player_id is required when moving to PLAYER")
        if self.to_owner in (CardOwner.DECK, CardOwner.DISCARD_PILE) and self.player_id is not None:
            raise ValueError("player_id must be null when moving to DECK or DISCARD_PILE")
        return self

class CardMoveOut(BaseModel):
    """
    Información de movimiento de una carta a otro dueño.
    """
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    to_owner: CardOwner
    player_id: Optional[UUID] = None

    @model_validator(mode="after")
    def _check_rules(self):
        if self.to_owner == CardOwner.PLAYER and self.player_id is None:
            raise ValueError("player_id required when to_owner=PLAYER")
        if self.to_owner != CardOwner.PLAYER and self.player_id is not None:
            raise ValueError("player_id must be None unless to_owner=PLAYER")
        return self

# ====== NUEVOS DTOs PARA /cards ======

class CardQueryIn(BaseModel):
    """
    Body para GET /cards.
    - Siempre requiere game_id.
    - Si llega card_id, se busca esa carta y no se exige owner/player_id.
    - Si NO llega card_id y owner=PLAYER, se exige player_id.
    """
    game_id: UUID
    owner: CardOwner | None = None
    player_id: UUID | None = None
    card_id: UUID | None = None

    @model_validator(mode="after")
    def _rules(self):
        if self.card_id is not None:
            return self
        if self.owner == CardOwner.PLAYER and self.player_id is None:
            raise ValueError("player_id is required when owner=PLAYER and card_id is not provided")
        return self

class CardMoveCmd(BaseModel):
    """
    Body para PUT /cards (mover una carta).
    Incluye el game_id para validar pertenencia antes de mover.
    """
    game_id: UUID
    card_id: UUID
    to_owner: CardOwner
    player_id: UUID | None = None

    @model_validator(mode="after")
    def _check_player_if_owner(self):
        if self.to_owner == CardOwner.PLAYER and self.player_id is None:
            raise ValueError("player_id is required when moving to PLAYER")
        if self.to_owner in (CardOwner.DECK, CardOwner.DISCARD_PILE) and self.player_id is not None:
            raise ValueError("player_id must be null when moving to DECK or DISCARD_PILE")
        return self

class DrawCardsIn(BaseModel):
    """
    Body para POST /cards/draw.
    Representa la acción de robar N cartas del mazo y dárselas a un jugador.
    """
    player_id: UUID
    n_cards: int

    @model_validator(mode="after")
    def _check_n_cards_range(self):
        if not (1 <= self.n_cards <= 6):
            raise ValueError("La cantidad de cartas a robar debe estar entre 1 y 6.")
        return self

class DiscardCardsIn(BaseModel):
    """
    Body para POST /cards/discard.
    Representa la acción de descartar una o varias cartas de un jugador.
    """
    player_id: UUID
    id_cards: list[UUID]

    @model_validator(mode="after")
    def _check_has_cards(self):
        if not self.id_cards:
            raise ValueError("Debe indicar al menos una carta para descartar")
        return self

class DraftCardIn(BaseModel):
    player_id: UUID
    card_id: UUID
    
# EVENTOS

class PlayEventBase(BaseModel):
    player_id: UUID
    event_id: UUID
    card_id: UUID | None = None
    target_player: UUID | None = None
    secret_id: UUID | None = None
    set_id: UUID | None = None
    requested_card_code: str | None = None
    target_card_id: UUID | None = None
    offered_card_id: UUID | None = None
    direction: str | None = None

class SelectPassingCardIn(BaseModel):
    """
    Body para PUT /cards/select_for_passing.
    Representa la carta que un jugador elige pasar.
    """
    player_id: UUID
    card_id: UUID


class CardTradeSelectionIn(BaseModel):
    """
    Body para PUT /cards/play/E_CT/{game_id}/selection.
    Representa la carta elegida por el jugador objetivo para el intercambio.
    """
    player_id: UUID
    target_card_id: UUID
    event_card_id: UUID


class CardTradeResolutionOut(BaseModel):
    offered_card: CardOut
    received_card: CardOut

class CardNoSoFastPlay(BaseModel):
    player_id: UUID
    card_id: UUID

class VoteIn(BaseModel):
    """
    Body para PUT /cards/vote.
    Representa el voto de un jugador hacia otro.
    """
    player_id: UUID
    target_player_id: UUID 
