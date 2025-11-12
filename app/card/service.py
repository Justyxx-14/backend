from uuid import UUID, uuid4
from sqlalchemy import desc, func
from sqlalchemy.orm import Session, joinedload
from sqlalchemy.orm.attributes import flag_modified
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from typing import List, Tuple, Dict, Any
from fastapi import HTTPException
from collections import Counter
from app.game.models import Game
from app.game.enums import TurnState
import random
from datetime import datetime, timedelta
import asyncio

from . import models, schemas
from .enums import CardOwner
from .exceptions import (
    CardNotFoundException,
    GameNotFoundException,
    DatabaseCommitException,
    CardsNotFoundOrInvalidException,
    PlayerHandLimitExceededException,
    NoCardsException,
    SecretNotFoundOrInvalidException,
    InvalidAmountOfCards,
)
from app.set.exceptions import SetNotFound
from app.secret.service import SecretService
from app.secret.models import Secrets
from app.game.enums import TurnState
from app.game.models import Game
from app.player.service import PlayerService
from app.secret.schemas import SecretOut

class CardService:

    @staticmethod
    def create_card(db: Session, game_id: UUID, card_in: schemas.CardIn) -> models.Card:
        """Crea una carta asociada a un juego."""
        card = models.Card(
            id=uuid4(),
            game_id=game_id,
            type=card_in.type,
            name=card_in.name,
            description=card_in.description,
            owner=CardOwner.DECK,
            owner_player_id=None,
        )
        db.add(card)

        try:
            db.commit()
        except IntegrityError as e:
            db.rollback()
            # Si la DB falló por FK (game inexistente), devolvemos 404 más claro
            raise GameNotFoundException(game_id) from e
        except SQLAlchemyError as e:
            db.rollback()
            raise DatabaseCommitException() from e

        db.refresh(card)
        return card


    @staticmethod
    def create_cards_batch(db: Session, game_id: UUID, batch_in: schemas.CardBatchIn) -> list[models.Card]:
        """Crea un lote de cartas para un juego."""

        cards = []
        for i, item in enumerate(batch_in.items):
            cards.append(
                models.Card(
                    id=uuid4(),
                    game_id=game_id,
                    type=item.type,
                    name=item.name,
                    description=item.description,
                    owner=CardOwner.DECK,   
                    owner_player_id=None,
                    order=i
                )
            )
        db.add_all(cards)

        try:
            db.commit()
        except IntegrityError as e:
            db.rollback()
            raise GameNotFoundException(game_id) from e
        except SQLAlchemyError as e:
            db.rollback()
            raise DatabaseCommitException() from e

        for card in cards:
            db.refresh(card)
        return cards

    @staticmethod
    def get_card_by_id(db: Session, card_id: UUID) -> models.Card | None:
        """Obtiene una carta por su ID."""
        return db.query(models.Card).filter(models.Card.id == card_id).first()


    @staticmethod
    def get_cards_by_game(db: Session, game_id: UUID) -> list[models.Card]:
        """Obtiene todas las cartas de un juego."""
        return db.query(models.Card).filter(models.Card.game_id == game_id).all()


    @staticmethod
    def get_cards_by_owner(db: Session, game_id: UUID,
                           owner: CardOwner,
                           player_id: UUID | None = None
                           ) -> list[models.Card]:
        """
        Obtiene todas las cartas de un owner.
        Si owner es PLAYER, se filtra por player_id (si es None, devuelve todas las del PLAYER).
        Si owner no es PLAYER, se ignora player_id y 
        devuelve las de DECK o DISCARD_PILE según corresponda.
        """
        q = db.query(models.Card).filter(
            models.Card.game_id == game_id,
            models.Card.owner == owner
        )
        if owner == CardOwner.PLAYER and player_id is not None:
            q = q.filter(models.Card.owner_player_id == player_id)
        return q.all()


    @staticmethod
    def move_card(db: Session, card_id: UUID, move_in: schemas.CardMoveIn) -> models.Card:
        card = CardService.get_card_by_id(db, card_id)
        if not card:
            raise CardNotFoundException(card_id)

        # aplicar el movimiento
        if move_in.to_owner == CardOwner.PLAYER:
            card.owner = CardOwner.PLAYER
            card.owner_player_id = move_in.player_id
        elif move_in.to_owner == CardOwner.DECK:
            card.owner = CardOwner.DECK
            card.owner_player_id = None
            # max order actual de la pila
            max_order = (
                db.query(func.max(models.Card.order))
                .filter(
                    models.Card.game_id == card.game_id,
                    models.Card.owner == CardOwner.DECK
                )
                .scalar()
            )
            card.order = (max_order or 0) + 1
        elif move_in.to_owner == CardOwner.DISCARD_PILE:
            card.owner = CardOwner.DISCARD_PILE
            card.owner_player_id = None

            # max order actual de la pila de descarte
            max_order = (
                db.query(func.max(models.Card.order))
                .filter(
                    models.Card.game_id == card.game_id,
                    models.Card.owner == CardOwner.DISCARD_PILE
                )
                .scalar()
            )
            card.order = (max_order or 0) + 1
        elif move_in.to_owner == CardOwner.DRAFT:
            card.owner = CardOwner.DRAFT
            card.owner_player_id = None
        elif move_in.to_owner == CardOwner.OUT_OFF_THE_GAME:
            card.owner = CardOwner.OUT_OFF_THE_GAME
            card.owner_player_id = None

        # commit con manejo de error
        try:
            db.commit()
        except SQLAlchemyError as e:
            db.rollback()
            raise DatabaseCommitException from e

        db.refresh(card)
        return card

    @staticmethod
    def query_cards(db: Session, q: schemas.CardQueryIn) -> list[models.Card]:
        """
        Implementa la consulta de GET /cards con body.
        """
        if q.owner is None:
            return CardService.get_cards_by_game(db, q.game_id)

        if q.owner == CardOwner.PLAYER:
            return CardService.get_cards_by_owner(db, q.game_id, q.owner, q.player_id)

        # DECK / DISCARD_PILE ignoran player_id
        return CardService.get_cards_by_owner(db, q.game_id, q.owner, None)
    
    @staticmethod
    def deal_cards(db: Session, game_id: UUID, jugadores_ids: list[UUID], cartas_por_jugador: int = 6) -> dict[UUID, list[models.Card]]:
        resultado: dict[UUID, list[models.Card]] = {pid: [] for pid in jugadores_ids}

        
        # Traer todas las cartas del mazo
        deck_cards = db.query(models.Card).filter(
            models.Card.game_id == game_id,
            models.Card.owner == CardOwner.DECK
        ).order_by(models.Card.order).all()

        not_so_fast_cards = [c for c in deck_cards if c.name == "E_NSF"]

        for pid in jugadores_ids:
            if not not_so_fast_cards:
                break  # Si se acaban las cartas
            carta = not_so_fast_cards.pop(0)
            move_in = schemas.CardMoveIn(to_owner=CardOwner.PLAYER, player_id=pid)
            CardService.move_card(db, carta.id, move_in)
            resultado[pid].append(carta)

        remaining_cards = db.query(models.Card).filter(
            models.Card.game_id == game_id,
            models.Card.owner == CardOwner.DECK
        ).order_by(models.Card.order).all()

        for pid in jugadores_ids:
            while len(resultado[pid]) < cartas_por_jugador and remaining_cards:
                carta = remaining_cards.pop(0)
                move_in = schemas.CardMoveIn(to_owner=CardOwner.PLAYER, player_id=pid)
                CardService.move_card(db, carta.id, move_in)
                resultado[pid].append(carta)

        return resultado
    
    @staticmethod
    def shuffle_deck(db: Session, game_id: UUID):
        deck_cards = db.query(models.Card).filter(
            models.Card.game_id == game_id,
            models.Card.owner == CardOwner.DECK
        ).all()

        random.shuffle(deck_cards)
        for i, card in enumerate(deck_cards, start=1):
            card.order = i

        db.commit()

    @staticmethod
    def moveDeckToPlayer (
        db: Session, 
        game_id: UUID, 
        player_id: UUID, 
        n_cards: int
    ) -> Tuple[List[models.Card], bool]:
        """
        Mueve una carta y comprueba si el mazo se ha vaciado como resultado.
        
        Returns:
            Tuple[models.Card, bool]: (Las cartas movidas, si_el_mazo_quedo_vacio)
        """

        if (CardService.count_player_hand(db,game_id,player_id) + n_cards > 6):
            raise PlayerHandLimitExceededException()
    
        top_cards = (
            db.query(models.Card)
            .filter(
                models.Card.game_id == game_id,
                models.Card.owner == CardOwner.DECK
            )
            .order_by(desc(models.Card.order))  # Orden descendente, el más alto primero
            .limit(n_cards)              # Limitar la cantidad
            .all()
        )
        for card in top_cards:
            move_in = schemas.CardMoveIn(to_owner=CardOwner.PLAYER, player_id=player_id)
            CardService.move_card(db, card.id, move_in)

        deck_count = db.query(func.count(models.Card.id)).filter(
            models.Card.game_id == game_id,
            models.Card.owner == CardOwner.DECK
        ).scalar()
        from app.game.service import GameService
        game_service = GameService(db)
        hand_updated = CardService.count_player_hand(db, game_id, player_id)
        if hand_updated == 6:
            game_service.change_turn_state(game_id, TurnState.END_TURN)
        else:
            game_service.change_turn_state(game_id, TurnState.DRAWING_CARDS)
        
        return top_cards, deck_count == 0
    
    @staticmethod
    def movePlayertoDiscard (db: Session, game_id: UUID, id_player: UUID, cardPlayer: list[UUID] | UUID):

        if isinstance(cardPlayer, UUID):
            cardPlayer = [cardPlayer]
        # obtener todas las cartas a descartar
        cards = db.query(models.Card).filter(
            models.Card.id.in_(cardPlayer),
            models.Card.game_id == game_id,
            models.Card.owner == CardOwner.PLAYER,
            models.Card.owner_player_id == id_player
        ).all()

        if len(cards) != len(cardPlayer):
            raise CardsNotFoundOrInvalidException()
        
        # Filtro por Early train to Padd
        etp_cards = [c for c in cards if c.name == "E_ETP"]
        other_cards = [c for c in cards if c.name != "E_ETP"]

        for c in etp_cards:
            CardService.early_train_to_paddington(
                db,
                game_id,
                c.id,
                id_player
            )

        for card in other_cards :
            move_in = schemas.CardMoveIn(to_owner=CardOwner.DISCARD_PILE)
            CardService.move_card(db, card.id, move_in)

        from app.game.service import GameService
        game_service = GameService(db)
        game_service.change_turn_state(game_id, TurnState.DISCARDING)
        return other_cards
    
    @staticmethod
    def count_player_hand(db: Session, game_id: UUID, player_id: UUID) -> int:
        """Devuelve la cantidad de cartas que tiene un jugador en su mano."""
        return db.query(models.Card).filter(
            models.Card.game_id == game_id,
            models.Card.owner == CardOwner.PLAYER,
            models.Card.owner_player_id == player_id
        ).count()
    
    @staticmethod
    def initialize_draft(db: Session, game_id: UUID) -> list[models.Card]:
        "Inicializa el draft para el game"
       
        #Chequeo que no haya cartas en draft
        check_draft_empty = (
            db.query(models.Card)
            .filter(
                models.Card.game_id == game_id,
                models.Card.owner == CardOwner.DRAFT
            )
            .first()
        )
        if check_draft_empty:
            return None

        # Busco las primeras 3 en el mazo
        draft = (
            db.query(models.Card)
            .filter(
                models.Card.game_id == game_id,
                models.Card.owner == CardOwner.DECK
            )
            .order_by(desc(models.Card.order))
            .limit(3)
            .all()
        )
        if not draft:
            return None
        
        # Cambio de DECK a DRAFT
        for card in draft :
            move_in = schemas.CardMoveIn(to_owner=CardOwner.DRAFT)
            CardService.move_card(db, card.id, move_in)
        return draft
    
    @staticmethod
    def pick_draft(
        db: Session, 
        game_id: UUID, 
        player_id: UUID, 
        card_id: UUID
    ) -> Tuple[models.Card, bool]:
        "Tomar una carta del draft"

        # Chequeo que se pueda levantar una carta
        player_cards = CardService.count_player_hand(db, game_id, player_id)
        if player_cards >= 6:
            raise PlayerHandLimitExceededException()
        
        draft_card = (
            db.query(models.Card)
            .filter(
                models.Card.id == card_id,
                models.Card.game_id == game_id,
                models.Card.owner == CardOwner.DRAFT
            ).one_or_none()
        )

        if not draft_card:
            raise NoCardsException(str(game_id))
        
        move_in = schemas.CardMoveIn(to_owner=CardOwner.PLAYER, player_id=player_id)
        CardService.move_card(db, draft_card.id, move_in)

        deck_is_empty = CardService.update_draft(db,game_id)
        from app.game.service import GameService
        game_service = GameService(db)
        hand_updated = CardService.count_player_hand(db, game_id, player_id)
        if hand_updated == 6:
            game_service.change_turn_state(game_id, TurnState.END_TURN)
        else:
            game_service.change_turn_state(game_id, TurnState.DRAWING_CARDS)
        
        return draft_card, deck_is_empty
    
    @staticmethod
    def update_draft(
        db: Session, 
        game_id: UUID
    ) -> bool:
        "Actualiza las cartas del draft si es que hay menos de 3"

        draft_cards = (
            db.query(models.Card)
            .filter(
                models.Card.game_id == game_id,
                models.Card.owner == CardOwner.DRAFT
            ).all()
        )

        if len(draft_cards) == 3:
            return False
        
        missing_cards = 3 - len(draft_cards)
        top_cards = (
            db.query(models.Card)
            .filter(
                models.Card.game_id == game_id,
                models.Card.owner == CardOwner.DECK
            )
            .order_by(desc(models.Card.order))
            .limit(missing_cards)
            .all()
        )
        for card in top_cards :
            move_in = schemas.CardMoveIn(to_owner=CardOwner.DRAFT)
            CardService.move_card(db, card.id, move_in)

        deck_count = db.query(func.count(models.Card.id)).filter(
        models.Card.game_id == game_id,
        models.Card.owner == CardOwner.DECK
        ).scalar()

        return deck_count == 0
    
    @staticmethod
    def query_draft (db: Session, game_id: UUID) -> list[models.Card] | None:
        "Devuelve las cartas en draft"

        draft_cards = (
            db.query(models.Card)
            .filter(
                models.Card.game_id == game_id,
                models.Card.owner == CardOwner.DRAFT
            )
            .all()
            )
        if not draft_cards:
            return None
        return draft_cards
    
    @staticmethod
    def see_top_discard (db: Session, game_id: UUID, amount: int) -> list[models.Card]:
        "Obtener las cartas del mazo de descarte en orden"

        if amount < 1:
            raise InvalidAmountOfCards
        
        discard_pile = (
            db.query(models.Card)
            .filter(
                models.Card.game_id == game_id,
                models.Card.owner == CardOwner.DISCARD_PILE
            )
            .order_by(desc(models.Card.order))
            .limit(amount)
            .all()
            )
        
        
        return discard_pile
    

    @staticmethod
    def look_into_the_ashes (db: Session, 
                            game_id: UUID,
                            event_card_id: UUID,
                            card_id: UUID,
                            player_id: UUID
                            ) -> models.Card:
        "Jugar look into the ashes"
        
        lita = CardService.get_card_by_id(db,event_card_id)
        if (
            not lita 
            or lita.name != "E_LIA" 
            or lita.owner_player_id !=player_id
        ):
            raise CardsNotFoundOrInvalidException()
        
        top_discard_pile = CardService.see_top_discard(db,game_id,5)
        
        if card_id and card_id not in [c.id for c in top_discard_pile]:
            raise CardsNotFoundOrInvalidException()
        
        move_in = schemas.CardMoveIn(to_owner=CardOwner.PLAYER, player_id = player_id)
        card = CardService.move_card(db, card_id, move_in)
        move_in_lita = schemas.CardMoveIn(to_owner=CardOwner.DISCARD_PILE)
        CardService.move_card(db, event_card_id, move_in_lita)
        return card
    
    @staticmethod
    def early_train_to_paddington(db: Session,game_id: UUID,
                            event_card_id: UUID,player_id: UUID):
        
        etp = CardService.get_card_by_id(db,event_card_id)
        if (
            not etp 
            or etp.name != "E_ETP" 
            or etp.owner_player_id !=player_id
        ):
            raise CardsNotFoundOrInvalidException()
        
        top_cards = (
            db.query(models.Card)
            .filter(
                models.Card.game_id == game_id,
                models.Card.owner == CardOwner.DECK
            )
            .order_by(desc(models.Card.order))
            .limit(6)
            .all()
        )
        for card in top_cards:
            move_in = schemas.CardMoveIn(to_owner=CardOwner.DISCARD_PILE)
            CardService.move_card(db, card.id, move_in)

        # Eliminamos la carta del juego
        out_card = schemas.CardMoveIn(to_owner=CardOwner.OUT_OFF_THE_GAME)
        etp_out = CardService.move_card(db,event_card_id,out_card)
        return etp_out
    
    @staticmethod
    def delay_the_murderer_escape(db: Session,
                                  game_id: UUID, 
                                  player_id: UUID,
                                  event_card_id: UUID
    )-> models.Card:
        
        dms = CardService.get_card_by_id(db,event_card_id)
        if (
            not dms 
            or dms.name != "E_DME" 
            or dms.owner_player_id !=player_id
        ):
            raise CardsNotFoundOrInvalidException()
        
        top_discard = CardService.see_top_discard(db,game_id,5)

        for card in top_discard:
            move_in = schemas.CardMoveIn(to_owner=CardOwner.DECK)
            CardService.move_card(db, card.id, move_in)

        # Eliminamos la carta del juego
        out_card = schemas.CardMoveIn(to_owner=CardOwner.OUT_OFF_THE_GAME)
        dms_out = CardService.move_card(db,event_card_id,out_card)

        return dms_out
    
    @staticmethod
    def cards_off_the_table(db: Session, 
                            game_id: UUID,
                            player_id: UUID,
                            event_card_id: UUID,
                            target_player: UUID
    ) -> models.Card:
        
        cot = CardService.get_card_by_id(db,event_card_id)
        if (
            not cot 
            or cot.name != "E_COT" 
            or cot.owner_player_id !=player_id
        ):
            raise CardsNotFoundOrInvalidException()
        
        cards_target_player = CardService.get_cards_by_owner(db, 
                                                             game_id, 
                                                             CardOwner.PLAYER,
                                                             target_player)
        
        not_so_fast_cards = [c for c in cards_target_player if c.name == "E_NSF"]
        
        move_in = schemas.CardMoveIn(to_owner=CardOwner.DISCARD_PILE)
        cot_out = CardService.move_card(db,event_card_id,move_in)
        for card in not_so_fast_cards:
            CardService.move_card(db, card.id, move_in)

        return cot_out
    
    @staticmethod
    def then_there_was_one_more(
        db: Session,
        game_id: UUID,
        player_id: UUID,
        event_card_id: UUID,
        target_player: UUID,
        secret_id: UUID
    ) -> models.Card:
        
        atwom = CardService.get_card_by_id(db,event_card_id)
        if (
            not atwom 
            or atwom.name != "E_ATWOM" 
            or atwom.owner_player_id !=player_id
        ):
            raise CardsNotFoundOrInvalidException()
        
        secret = SecretService.get_secret_by_id(db, secret_id)
        if (not secret 
            or not secret.revealed
            or secret.game_id != game_id):
            raise SecretNotFoundOrInvalidException(secret_id,game_id)
        
        SecretService.change_secret_status(db, secret_id)
        SecretService.move_secret(db, secret_id, target_player)

        move_in = schemas.CardMoveIn(to_owner=CardOwner.DISCARD_PILE)
        atwom_out = CardService.move_card(db,event_card_id,move_in)

        return atwom_out
    
    @staticmethod
    def another_victim(
        db: Session, 
        game_id: UUID,
        player_id: UUID,
        event_card_id: UUID,
        target_set_id: UUID
    ) -> models.Card:
        
        another_victim = CardService.get_card_by_id(db,event_card_id)
        if (
            not another_victim 
            or another_victim.name != "E_AV" 
            or another_victim.owner_player_id !=player_id
        ):
            raise CardsNotFoundOrInvalidException()
        
        from app.set.service import SetService
        set_service = SetService(db)
        target_set = set_service.get_set_by_id(db,target_set_id)
        if (not target_set
            or target_set.game_id != game_id
            or target_set.owner_player_id == player_id):
            raise SetNotFound(target_set_id)
        set_service.change_set_owner(game_id,target_set_id,player_id)
        move_in = schemas.CardMoveIn(to_owner=CardOwner.DISCARD_PILE)
        another_victim_out = CardService.move_card(db,event_card_id,move_in)

        return another_victim_out

    @staticmethod
    def card_trade(
        db: Session,
        game_id: UUID,
        player_id: UUID,
        event_card_id: UUID,
        target_player_id: UUID,
        offered_card_id: UUID,
        target_card_id: UUID,
    ) -> Dict[str, Any]:
        """
        Intercambia cartas entre jugadores según las reglas de Card Trade.
        Devuelve un diccionario [discarded_card, blackmailed_events]
        """
        blackmailed_events: List[Dict[str, Any]] = []

        offered_card = CardService.get_card_by_id(db, offered_card_id)
        if (
            not offered_card
            or offered_card.game_id != game_id
            or offered_card.owner != CardOwner.PLAYER
            or offered_card.owner_player_id != player_id
        ):
            raise CardsNotFoundOrInvalidException()

        target_card = CardService.get_card_by_id(db, target_card_id)
        if (
            not target_card
            or target_card.game_id != game_id
            or target_card.owner != CardOwner.PLAYER
            or target_card.owner_player_id != target_player_id
        ):
            raise CardsNotFoundOrInvalidException()

        move_to_target = schemas.CardMoveIn(
            to_owner=CardOwner.PLAYER, player_id=target_player_id
        )
        CardService.move_card(db, offered_card_id, move_to_target)

        from app.game.service import GameService
        game_service = GameService(db)

        print(offered_card.name, target_card.name)

        # --- Lógica de Blackmailed (A -> B) ---
        if offered_card.name == "DV_BLM":
            blackmailed_events.append(
                CardService._create_blackmailed_event(
                    db, game_id,
                    actor_player_id=player_id, # A (envió) elige
                    target_player_id=target_player_id, # B (recibió) muestra
                    trigger_card_id=offered_card.id
                )
            )
        
        elif offered_card.name == "DV_SFP":
            game_service.change_turn_state(
                game_id, 
                TurnState.PENDING_DEVIOUS, 
                target_player_id=target_player_id
            )
            

        move_to_player = schemas.CardMoveIn(
            to_owner=CardOwner.PLAYER, player_id=player_id
        )
        CardService.move_card(db, target_card_id, move_to_player)

        # --- Lógica de Blackmailed (B -> A) ---
        if target_card.name == "DV_BLM":
            blackmailed_events.append(
                CardService._create_blackmailed_event(
                    db, game_id,
                    actor_player_id=target_player_id, # B (envió) elige
                    target_player_id=player_id, # A (recibió) muestra
                    trigger_card_id=target_card.id
                )
            )

        elif target_card.name == "DV_SFP":
            game_service.change_turn_state(
                game_id, 
                TurnState.PENDING_DEVIOUS, 
                target_player_id=player_id
            )

        discard_event = schemas.CardMoveIn(to_owner=CardOwner.DISCARD_PILE)
        discarded_event = CardService.move_card(db, event_card_id, discard_event)

        return {
            "discarded_card": discarded_event,
            "blackmailed_events": blackmailed_events
        }
    
    @staticmethod
    def ensure_move_valid(db: Session,game_id,player_id: UUID,n_cards:int) -> bool:
        player_service = PlayerService(db)
        player_obj = player_service.get_player_entity_by_id(player_id)
        hand_player = CardService.count_player_hand(db, game_id,player_id)
        # Si está en desgracia y quiere mover más de 1 carta → inválido
        if player_obj.social_disgrace and n_cards > 1:
            return False
        return not player_obj.social_disgrace or (hand_player == 6)

    @staticmethod
    def select_card_for_passing(
        db: Session, 
        game_id: UUID, 
        player_id: UUID, 
        card_id: UUID
    ) -> models.Card:
        """
        Mueve la carta de un jugador al estado PASSING,
        conservando el owner_player_id original.
        """
        card = CardService.get_card_by_id(db, card_id)

        if (not card 
            or card.owner != CardOwner.PLAYER 
            or card.owner_player_id != player_id
            or card.game_id != game_id):
            raise CardsNotFoundOrInvalidException(f"Card {card_id} is not valid for this action")

        existing_selection = (
            db.query(models.Card)
            .filter(
                models.Card.game_id == game_id,
                models.Card.owner == CardOwner.PASSING,
                models.Card.owner_player_id == player_id
            )
            .first()
        )
        if existing_selection:
            raise HTTPException(status_code=403, detail="Player has already selected a card")

        card.owner = CardOwner.PASSING
        db.commit()
        db.refresh(card)
        
        return card

    @staticmethod
    def check_if_all_players_selected(
        db: Session, 
        game_id: UUID, 
        game_entity: Game
    ) -> bool:
        """
        Comprueba si el número de cartas en 'PASSING' 
        coincide con el número de jugadores.
        """
        n_players = len(game_entity.players)
        
        n_passing_cards = (
            db.query(func.count(models.Card.id))
            .filter(
                models.Card.game_id == game_id,
                models.Card.owner == CardOwner.PASSING
            )
            .scalar()
        )
        
        return n_players > 0 and n_players == n_passing_cards

    @staticmethod
    def execute_dead_card_folly_swap(
        db: Session, 
        game_id: UUID, 
        game_entity: Game
    ) -> List[Dict[str, Any]]:
        """
        Ejecuta el intercambio de todas las cartas en estado 'PASSING'.
        Ejecuta la accion de la carta Blackmailed en el jugador que la recibe.
        """
        from app.game.enums import TurnState
        from app.game.service import GameService
        game_service = GameService(db)

        blackmailed_events: List[Dict[str, Any]] = []

        if not game_entity.turn_state:
             raise HTTPException(status_code=500, detail="Game state is missing during swap")

        direction = game_entity.turn_state.passing_direction
        event_card_id = game_entity.turn_state.current_event_card_id
        
        sorted_players = sorted(game_entity.players, key=lambda p: p.id)
        players_list = [p.id for p in sorted_players]
        num_players = len(players_list)

        cards_to_pass = (
            db.query(models.Card)
            .filter(
                models.Card.game_id == game_id,
                models.Card.owner == CardOwner.PASSING
            )
            .all()
        )
        
        for card in cards_to_pass:
            sender_id = card.owner_player_id
            sender_index = players_list.index(sender_id)

            if direction == "right":
                recipient_index = (sender_index + 1) % num_players
            else:
                recipient_index = (sender_index - 1 + num_players) % num_players
                
            recipient_id = players_list[recipient_index]
            
            move_in = schemas.CardMoveIn(to_owner=CardOwner.PLAYER, player_id=recipient_id)
            CardService.move_card(db, card.id, move_in)

            if card.name == "DV_BLM":
                blackmail_data = CardService._create_blackmailed_event(
                    db, game_id,
                    actor_player_id=sender_id,
                    target_player_id=recipient_id,
                    trigger_card_id=card.id
                )
                blackmailed_events.append(blackmail_data)

            if card.name == "DV_SFP":
                game_service.change_turn_state(
                    game_id,
                    TurnState.PENDING_DEVIOUS,
                    target_player_id=recipient_id
                )
        
        if game_entity.turn_state.state != TurnState.PENDING_DEVIOUS:
            game_service.change_turn_state(
                game_id, 
                TurnState.DISCARDING
            )
        
        return blackmailed_events


    def social_faux_pas(
            self,
            game_id: UUID,
            player_id: UUID,
            secret_id: UUID,
            social_faux_pas_id: UUID) -> SecretOut:
        
        player = PlayerService(self.db)
        player_entity = player.get_player_entity_by_id(player_id)

        if not player_entity or player_entity.game_id != game_id:
            raise ValueError("Player not found in the game")
        
        secret = SecretService(self.db)
        secret_entity = secret.get_secret_by_id(secret_id)

        if not secret_entity or secret_entity.owner_player_id != player_id:
            raise ValueError("Secret not found")

        if not secret_entity.revealed:
            try:
                changed_secret = secret.change_secret_status(self.db, secret_id)
            except ValueError as e:
                raise ValueError("Error discarding") from e
            
        else:
            raise ValueError("Secret already revealed")
        
        # Move the Social Faux Pas card to the discard pile
        CardService.movePlayertoDiscard(self.db, game_id, player_id, social_faux_pas_id)
        
        return changed_secret
    
    @staticmethod
    def get_players_who_selected_card(db: Session, game_id: UUID) -> List[UUID]:
        """
        Devuelve una lista de los IDs de los jugadores que ya han 
        seleccionado una carta para la fase 'PASSING_CARDS'.
        """
        
        player_ids_tuples = (
            db.query(models.Card.owner_player_id)
            .filter(
                models.Card.game_id == game_id,
                models.Card.owner == CardOwner.PASSING,
                models.Card.owner_player_id.isnot(None)
            )
            .distinct()
            .all()
        )
        
        return [pid[0] for pid in player_ids_tuples]
    
    @staticmethod
    def check_if_all_players_voted(
        db: Session, 
        game_id: UUID, 
        game_entity: Game
    ) -> bool:
        """
        Comprueba si el número de votos en el JSON
        coincide con el número de jugadores.
        """
        n_players = len(game_entity.players)
        
        if not game_entity.turn_state or not game_entity.turn_state.vote_data:
            n_votes = 0
        else:
            n_votes = len(game_entity.turn_state.vote_data)
        
        return n_players > 0 and n_players == n_votes
    
    @staticmethod
    async def execute_pys_vote(
        db: Session, 
        game_id: UUID, 
        game_entity: Game
    ) -> UUID:
        """
        Cuenta los votos, descarta la carta PYS, y cambia el estado
        a CHOOSING_SECRET, apuntando al jugador elegido.
        Devuelve el ID del jugador que debe revelar.
        """
        from app.game.service import GameService
        game_service = GameService(db)

        if not game_entity.turn_state or not game_entity.turn_state.vote_data:
            raise HTTPException(
                status_code=500, 
                detail="Estado de juego o turno actual fallan en la votacion"
            )
        
        event_card_id = game_entity.turn_state.current_event_card_id

        pys_player_id = game_entity.current_turn

        all_votes_dict = game_entity.turn_state.vote_data
        
        pys_player_target_id = all_votes_dict.get(str(pys_player_id))
        pys_player_target_id = UUID(pys_player_target_id) if pys_player_target_id else None

        if not pys_player_target_id:
            raise HTTPException(status_code=500, detail="No se encontró el voto del jugador que inició el evento")

        # Contar los votos
        vote_counts = Counter(all_votes_dict.values())
        
        # Jugador(es) mas votado(s)
        max_votes = max(vote_counts.values())
        players_with_max_votes = [
            UUID(pid) for pid, count in vote_counts.items() if count == max_votes
        ]

        player_to_reveal_id: UUID
        
        if len(players_with_max_votes) == 1:
            # Ganador por Mayoría Estricta
            player_to_reveal_id = players_with_max_votes[0]
        else:
            # Empate 
            # El voto del PYS Player decide el ganador.
            player_to_reveal_id = pys_player_target_id
        
        # El jugador elegido revela un secreto
        game_service.change_turn_state(
            game_id, 
            TurnState.CHOOSING_SECRET_PYS,
            target_player_id=player_to_reveal_id 
        )
        
        return player_to_reveal_id
    
    @staticmethod
    def verify_cancellable_card(
        db: Session,
        event_card_id: UUID
    ) -> bool:
        """
        Verifica si la carta de evento puede ser cancelada por el jugador.
        """
        card = CardService.get_card_by_id(db, event_card_id)

        if (not card):
            raise CardsNotFoundOrInvalidException(f"Card {event_card_id} not found")

        return card.name not in {"E_COT", "DV_BLM"}

    @staticmethod
    def _create_blackmailed_event(
        db: Session, 
        game_id: UUID, 
        actor_player_id: UUID, # Quien envía (elige)
        target_player_id: UUID, # Quien recibe (muestra)
        trigger_card_id: UUID
    ) -> Dict[str, Any]:
        """
        Helper para crear el payload del evento Blackmailed.
        Busca los secretos NO revelados del jugador objetivo.
        """
        
        secrets_of_target = db.query(Secrets).filter(
            Secrets.game_id == game_id,
            Secrets.owner_player_id == target_player_id,
            Secrets.revealed == False
        ).all()

        blackmailed_data = {
            "actor_player_id": str(actor_player_id),
            "target_player_id": str(target_player_id),
            "trigger_card_name": "Blackmailed",
            "available_secrets": [
                {"id": str(s.id), "name": s.name} for s in secrets_of_target
            ]
        }
        
        return blackmailed_data

    async def wait_for_cancellation(db: Session, game_id: UUID, timeout: int = 7) -> bool:
        """
        Espera `timeout` segundos mientras el estado de la partida sea CANCELLED_CARD_PENDING.
        """

        end_time = datetime.now() + timedelta(seconds=timeout)

        while datetime.now() < end_time:
            # Consultamos el estado en la DB
            game = db.query(Game).filter_by(id=game_id).first()
            if not game or game.turn_state.state != TurnState.CANCELLED_CARD_PENDING:
                raise HTTPException(status_code=404, detail="Game")
            db.refresh(game.turn_state)

            if game.turn_state.is_canceled_card != game.turn_state.last_is_canceled_card:
                end_time = datetime.now() + timedelta(seconds=timeout)
                game.turn_state.last_is_canceled_card = game.turn_state.is_canceled_card
                db.commit()

            await asyncio.sleep(0.2) 
    
    @staticmethod
    def check_players_SFP(db: Session, game_id: UUID, player_id: UUID) -> bool:
        from app.game.models import GameTurnState

        game_state = (
            db.query(GameTurnState)
            .filter(GameTurnState.game_id == game_id)
            .first()
        )

        if game_state is None:
            raise HTTPException(status_code=404, detail="GameTurnState no encontrado")

        if game_state.sfp_players is None:
            raise HTTPException(status_code=500, detail="No se encontró la lista de votos de jugadores")
        
        pid = str(player_id)

        try:
            game_state.sfp_players.remove(pid)
        except ValueError:
            raise HTTPException(status_code=400, detail="El jugador no estaba pendiente")

        db.commit()

        return len(game_state.sfp_players) == 0