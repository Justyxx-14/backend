from fastapi import HTTPException
from starlette.status import (
    HTTP_404_NOT_FOUND,
    HTTP_400_BAD_REQUEST
)


# === Mensajes ===
ERR_SECRET_NOT_FOUND = "Secret with id '{secret_id}' not found."
ERR_SECRET_GAME_MISMATCH = "Secret '{secret_id}' does not belong to game '{game_id}'."
ERR_SECRET_OWNER_MISMATCH = "Secret '{secret_id}' is not owned by player '{player_id}'."
ERR_PLAYER_ID_REQUIRED= "player_id is required."

# === Excepciones HTTP ===
class SecretNotFound(HTTPException):
    def __init__(self, secret_id: str):
        super().__init__(
            status_code=HTTP_404_NOT_FOUND,
            detail=ERR_SECRET_NOT_FOUND.format(secret_id=secret_id),
        )


class SecretGameMismatch(HTTPException):
    def __init__(self, secret_id: str, game_id: str):
        super().__init__(
            status_code=HTTP_404_NOT_FOUND,
            detail=ERR_SECRET_GAME_MISMATCH.format(secret_id=secret_id, game_id=game_id),
        )


class SecretAndPlayerRequired(HTTPException):
    def __init__(self):
        super().__init__(status_code=HTTP_400_BAD_REQUEST, detail=ERR_PLAYER_ID_REQUIRED)


class SecretOwnerMismatch(HTTPException):
    def __init__(self, secret_id: str, player_id: str):
        super().__init__(status_code=HTTP_404_NOT_FOUND,
                         detail=ERR_SECRET_OWNER_MISMATCH.format(secret_id=secret_id, player_id=player_id))

