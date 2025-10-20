import uuid
from uuid import UUID

from typing import List, Optional

from sqlalchemy.orm import Session

from app.player.dtos import PlayerInDTO, PlayerOutDTO
from app.player.models import Player

class PlayerService:
    def __init__(self, db: Session):
        self.db = db

    def get_players(self) -> List[PlayerOutDTO]:
        """Devuelve todos los jugadores"""
        players = self.db.query(Player).all()

        players_dto = [
            PlayerOutDTO(
                id=player.id,
                name=player.name,
                birthday=player.birthday,
                game_id=None # player.game_id if player.game_id else None -> Aún no implementado en el modelo
            )
            for player in players
        ]   
        return players_dto

    
    def get_player_by_id(self, player_id: UUID) -> PlayerOutDTO | None:
        """Devuelve un jugador por su ID"""
        player = self.db.query(Player).filter(Player.id == player_id).first()
        if not player:
            return None
        return PlayerOutDTO(
            id=player.id,
            name=player.name,
            birthday=player.birthday,
            game_id= None # player.game_id if player.game_id else None -> Aún no implementado en el modelo
        )
    
    def get_player_entity_by_id(self, player_id: UUID) -> Optional[Player]:
        """Devuelve la entidad Player por su ID (uso interno)"""
        return self.db.query(Player).filter(Player.id == player_id).first()

    def get_players_by_game_id(self, game_id: UUID) -> list[Player]:
        """Devuelve la lista de jugadores de un juego por game_id"""
        return self.db.query(Player).filter(Player.game_id == game_id).all()
    
    def create_player(self, player_data: PlayerInDTO) -> PlayerOutDTO:
        """Crea un nuevo jugador"""
        new_player = Player(
            id= uuid.uuid4(),
            name=player_data.name, 
            birthday=player_data.birthday
        )
        self.db.add(new_player)
        try:
            self.db.commit()
        except Exception as e:
            self.db.rollback()
            raise e
        self.db.refresh(new_player)
        return PlayerOutDTO(
            id=new_player.id,
            name=new_player.name,
            birthday=new_player.birthday,
            game_id = None
        )
    
    def assign_game_to_player(self, player_id: UUID, game_id: UUID) -> PlayerOutDTO:
        """Asigna un juego a un jugador"""
        player = self.db.query(Player).filter(Player.id == player_id).first()
        if not player:
            raise ValueError("Player not found")
        player.game_id = game_id
        try:
            self.db.commit()
        except Exception as e:
            self.db.rollback()
            raise e
        self.db.refresh(player)
        return PlayerOutDTO(
            id=player.id,
            name=player.name,
            birthday=player.birthday,
            game_id=game_id
        )
    
    def delete_player(self, player_id: UUID) -> UUID:
        """Elimina un jugador por su ID"""
        player = self.db.query(Player).filter(Player.id == player_id).first()
        if not player:
            raise ValueError("Player not found")
        self.db.delete(player)
        try:
            self.db.commit()
        except Exception as e:
            self.db.rollback()
            raise e
        return player_id