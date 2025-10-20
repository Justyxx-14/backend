from enum import Enum

class GameEndReason(str, Enum):
    DECK_EMPTY = "DECK_EMPTY"
    SECRETS_REVEALED = "SECRETS_REVEALED"
    MURDERER_REVEALED = "MURDERER_REVEALED"

class WinningTeam(str, Enum):
    MURDERERS = "MURDERERS"
    DETECTIVES = "DETECTIVES"

class PlayerRole(str, Enum):
    MURDERER = "MURDERER"
    ACCOMPLICE = "ACCOMPLICE"
    DETECTIVE = "DETECTIVE"

