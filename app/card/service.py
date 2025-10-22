from uuid import UUID, uuid4
from sqlalchemy import desc, func
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from typing import List, Tuple
import random


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
    