import json
import uuid
import random
from uuid import UUID
from typing import List
from sqlalchemy.orm import Session
from . import models
from pathlib import Path
from .enums import SecretType
from .dtos import SecretInDTO, SecretOutDTO
from app.player.service import PlayerService
from app.secret.schemas import SecretOut


class SecretService:

    @staticmethod
    def _to_dto(secret: models.Secrets) -> SecretOut:
        return SecretOut(
            id=secret.id,
            name=secret.name,
            role=secret.role,
            description=secret.description,
            owner_player_id=secret.owner_player_id,
            revealed=secret.revealed,
            game_id=secret.game_id
        )
    
    @staticmethod
    def create_secrets(
        db: Session, 
        game_id: UUID, 
        jugadores_ids: list[UUID]
    ) -> List[SecretOutDTO]:
        """
        Crea todos los secretos de una partida, sin asignar dueño todavia
        """
        path = Path(__file__).parent.parent / "secret" / "secrets.json"

        if not path.exists():
            raise FileNotFoundError(f"No se encontró secrets.json en {path.resolve()}")

        with open(path, "r", encoding="utf-8") as f:
            deck_data = json.load(f)

        cards_per_player: int = 3
        
        # Clasificamos los secretos del JSON
        common_secrets_data = [s for s in deck_data["items"] if s["type"] == "COMMON"]
        murderer_secret_data = next(s for s in deck_data["items"] if s["type"] == "MURDERER")
        accomplice_secret_data = next(s for s in deck_data["items"] if s["type"] == "ACCOMPLICE")

        # Determinar cuántos COMMON hacen falta
        num_players = len(jugadores_ids)
        total_common_needed = cards_per_player * num_players - 1  # restamos 1 por el MURDERER
        include_accomplice = num_players >= 5
        if include_accomplice:
            total_common_needed -= 1  # restamos también el ACCOMPLICE

        if total_common_needed > len([s for s in deck_data["items"] if s["type"] == "COMMON"]):
            raise ValueError(f"Not enough COMMON secrets in the deck. Needed {total_common_needed}, available {common_secrets_data}")

        # Elegir los COMMON aleatoriamente
        sample_common = random.sample(common_secrets_data, total_common_needed)

        # Combinar todos los secretos que se van a crear
        selected_secrets_data = sample_common + [murderer_secret_data]
        if include_accomplice:
            selected_secrets_data.append(accomplice_secret_data)

        # Crear e insertar en la base
        secrets_created: List[models.Secrets] = []
        for item in selected_secrets_data:
            secret_type = SecretType[item["type"]]
            secret = models.Secrets(
                id=uuid.uuid4(),
                game_id=game_id,
                name=item["name"],
                description=item.get("description", ""),
                role=secret_type,
                owner_player_id=None,
                revealed=False
            )
            db.add(secret)
            secrets_created.append(secret)

        db.commit()
        # Refrescar los objetos para obtener su estado actualizado
        for secret in secrets_created:
            db.refresh(secret)

        return [SecretService._to_dto(secret) for secret in secrets_created]
    
    @staticmethod
    def get_secret_by_id(db: Session, secret_id: UUID) -> SecretOutDTO | None:
        secret = db.query(models.Secrets).filter(models.Secrets.id == secret_id).first()
        return SecretService._to_dto(secret) if secret else None
    
    @staticmethod
    def get_secrets_by_game_id(db: Session, game_id: UUID) -> List[SecretOutDTO]:
        secret = db.query(models.Secrets).filter(models.Secrets.game_id == game_id).all()
        return [SecretService._to_dto(secret) for secret in secret]
    
    @staticmethod
    def get_secrets_by_player_id(db: Session, player_id: UUID) -> List[SecretOutDTO]:
        secret = db.query(models.Secrets).filter(models.Secrets.owner_player_id == player_id).all()
        return [SecretService._to_dto(secret) for secret in secret]
    
    @staticmethod
    def deal_secrets(
        db: Session, 
        game_id: UUID, 
        jugadores_ids: list[UUID], 
    ) -> dict[UUID, List[SecretOutDTO]]:
        """
        Reparte secretos según las reglas:
        - Cada jugador tiene 3 secretos
        - Siempre hay un MURDERER
        - ACCOMPLICE solo si hay 5 o 6 jugadores
        - El resto son COMMON
        """
        deck_secrets = db.query(models.Secrets).filter(
            models.Secrets.game_id == game_id
        ).all()

        if not deck_secrets:
            return {}
        
        # Comprobamos si el cómplice está en juego
        accomplice_in_game = any(s.role == SecretType.ACCOMPLICE for s in deck_secrets)
        
        # Diccionario temporal para comprobar la repartición valida
        tentative_hands = {}
        cards_per_player: int = 3

        # Bucle principal para reintentar la repartición si no es válida.
        while True:
            # Siempre mezclamos el mazo
            random.shuffle(deck_secrets)
            
            # Repartimos sin guardar en la DB.
            is_deal_valid = True
            for i, jugador_id in enumerate(jugadores_ids):
                jugador_secrets = deck_secrets[i * cards_per_player : (i + 1) * cards_per_player]
                tentative_hands[jugador_id] = jugador_secrets
            
            # Verificamos la repartición si el cómplice está en juego.
            if accomplice_in_game:
                for hand in tentative_hands.values():
                    # Usamos un set para una búsqueda eficiente de los roles en la mano.
                    hand_types = {secret.role for secret in hand}
                    if SecretType.MURDERER in hand_types and SecretType.ACCOMPLICE in hand_types:
                        is_deal_valid = False
                        break  # Si encontramos una mano inválida, no hace falta seguir revisando.
            
            # Si la repartición es válida, salimos del bucle.
            if is_deal_valid:
                break

        # Una vez encontrada una repartición válida, la guardamos en la DB.
        resultado: dict[UUID, List[SecretOutDTO]] = {pid: [] for pid in jugadores_ids}
        for jugador_id, secrets in tentative_hands.items():
            for secret in secrets:
                secret.owner_player_id = jugador_id
                db.add(secret)
            resultado[jugador_id] = [SecretService._to_dto(s) for s in secrets]

        db.commit()

        return resultado

    @staticmethod
    def change_secret_status(db: Session, secret_id: UUID) -> SecretOutDTO:
        # Llamo directamente a la DB cuando le tengo que modificar un campo
        secret = (db.query(models.Secrets)
                  .filter(models.Secrets.id == secret_id).first())
        if not secret:
            raise ValueError(f"Secret with id {secret_id} not found")
        
        secret.revealed = not secret.revealed
        db.flush()
        PlayerService.update_social_disgrace(db, secret.owner_player_id)
        db.commit()
        db.refresh(secret)
        
        return SecretService._to_dto(secret)

    @staticmethod
    def move_secret(db: Session, secret_id: UUID, new_player_id: UUID) -> SecretOutDTO:
        # Llamo directamente a la DB cuando le tengo que modificar un campo
        secret = (db.query(models.Secrets)
                  .filter(models.Secrets.id == secret_id).first())
        if not secret:
            raise ValueError(f"Secret with id {secret_id} not found")
        
        previous_owner = secret.owner_player_id
        secret.owner_player_id = new_player_id
        db.flush()
        PlayerService.update_social_disgrace(db, previous_owner)
        PlayerService.update_social_disgrace(db, new_player_id)
        db.commit()
        db.refresh(secret)

        return SecretService._to_dto(secret)

    @staticmethod
    def get_murderer_team_ids(db: Session, game_id: UUID) -> set[UUID]:
        """Devuelve un set de IDs de los jugadores del equipo asesino."""
        team_secrets = db.query(models.Secrets.owner_player_id).filter(
            models.Secrets.game_id == game_id,
            models.Secrets.role.in_([SecretType.MURDERER, SecretType.ACCOMPLICE])
        ).all()
        
        # Filtra Nones por si los secretos aún no están asignados
        return {row[0] for row in team_secrets if row[0] is not None}