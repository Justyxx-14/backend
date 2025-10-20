from enum import Enum

class SecretType(str, Enum):
    MURDERER = "MURDERER"
    ACCOMPLICE = "ACCOMPLICE"
    COMMON = "COMMON"