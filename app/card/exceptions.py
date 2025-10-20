from fastapi import HTTPException, status
from starlette.status import (
    HTTP_404_NOT_FOUND,
    HTTP_400_BAD_REQUEST,
    HTTP_500_INTERNAL_SERVER_ERROR,
)

ERR_CARD_NOT_FOUND = "Card with id '{card_id}' not found."
ERR_CARD_ID_MISMATCH = (
    "The card ID in the path and the card ID in the request body do not match."
)
ERR_GAME_NOT_FOUND = "Game with id '{game_id}' not found."
ERR_DB_COMMIT = "A database error occurred while committing changes."
ERR_CARD_GAME_MISMATCH = "Card '{card_id}' does not belong to game '{game_id}'."
ERR_CARDS_NOT_FOUND_OR_INVALID = "One or more cards do not exist or do not belong to the player in this game."
ERR_NO_CARDS = "No hay cartas disponibles en el juego '{game_id}'."
ERR_NO_PLAYER = "Player_id no fue enviado"
INV_AMOUNT = "Cantidad de cartas invalido"
ERR_SECRET_NOT_FOUND_OR_INVALID = (
    "The secret with id '{secret_id}' was not found, is not revealed, or does not belong to the game '{game_id}'."
)

class CardNotFoundException(HTTPException):
    def __init__(self, card_id: str):
        super().__init__(
            status_code=HTTP_404_NOT_FOUND,
            detail=ERR_CARD_NOT_FOUND.format(card_id=card_id)
        )

class CardIdMismatchException(HTTPException):
    def __init__(self):
        super().__init__(
            status_code=HTTP_400_BAD_REQUEST,
            detail=ERR_CARD_ID_MISMATCH
        )


class GameNotFoundException(HTTPException):
    def __init__(self, game_id: str):
        super().__init__(
            status_code=HTTP_404_NOT_FOUND,
            detail=ERR_GAME_NOT_FOUND.format(game_id=game_id)
        )

class DatabaseCommitException(HTTPException):
    def __init__(self):
        super().__init__(
            status_code=HTTP_500_INTERNAL_SERVER_ERROR,
            detail=ERR_DB_COMMIT
        )

class CardGameMismatchException(HTTPException):
    def __init__(self, card_id: str, game_id: str):
        super().__init__(
            status_code=HTTP_404_NOT_FOUND,
            detail=ERR_CARD_GAME_MISMATCH.format(card_id=card_id, game_id=game_id)
        )

class CardsNotFoundOrInvalidException(HTTPException):
    def __init__(self,detail = ERR_CARDS_NOT_FOUND_OR_INVALID):
        super().__init__(
            status_code=HTTP_404_NOT_FOUND,
            detail=detail
        )

class PlayerHandLimitExceededException(HTTPException):
    def __init__(self, detail="El jugador no puede tener más de 6 cartas en la mano."):
        super().__init__(
            status_code=409,
            detail=detail
        )

class InvalidAmountOfCards(HTTPException):
    def __init__(self):
        super().__init__(
            status_code=HTTP_400_BAD_REQUEST,
            detail=INV_AMOUNT
        )

class NoCardsException(HTTPException):
    def __init__(self, game_id: str):
        super().__init__(
            status_code=HTTP_404_NOT_FOUND,
            detail=ERR_NO_CARDS.format(game_id=game_id)
        )

class PlayerNotIncluyedExcepcion(HTTPException):
    def __init__(self):
        super().__init__(
            status_code=HTTP_400_BAD_REQUEST,
            detail=ERR_NO_PLAYER
        )

class SecretNotFoundOrInvalidException(HTTPException):
    def __init__(self, secret_id: str, game_id: str):
        super().__init__(
            status_code=HTTP_404_NOT_FOUND,
            detail=ERR_SECRET_NOT_FOUND_OR_INVALID.format(secret_id=secret_id, game_id=game_id)
        )