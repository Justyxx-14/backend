from datetime import date
from typing import List, Optional, Union
from uuid import UUID
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func
from pathlib import Path
import json

from app.game.models import Game
from app.game.dtos import GameInDTO, GameOutDTO
from app.game.schemas import EndGameResult, PlayerSummary, PlayerRoleInfo
from app.game.enums import GameEndReason, WinningTeam, PlayerRole
from app.player.service import PlayerService
from app.player.dtos import PlayerInDTO
from app.player.models import Player
from app.card.enums import CardOwner
from app.card.schemas import CardIn, CardBatchIn
from app.card.service import CardService
from app.secret.service import SecretService
from app.secret.models import Secrets
from app.secret.enums import SecretType



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
            host_id=game.host_id,
            min_players=game.min_players,
            max_players=game.max_players,
            ready=game.ready,
            players_ids=[p.id for p in game.players],
        )

    def create_game(self, game_data: GameInDTO) -> GameOutDTO:
        player_service = PlayerService(self.db)
        host_dto = PlayerInDTO(name=game_data.host_name, birthday=game_data.birthday)
        host = player_service.create_player(host_dto)

        new_game = Game(
            name=game_data.name,
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