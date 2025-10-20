from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db import get_db
from . import schemas
from .service import SecretService
from .exceptions import (
            SecretNotFound,
            SecretGameMismatch,
            SecretOwnerMismatch,
            SecretAndPlayerRequired
            )
from .enums import SecretType

# ----------------- helper -----------------

def _to_out(dto) -> schemas.SecretOut:
    return schemas.SecretOut(
        id=dto.id,
        game_id=dto.game_id,
        type=dto.role,
        name=dto.name,
        description=dto.description,
        owner_player_id=getattr(dto, "owner_player_id", None),
        revealed=dto.revealed,
    )# =========================
# /secrets
# =========================

secret_router = APIRouter(prefix="/secrets", tags=["secrets"])


# ---------------------------
# GET /secrets
# ---------------------------
@secret_router.get("", response_model=List[schemas.SecretOut])
def query_secrets(
    game_id: UUID = Query(..., description="ID de la partida"),
    player_id: UUID | None = Query(None, description="Filtra por dueño del secreto"),
    secret_id: UUID | None = Query(None, description="ID de un secreto específico"),
    db: Session = Depends(get_db)
    ):
    """
    Permite obtener los secretos de un juego vía query params.
    Reglas:
    - Si solo se pasa player_id sin secret_id => devuelve todos los secretos del jugador.
    - Si además se pasa secret_id con el player_id => devuelve el secreto específico.
    """
    # Evitamos listar todo el juego
    if not secret_id and not player_id:
        raise SecretAndPlayerRequired()

    if secret_id:
        if player_id is None:
            raise SecretAndPlayerRequired()
        dto = SecretService.get_secret_by_id(db, secret_id)
        if not dto:
            raise SecretNotFound(str(secret_id))
        if dto.game_id != game_id:
            raise SecretGameMismatch(str(secret_id), str(game_id))
        if dto.owner_player_id != player_id:
            raise SecretOwnerMismatch(str(secret_id), str(player_id))
        return [_to_out(dto)]


    items = SecretService.get_secrets_by_player_id(db, player_id)
    items = [x for x in items if x.game_id == game_id]
    return [_to_out(x) for x in items]
