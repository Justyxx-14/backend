from typing import Sequence
from uuid import UUID, uuid4
from sqlalchemy import func
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError

from app.card.enums import CardType, CardOwner
from app.card.service import CardService
from .models import Set as SetModel
from .dtos import *
from .enums import SetType
from app.game.service import GameService
from app.game.schemas import GameEndReason
from app.secret.enums import SecretType
from app.secret.models import Secrets
from app.secret.service import SecretService
from app.card.service import CardService
from app.player.models import Player


class SetService:
    DETECTIVE_TWO_BASE = {"D_MS", "D_TB", "D_TUB", "D_PP", "D_LEB"}
    DETECTIVE_THREE_BASE = {"D_MM", "D_HP"}
    DETECTIVE_SIBLINGS = {"D_TB", "D_TUB"}
    HQW_NAME = "D_HQW"
    D_MS_NAME = "D_MS"

    def __init__(self, db: Session):
        self.db = db

    @staticmethod
    def _to_dto(set_model: SetModel) -> SetOut:
        return SetOut(
            id=set_model.id,
            type=set_model.type,
            game_id=set_model.game_id,
            owner_player_id=set_model.owner_player_id,
        )

    @staticmethod
    def get_set_by_id(db: Session, set_id: UUID) -> SetOut | None:
        item = db.query(SetModel).filter(SetModel.id == set_id).first()
        return SetService._to_dto(item) if item else None

    @staticmethod
    def get_sets_for_player_in_game(
        db: Session, *, player_id: UUID, game_id: UUID
    ) -> list[SetOut]:
        items = (
            db.query(SetModel)
            .filter(
                SetModel.owner_player_id == player_id,
                SetModel.game_id == game_id,
            )
            .all()
        )
        return [SetService._to_dto(item) for item in items]

    def validate_set(self, card_ids: Sequence[UUID]) -> SetType:
        """
        Valida las cartas, verifica que pueden formar un set correcto
        y retorna el tipo de set.
        NO toca la base de datos (no crea ni borra nada).
        """
        if len(card_ids) not in {2, 3}:
            raise ValueError("Invalid number of cards for a set")

        set_type = self.determine_set_type(list(card_ids))

        return set_type

    def create_set(
            self, 
            game_id: UUID, 
            player_id: UUID, 
            set_type: SetType, 
            cards: list[UUID]
        ) -> SetOut:
        """
        Recibe un set ya validado y su tipo.
        Se encarga de crear en la base de datos, 
        cambiar de dueño las cartas a SET y persistir el set.
        """
        list_card = []
        list_card = self._load_cards(cards,player_id=player_id,game_id=game_id)

        for card in list_card:
            card.owner = CardOwner.SET
            self.db.add(card)

        new_set = SetModel(
            id=uuid4(),
            game_id=game_id,
            type=set_type,
            owner_player_id=player_id,
        )
        self.db.add(new_set)

        try:
            self.db.commit()
        except SQLAlchemyError as exc:
            self.db.rollback()
            raise ValueError("Failed to create set") from exc

        self.db.refresh(new_set)

        return SetService._to_dto(new_set)


    def _load_cards(
        self,
        card_ids: Sequence[UUID],
        *,
        player_id: UUID,
        game_id: UUID,
    ):
        cards = []
        for card_id in card_ids:
            card = CardService.get_card_by_id(self.db, card_id)
            if card is None:
                raise ValueError(f"Card {card_id} not found")
            if card.game_id != game_id:
                raise ValueError(f"Card {card_id} does not belong to game {game_id}")
            if card.owner != CardOwner.PLAYER or card.owner_player_id != player_id:
                raise ValueError(f"Card {card_id} does not belong to player {player_id}")
            cards.append(card)
        return cards

    def determine_set_type(self, cards:list[UUID]) -> SetType:
        list_card = []
        for c in cards:
            card = CardService.get_card_by_id(self.db, c)
            list_card.append(card)
        if any(card.type != CardType.DETECTIVE for card in list_card):
            raise ValueError("Invalid type of cards for a set")

        if len(list_card) == 2:
            return self._resolve_two_detective_cards(list_card)

        if len(list_card) == 3:
            return self._resolve_three_detective_cards(list_card)

        raise ValueError("Unsupported number of cards")

    @classmethod
    def _resolve_two_detective_cards(cls, cards) -> SetType:
        names = cls._extract_card_names(cards)
        unique_names = set(names)

        if len(unique_names) == 1:
            (name,) = tuple(unique_names)
            if name not in cls.DETECTIVE_TWO_BASE:
                raise ValueError("Invalid set of detectives")
            return cls._to_set_type(name)

        if cls.HQW_NAME in unique_names:
            other_names = unique_names - {cls.HQW_NAME}
            if len(other_names) != 1:
                raise ValueError("Invalid set of detectives")
            (other_name,) = tuple(other_names)
            if other_name not in cls.DETECTIVE_TWO_BASE:
                raise ValueError("Invalid set of detectives")
            if other_name == cls.D_MS_NAME:
                return SetType.HARLEY_MS
            return cls._to_set_type(other_name)

        if unique_names.issubset(cls.DETECTIVE_SIBLINGS):
            return SetType.SIBLINGS_B

        raise ValueError("Invalid set of detectives")

    @classmethod
    def _resolve_three_detective_cards(cls, cards) -> SetType:
        names = cls._extract_card_names(cards)
        unique_names = set(names)

        if len(unique_names) == 1:
            (name,) = tuple(unique_names)
            if name not in cls.DETECTIVE_THREE_BASE:
                raise ValueError("Cannot form a set with HQW")
            return cls._to_set_type(name)

        if len(unique_names) == 2 and cls.HQW_NAME in unique_names:
            other_names = unique_names - {cls.HQW_NAME}
            if len(other_names) != 1:
                raise ValueError("Invalid set of detectives")
            (other_name,) = tuple(other_names)
            if other_name not in cls.DETECTIVE_THREE_BASE:
                raise ValueError("Invalid set of detectives")
            return cls._to_set_type(other_name)

        raise ValueError("Invalid set of detectives")

    @staticmethod
    def _extract_card_names(cards) -> list[str]:
        return [card.name for card in cards]

    @staticmethod
    def _to_set_type(card_name: str) -> SetType:
        return SetType(card_name.removeprefix("D_").upper())

    def play_set(
        self,
        set_id: UUID,
        target_player_id: UUID,
        secret_id: UUID
    ) -> SetPlayResult:
        
        existing_set = self.db.query(SetModel).filter(SetModel.id == set_id).first()
        if not existing_set:
            raise ValueError("Set not found")

        service_secret = SecretService()
        
        target_secret = service_secret.get_secret_by_id(self.db, secret_id)
        if not target_secret:
             raise ValueError("Secret not found")
        if target_secret.owner_player_id != target_player_id:
            raise ValueError("The secret must belong to the target player")

        if existing_set.type == SetType.PP: # Set para ocultar
            if not target_secret.revealed:
                raise ValueError("The secret is already hidden")
        else: # Set para revelar
            if target_secret.revealed:
                raise ValueError("The secret is already revealed")
      
        game_id = existing_set.game_id
        set_out_dto = self._to_dto(existing_set)
        game_service = GameService(self.db)
        end_game_result: EndGameResult | None = None

        updated_secret_dto = service_secret.change_secret_status(self.db, secret_id)

        if existing_set.type != SetType.PP and updated_secret_dto.revealed:

            # Si el secreto es el asesino
            if updated_secret_dto.role == SecretType.MURDERER:
                end_game_result = game_service.end_game(
                    game_id, GameEndReason.MURDERER_REVEALED
                )

            else:               
                murderer_team_ids = SecretService.get_murderer_team_ids(self.db, game_id)
                
                unrevealed_detective_secrets_count = self.db.query(func.count(Secrets.id)).filter(
                    Secrets.game_id == game_id,
                    Secrets.revealed == False,
                    Secrets.owner_player_id.notin_(murderer_team_ids) 
                ).scalar()
                # Todos los secretos de los "detectives" se revelaron
                if unrevealed_detective_secrets_count == 0:
        
                    # Comprobamos que el asesino siga oculto
                    murderer_role_secret = self.db.query(Secrets).filter(
                        Secrets.game_id == game_id,
                        Secrets.role == SecretType.MURDERER
                    ).first()
                    
                    if murderer_role_secret and not murderer_role_secret.revealed:
                        # Ganan los asesinos
                        end_game_result = game_service.end_game(
                            game_id, GameEndReason.SECRETS_REVEALED
                        )

        return SetPlayResult(set_out=set_out_dto, end_game_result=end_game_result)
    
    def add_card_to_set(
            self, 
            game_id:UUID, 
            player_id:UUID,
            set_id: UUID,
            card_id: UUID
    )-> SetOut:
        """
        Agrega una carta detective a un set existente del mismo jugador.
        Valida compatibilidad de tipo y propiedad antes de actualizar.
        No ejecuta la acción asociada al SET.
        """
        set_played = self.db.query(SetModel).filter(SetModel.id == set_id).first()
        card_to_add = CardService.get_card_by_id(self.db, card_id)
        if (not card_to_add
            or card_to_add.game_id != game_id
            or card_to_add.owner_player_id != player_id):
            raise ValueError("NotValidCardID")

        if (not set_played
            or set_played.game_id != game_id
            or set_played.owner_player_id != player_id):
            raise ValueError("NotValidSetToAdd")
        
        if (not card_to_add.type == "DETECTIVE"
            or card_to_add.name == "D_HQW"):
            raise ValueError("NotValidCardID")

        if set_played.type == "HARLEY_MS" and card_to_add.name != "D_MS":
            raise ValueError("NotMatchingSetType")
        if set_played.type == "SIBLINGS_B" and card_to_add.name not in ("D_TB", "D_TUB"):
            raise ValueError("NotMatchingSetType")
        if set_played.type != self._to_set_type(card_to_add.name):
            raise ValueError("NotMatchingSetType")
        
        card_to_add.owner=CardOwner.SET
        
        try:
            self.db.add(card_to_add)
            self.db.commit()
        except SQLAlchemyError as exc:
            self.db.rollback()
            raise ValueError("Failed to add card to set") from exc
        

        return SetService._to_dto(set_played)

    def change_set_owner(self, game_id: UUID, set_id: UUID, new_owner_id: UUID) -> SetOut:
        existing_set = self.db.query(SetModel).filter(
            SetModel.id == set_id,
            SetModel.game_id == game_id
        ).first()
        if not existing_set:
            raise ValueError("Set not found")
        
        if existing_set.owner_player_id == new_owner_id:
            raise ValueError("New owner is the same as the current owner")
        
        # verify that the new owner is a player in the same game
        player = self.db.get(Player, new_owner_id)
        if player is None or getattr(player, "game_id", None) != existing_set.game_id:
            raise ValueError("New owner does not belong to the game")

        existing_set.owner_player_id = new_owner_id

        try:
            self.db.commit()
        except SQLAlchemyError as exc:
            self.db.rollback()
            raise ValueError("Failed to change set owner") from exc
        self.db.refresh(existing_set)
        return SetOut(
            id=existing_set.id,
            type=existing_set.type,
            game_id=existing_set.game_id,
            owner_player_id=existing_set.owner_player_id
        )
