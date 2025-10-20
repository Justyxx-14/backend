from fastapi import HTTPException
from starlette.status import HTTP_400_BAD_REQUEST, HTTP_404_NOT_FOUND

ERR_PLAYER_ID_REQUIRED = "player_id is required for this query."
ERR_SET_NOT_FOUND = "Set with id '{set_id}' not found."
ERR_SET_GAME_MISMATCH = "Set '{set_id}' does not belong to game '{game_id}'."
ERR_SET_OWNER_MISMATCH = "Set '{set_id}' is not owned by player '{player_id}'."


class SetPlayerRequired(HTTPException):
    def __init__(self):
        super().__init__(status_code=HTTP_400_BAD_REQUEST, detail=ERR_PLAYER_ID_REQUIRED)


class SetNotFound(HTTPException):
    def __init__(self, set_id: str):
        super().__init__(
            status_code=HTTP_404_NOT_FOUND,
            detail=ERR_SET_NOT_FOUND.format(set_id=set_id),
        )


class SetGameMismatch(HTTPException):
    def __init__(self, set_id: str, game_id: str):
        super().__init__(
            status_code=HTTP_404_NOT_FOUND,
            detail=ERR_SET_GAME_MISMATCH.format(set_id=set_id, game_id=game_id),
        )


class SetOwnerMismatch(HTTPException):
    def __init__(self, set_id: str, player_id: str):
        super().__init__(
            status_code=HTTP_404_NOT_FOUND,
            detail=ERR_SET_OWNER_MISMATCH.format(set_id=set_id, player_id=player_id),
        )
