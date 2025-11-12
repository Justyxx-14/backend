import json
import pytest
from app.card.enums import CardOwner, CardType

def test_cardowner_members_and_values():
    """CardOwner expone exactamente los dueños esperados del dominio."""
    names = {m.name for m in CardOwner}
    values = {m.value for m in CardOwner}
    assert names == {"DECK", "DISCARD_PILE", "PLAYER", "DRAFT", "OUT_OFF_THE_GAME", "SET", "PASSING"}
    assert values == {"DECK", "DISCARD_PILE", "PLAYER", "DRAFT", "OUT_OFF_THE_GAME", "SET", "PASSING"}

def test_cardtype_has_event_at_least():
    """CardType incluye al menos EVENT (pueden existir otros tipos)."""
    names = {m.name for m in CardType}
    values = {m.value for m in CardType}
    assert "EVENT" in names
    assert "EVENT" in values

def test_enums_are_strenum_like():
    """Los enums deben comportarse como strings (StrEnum): casteo y comparación."""
    # instancia y tipo
    assert isinstance(CardOwner.DECK, CardOwner)
    assert isinstance(CardOwner.DECK, str)  # se comporta como str
    # comparación directa con string
    assert CardOwner.DECK == "DECK"
    # casteo desde string válido
    assert CardOwner("PLAYER") is CardOwner.PLAYER

    with pytest.raises(ValueError):
        CardOwner("NOT_A_VALID_OWNER")

def test_json_serialization_roundtrip():
    """Serialización a JSON conserva los valores string del enum."""
    payload = {
        "owner": CardOwner.DISCARD_PILE,
        "type": CardType.EVENT,
    }
    s = json.dumps(payload)
    data = json.loads(s)
    assert data["owner"] == "DISCARD_PILE"
    assert data["type"] == "EVENT"

def test_pydantic_integration_validation():
    """Integración mínima con Pydantic: valida y serializa a string."""
    from pydantic import BaseModel, ValidationError

    class M(BaseModel):
        owner: CardOwner
        type: CardType

    # válido
    m = M(owner="PLAYER", type="EVENT")
    assert m.owner is CardOwner.PLAYER
    assert m.type is CardType.EVENT
    # json salen strings
    d = m.model_dump()
    assert d == {"owner": "PLAYER", "type": "EVENT"}

    # inválido
    with pytest.raises(ValidationError):
        M(owner="SOMEWHERE", type="EVENT")
    with pytest.raises(ValidationError):
        M(owner="PLAYER", type="NOT_A_TYPE")

