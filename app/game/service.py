from datetime import date
from typing import List, Optional, Union
from uuid import UUID
from fastapi import HTTPException
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func
from sqlalchemy.orm.attributes import flag_modified
from pathlib import Path
import json
from fastapi.encoders import jsonable_encoder

from app.game.models import Game
from app.game.models import GameTurnState
from app.game.dtos import GameInDTO, GameOutDTO
from app.game.schemas import EndGameResult, PlayerSummary, PlayerRoleInfo, GameTurnStateOut
from app.game.enums import GameEndReason, WinningTeam, PlayerRole, TurnState
from app.player.service import PlayerService
from app.player.dtos import PlayerInDTO
from app.player.models import Player
from app.card.enums import CardOwner
from app.card.schemas import CardIn, CardBatchIn, CardMoveIn
from app.card.service import CardService
from app.secret.service import SecretService
from app.secret.models import Secrets
from app.secret.enums import SecretType
from app.websocket.connection_man import manager
from app.game.turn_timer import turn_timer_manager



class GameService:
    def __init__(self, db: Session):
        self.db = db

    def get_games(self, full: bool = False, ready: bool = False) -> List[GameOutDTO]:
        """
        Lista partidas con filtros opcionales:
          - include_full=True  -> incluye partidas llenas (no aplica HAVING count(players) < max_players)
          - include_ready=True -> incluye partidas ya iniciadas (no filtra ready == False)
        Por defecto (False/False) filtra juegos disponibles.
        """
        q = self.db.query(Game).outerjoin(Game.players).group_by(Game.id)
        if not full:
            q = q.having(func.count(Game.players) < Game.max_players)
        if not ready:
            q = q.filter(Game.ready == False)
        games = q.all() or []

        return [
            GameOutDTO(
                id=g.id,
                name=g.name,
                password= g.password,
                host_id=g.host_id,
                min_players=g.min_players,
                max_players=g.max_players,
                ready=g.ready,
                players_ids=[p.id for p in g.players],
            )
            for g in games
        ]

    def get_game_by_id(self, game_id: UUID) -> Optional[GameOutDTO]:
        game = self.db.query(Game).filter(Game.id == game_id).first()
        if not game:
            return None
        return GameOutDTO(
            id=game.id,
            name=game.name,
            password=game.password,
            host_id=game.host_id,
            min_players=game.min_players,
            max_players=game.max_players,
            ready=game.ready,
            players_ids=[p.id for p in game.players],
        )
    
    def get_game_entity_by_id(self, game_id: UUID) -> Optional[Game]:
        """
        Devuelve la entidad (el modelo SQLAlchemy) completa de Game,
        incluyendo la relación 'turn_state' precargada.
        """
        game = (
            self.db.query(Game)
            .options(joinedload(Game.turn_state))
            .filter(Game.id == game_id)
            .first()
        )
        return game

    def get_turn_state_entity(self, game_id: UUID) -> Optional[GameTurnState]:
        """
        Devuelve la entidad (modelo SQLAlchemy) GameTurnState para un juego.
        """
        state = (
            self.db.query(GameTurnState)
            .filter(GameTurnState.game_id == game_id)
            .first()
        )
        return state

    def create_game(self, game_data: GameInDTO) -> GameOutDTO:
        player_service = PlayerService(self.db)
        host_dto = PlayerInDTO(name=game_data.host_name, birthday=game_data.birthday)
        host = player_service.create_player(host_dto)

        new_game = Game(
            name=game_data.name,
            password=game_data.password,
            host_id=host.id,
            min_players=game_data.min_players,
            max_players=game_data.max_players,
            ready=False,
        )
        self.db.add(new_game)
        self.db.commit()
        self.db.refresh(new_game)

        # Asignar host al juego
        host_entity = player_service.get_player_entity_by_id(host.id)
        if not host_entity:
            raise Exception("Host player not found after creation")
        host_entity.game_id = new_game.id
        self.db.commit()
        self.db.refresh(host_entity)

        return GameOutDTO(
            id=new_game.id,
            name=new_game.name,
            password=new_game.password,
            host_id=host.id,
            min_players=new_game.min_players,
            max_players=new_game.max_players,
            ready=new_game.ready,
            players_ids=[host.id],
        )

    def add_player(self, game_id: UUID, join_data: PlayerInDTO) -> Optional[UUID]:
        game = self.db.query(Game).filter(Game.id == game_id).first()
        if not game or game.ready or len(game.players) >= game.max_players:
            return None

        player_service = PlayerService(self.db)
        new_player = player_service.create_player(join_data)

        new_player_entity = player_service.get_player_entity_by_id(new_player.id)
        if not new_player_entity:
            raise Exception("Player not found after creation")
        new_player_entity.game_id = game.id

        self.db.commit()
        return new_player.id

    def can_start(self, game_id: UUID) -> bool:
        game = self.db.query(Game).filter(Game.id == game_id).first()
        if not game:
            return False
        return len(game.players) >= game.min_players and not game.ready

    def first_player(self, game_id: UUID) -> Optional[UUID]:
        target_date = date(
            year=2025, month=9, day=15
        )  # usamos año fijo solo para comparar día/mes
        player_serv = PlayerService(self.db)
        players = player_serv.get_players_by_game_id(game_id)

        if not players:
            return None

        def diff(player):
            # Convertir cumpleaños a año 2025
            bday = player.birthday.replace(year=2025)
            delta = abs((bday - target_date).days)
            return delta

        # Retornar ID del jugador con menor diferencia
        return min(players, key=diff).id

    def next_player(self, game_id: UUID) -> Union[UUID, EndGameResult]:
        game = self.db.query(Game).filter(Game.id == game_id).first()
        if not game or len(game.players) < game.min_players:
            raise ValueError(f"El juego {game_id} no está iniciado o no hay suficientes jugadores")         

        current_player_id = game.current_turn
        players = sorted(game.players, key=lambda p: p.id)

        if not players:
            raise ValueError(f"El juego {game_id} no tiene jugadores")

        for idx, player in enumerate(players):
            if player.id == current_player_id:
                next_idx = (idx + 1) % len(players)
                game.current_turn = players[next_idx].id
                if game.turn_state:
                    game.turn_state.state = TurnState.IDLE
                else:
                    # Si no existe, lo creamos
                    game.turn_state = GameTurnState(
                        game_id=game.id,
                        state=TurnState.IDLE
                    )
                self.db.commit()
                return players[next_idx].id

        game.current_turn = players[0].id
        self.db.commit()
        return players[0].id

    def start_game(self, game_id: UUID, deck_json: dict | None = None) -> bool:
        game = self.db.query(Game).filter(Game.id == game_id).first()
        if not game or len(game.players) < game.min_players:
            return False

        game.current_turn = self.first_player(game_id)

        game.ready = True
        self.db.commit()  # Marcamos la partida como lista y setea first player

        # Usar deck_json pasado como parámetro o leer desde archivo
        if deck_json is None:
            if len(game.players) == 2:
                deck_path = Path(__file__).parent.parent / "card" / "deck2p.json"
            else:
                deck_path = Path(__file__).parent.parent / "card" / "deck.json"
            
            with deck_path.open("r", encoding="utf-8") as f:    
                deck_json = json.load(f)

        assert deck_json is not None

        cartas_json_payload = CardBatchIn(
            items=[
                CardIn(
                    type=item["type"],
                    name=item["name"],
                    description=item["description"],
                )
                for item in deck_json["items"]
            ]
        )

        # Crear cartas en la DB
        CardService.create_cards_batch(self.db, game.id, cartas_json_payload)
        CardService.shuffle_deck(self.db,game_id)

        # Repartir cartas a los jugadores
        player_service = PlayerService(self.db)
        jugadores = player_service.get_players_by_game_id(game.id)
        jugadores_ids = [p.id for p in jugadores]

        # Repartir 6 cartas por jugador
        CardService.deal_cards(self.db, game.id, jugadores_ids, cartas_por_jugador=6)

        # Inicializa el draft
        draft = CardService.initialize_draft(self.db, game.id)
        assert draft != None

        # Inicializar y repartir los secretos
        SecretService.create_secrets(self.db, game.id, jugadores_ids)
        SecretService.deal_secrets(self.db, game.id, jugadores_ids)   
        game.turn_state = GameTurnState(
            game_id=game.id,
            state=TurnState.IDLE
        )
        
        self.db.commit()
        return True

    def get_turn(self, game_id: UUID) -> Optional[UUID]:
        current_turn = self.db.query(Game).filter(Game.id == game_id).first()
        if not current_turn:
            return None

        return current_turn.current_turn
    
    def are_all_other_secrets_revealed(self, game_id: UUID) -> bool:
        '''
        Verifica si todos los secretos de los demás jugadores, excepto
        del asesino fueron revelados
        '''
        all_secrets = self.db.query(Secrets).filter(Secrets.game_id == game_id).all()

        unrevealed_secrets = any(
            not secret.revealed and secret.role != SecretType.MURDERER
            for secret in all_secrets
        )

        return not unrevealed_secrets

    def end_game(
        self,
        game_id: UUID, 
        reason: GameEndReason
    ) -> EndGameResult:
        game = (
            self.db.query(Game)
            .options(joinedload(Game.players), joinedload(Game.secrets))
            .filter(Game.id == game_id)
            .first()
        )
        if not game:
            raise ValueError(f"Game {game_id} not found")
        
        murderer_id = None
        accomplice_id = None
        player_roles_map = {p.id: PlayerRole.DETECTIVE for p in game.players}

        for secret in game.secrets:
            if secret.owner_player_id:
                if secret.role == SecretType.ACCOMPLICE:
                    accomplice_id = secret.owner_player_id
                    player_roles_map[secret.owner_player_id] = PlayerRole.ACCOMPLICE
                elif secret.role == SecretType.MURDERER:
                    murderer_id = secret.owner_player_id
                    player_roles_map[secret.owner_player_id] = PlayerRole.MURDERER

        winning_team: WinningTeam
        winners_ids: set[UUID] = set()

        if reason in [GameEndReason.DECK_EMPTY, GameEndReason.SECRETS_REVEALED]:
            winning_team = WinningTeam.MURDERERS
            if murderer_id:
                winners_ids.add(murderer_id)
            if accomplice_id:
                winners_ids.add(accomplice_id)
        
        elif reason == GameEndReason.MURDERER_REVEALED:
            winning_team = WinningTeam.DETECTIVES
            all_players_ids = {p.id for p in game.players}
            murderer_team_ids = {murderer_id, accomplice_id}
            winners_ids = all_players_ids - murderer_team_ids

        else:
            raise ValueError(f"Invalid game end reason: {reason}")

        winners_list = [
            PlayerSummary(
                id=p.id,
                name=p.name
            )
            for p in game.players
            if p.id in winners_ids
        ]
        
        player_roles_list = [
            PlayerRoleInfo(
                id = p.id,
                name = p.name,
                role = player_roles_map[p.id]
            )
            for p in game.players
        ]

        result_dto = EndGameResult(
            reason = reason,
            winning_team = winning_team,
            winners = winners_list,
            player_roles = player_roles_list
        )

        return result_dto
    
    def get_turn_state(self, game_id: UUID) -> GameTurnStateOut:
        turn_state = (
            self.db.query(GameTurnState)
            .filter(GameTurnState.game_id == game_id)
            .first()
        )
        if not turn_state:
            raise ValueError("No existe estado de turno para este juego")

        return GameTurnStateOut(
            turn_state=turn_state.state,
            target_player_id=turn_state.target_player_id,
            current_event_card_id=turn_state.current_event_card_id,
            card_trade_offered_card_id=turn_state.card_trade_offered_card_id,
            passing_direction=turn_state.passing_direction,
            is_cancelled=turn_state.is_canceled_card,
            last_is_canceled_card=turn_state.last_is_canceled_card,
            vote_data=turn_state.vote_data,
            sfp_players=[str(p) for p in turn_state.sfp_players] if turn_state.sfp_players else None
        )
    
    def change_turn_state(
            self, 
            game_id: UUID, 
            new_state: TurnState, 
            target_player_id: UUID | None = None,
            passing_direction: str | None = None,
            current_event_card_id: UUID | None = None,
            card_trade_offered_card_id: UUID | None = None,
            is_cancelled: bool | None = None
    ):

        game_obj = self.db.query(Game).filter(Game.id == game_id).first()
        if not game_obj: 
            raise ValueError("Juego no encontrado")
        if not game_obj.turn_state:
            raise ValueError("Estado de la partida no encontrado")
        
        game_obj.turn_state.state = new_state

        if new_state in {
            TurnState.CHOOSING_SECRET, 
            TurnState.CHOOSING_SECRET_PYS,
            TurnState.CARD_TRADE_PENDING
        }:
            if not target_player_id:
                raise ValueError("Se requiere target_played_id")
            game_obj.turn_state.target_player_id = target_player_id
        else :
            game_obj.turn_state.target_player_id = None

        if new_state == TurnState.PASSING_CARDS:
            # Si entramos en este estado, exigimos los nuevos campos
            if not passing_direction or not current_event_card_id:
                raise ValueError("Se requieren passing_direction y current_event_card_id para este estado")
            game_obj.turn_state.passing_direction = passing_direction
            game_obj.turn_state.current_event_card_id = current_event_card_id
            game_obj.turn_state.card_trade_offered_card_id = None
            game_obj.turn_state.vote_data = None

        elif new_state == TurnState.CARD_TRADE_PENDING:
            required_fields = [
                current_event_card_id,
                card_trade_offered_card_id,
            ]
            if any(value is None for value in required_fields):
                raise ValueError("Se requieren datos de Card Trade para este estado")
            game_obj.turn_state.passing_direction = None
            game_obj.turn_state.current_event_card_id = current_event_card_id
            game_obj.turn_state.card_trade_offered_card_id = card_trade_offered_card_id

        elif new_state == TurnState.CANCELLED_CARD_PENDING:
            if is_cancelled == None:
                raise ValueError("Se requiere is_cancelled")
            game_obj.turn_state.is_canceled_card = is_cancelled
            if game_obj.turn_state.last_is_canceled_card == None:
                game_obj.turn_state.last_is_canceled_card = is_cancelled

        elif new_state == TurnState.VOTING:
            game_obj.turn_state.vote_data = {} 

            if not current_event_card_id:
                 raise ValueError("Se requiere current_event_card_id para el estado VOTING")
            game_obj.turn_state.current_event_card_id = current_event_card_id
        
        elif new_state == TurnState.PENDING_DEVIOUS:
            if target_player_id is None:
                raise ValueError("Se requiere target_player_id")
            if game_obj.turn_state.sfp_players is None:
                game_obj.turn_state.sfp_players = []        
            game_obj.turn_state.sfp_players = game_obj.turn_state.sfp_players + [str(target_player_id)]

        else:
            # Limpia estos campos si estamos en cualquier OTRO estado
            game_obj.turn_state.passing_direction = None
            game_obj.turn_state.current_event_card_id = None
            game_obj.turn_state.card_trade_offered_card_id = None
            game_obj.turn_state.is_canceled_card = None
            game_obj.turn_state.last_is_canceled_card = None
            game_obj.turn_state.vote_data = None
            game_obj.turn_state.sfp_players = []

        self.db.commit()
    
    def submit_player_vote(
        self,
        game_id: UUID,
        voter_player_id: UUID,
        voted_player_id: UUID
    ):
        """
        Registra el voto de un jugador en el GameTurnState.
        """
        turn_state = self.get_turn_state_entity(game_id)
        
        if not turn_state or turn_state.state != TurnState.VOTING:
            raise HTTPException(status_code=403, detail="Not in a voting phase")

        if voter_player_id == voted_player_id:
            raise HTTPException(status_code=400, detail="Cannot vote for oneself")

        current_votes = turn_state.vote_data if turn_state.vote_data is not None else {}

        if str(voter_player_id) in current_votes: 
            raise HTTPException(status_code=403, detail="Player has already voted")

        # Añadimos el nuevo voto
        current_votes[str(voter_player_id)] = str(voted_player_id)
        turn_state.vote_data = current_votes
        
        flag_modified(turn_state, "vote_data")

        self.db.commit()

    async def handler_end_timer(
            self,
            game_id: UUID
    ):
        game_obj = self.db.query(Game).filter(Game.id == game_id).first()
        current_turn=game_obj.current_turn
        if game_obj.turn_state.state != TurnState.END_TURN:
            self.handle_end_timer_normal_state(
                game_id,
                current_turn
            )

            self.change_turn_state(game_id,TurnState.END_TURN)
        result: Union[UUID, EndGameResult] = self.next_player(game_id)

        if isinstance(result, EndGameResult):
            await manager.broadcast_to_game(
                game_id, {"type": "gameEnded", "data": jsonable_encoder(result)}
            )
            turn_timer_manager.stop_timer(game_id)
        

        else:
            id_next_player = result
            await manager.broadcast_to_game(
                game_id, {"type" : "endTimer", 
                          "data": {
                              "player_id":str(current_turn)
                          }}
            )
    
            await manager.broadcast_to_game(
                game_id, {"type" : "turnChange", 
                          "data": {
                              "player_id":str(id_next_player)
                          }}
            )
            
    def handle_end_timer_normal_state(
            self, 
            game_id: UUID, 
            player_id: UUID
    ): 
        
        hand_player = CardService.count_player_hand(
            self.db,
            game_id,
            player_id
        )

        if hand_player == 6:
            cards_player = CardService.get_cards_by_owner(
                self.db,
                game_id,
                CardOwner.PLAYER,
                player_id
            )


            move_in = CardMoveIn(to_owner=CardOwner.DISCARD_PILE)
            CardService.move_card(self.db,
                                  cards_player[0].id,
                                  move_in)
            CardService.moveDeckToPlayer(
                self.db,
                game_id,
                player_id,
                1)
        else:
            CardService.moveDeckToPlayer(
                self.db,
                game_id,
                player_id,
                (6-hand_player)
            )


    def get_player_neighbors(
        self, 
        game_id: UUID, 
        player_id: UUID
    ) -> tuple[Optional[Player], Optional[Player]]:
        """
        Encuentra al jugador anterior y al siguiente en el orden de turno (por UUID).
        Devuelve una tupla (previous_player, next_player).
        """
        players = self.db.query(Player).filter(Player.game_id == game_id).all()

        if not players or len(players) < 2:
            return (None, None)

        sorted_players = sorted(players, key=lambda p: p.id)

        # Encontrar el índice del jugador actual
        current_index = -1
        for i, p in enumerate(sorted_players):
            if p.id == player_id:
                current_index = i
                break
        
        if current_index == -1:
            # El jugador que lo pide no está en el juego
            return (None, None)

        num_players = len(sorted_players)
        
        prev_index = (current_index - 1 + num_players) % num_players
        
        next_index = (current_index + 1) % num_players

        return (sorted_players[prev_index], sorted_players[next_index])


    def remove_player(self, game_id: UUID, player_id: UUID) -> bool:
        """
        Remueve un jugador de una partida que no ha comenzado.
        Si el jugador es el host, elimina la partida completa.
        Devuelve True si la operación fue exitosa (jugador removido o juego eliminado).
        """
        game = self.db.query(Game).filter(Game.id == game_id).first()
        player_service = PlayerService(self.db)
        
        # Obtenemos la entidad del jugador
        player_to_remove = player_service.get_player_entity_by_id(player_id)

        if not game or not player_to_remove or player_to_remove.game_id != game_id:
            # Juego no encontrado, jugador no encontrado, o jugador no está en este juego
            return False

        if game.ready:
            # No se permite abandonar una partida ya iniciada 
            return False 

        if game.host_id == player_id:
            self.db.delete(game)
            self.db.commit()
            return True
        else:
            self.db.delete(player_to_remove) 

            self.db.commit()
            return True
    