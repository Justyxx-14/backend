from enum import Enum

class CardType(str, Enum):
    DETECTIVE = "DETECTIVE"
    EVENT = "EVENT"
    DEVIOUS = "DEVIOUS" 

class CardOwner(str, Enum):
    PLAYER = "PLAYER"
    DECK = "DECK"
    DISCARD_PILE = "DISCARD_PILE"
    DRAFT = "DRAFT"
    OUT_OFF_THE_GAME = "OUT_OFF_THE_GAME"
    SET = "SET"
