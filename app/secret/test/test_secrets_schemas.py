import pytest
from uuid import uuid4, UUID
from pydantic import ValidationError
from app.secret.schemas import SecretOut, SecretMove, SecretQuery
from app.secret.enums import SecretType


def _any_secret_type():
    members = list(SecretType)
    if not members:
        pytest.skip("SecretType has no members", allow_module_level=True)
    return members[0]


# =====================
# SecretOut
# =====================
def test_secret_out_accepts_uuid_and_required_fields():
    sid, gid, pid = uuid4(), uuid4(), uuid4()
    stype = _any_secret_type()

    s = SecretOut(
        id=sid,
        game_id=gid,
        type=stype,
        name="Nombre",
        description="Desc",
        owner_player_id=pid,
        revealed=True,
    )

    assert isinstance(s.id, UUID)
    assert s.game_id == gid
    assert s.owner_player_id == pid
    assert s.revealed is True
    assert s.type == stype


def test_secret_out_requires_revealed():
    sid, gid = uuid4(), uuid4()
    stype = _any_secret_type()

    with pytest.raises(ValidationError):
        # Falta el campo "revealed"
        SecretOut(
            id=sid,
            game_id=gid,
            type=stype,
            name="n",
            description="d",
            owner_player_id=None
        )


def test_secret_out_allows_player_none():
    sid, gid = uuid4(), uuid4()
    stype = _any_secret_type()

    s = SecretOut(
        id=sid,
        game_id=gid,
        type=stype,
        name="n",
        description="d",
        owner_player_id=None,
        revealed=False
    )

    assert s.owner_player_id is None
    assert s.revealed is False


# =====================
# SecretMove
# =====================
def test_secret_move_requires_all_fields():
    gid, secret, fromp, top = uuid4(), uuid4(), uuid4(), uuid4()

    sm = SecretMove(
        game_id=gid,
        secret_id=secret,
        from_player=fromp,
        to_player=top
    )

    assert sm.game_id == gid
    assert sm.secret_id == secret
    assert sm.from_player == fromp
    assert sm.to_player == top

    with pytest.raises(ValidationError):
        # Falta to_player
        SecretMove(
            game_id=gid,
            secret_id=secret,
            from_player=fromp
        )


# =====================
# SecretQuery
# =====================
def test_secret_query_accepts_valid_combinations():
    gid = uuid4()

    # Solo player_id
    sq2 = SecretQuery(game_id=gid, player_id=uuid4(), secret_id=None)
    assert sq2.player_id is not None
    assert sq2.secret_id is None

    # Ambos presentes
    sq3 = SecretQuery(game_id=gid, player_id=uuid4(), secret_id=uuid4())
    assert sq3.player_id is not None
    assert sq3.secret_id is not None


def test_secret_query_requires_player_id():
    gid = uuid4()
    with pytest.raises(ValidationError):
        SecretQuery(game_id=gid, player_id=None, secret_id=None)

    with pytest.raises(ValidationError):
        SecretQuery(game_id=gid, player_id=None, secret_id=uuid4())

def test_secret_query_invalid_uuid_strings_raise():
    with pytest.raises(ValidationError):
        SecretQuery(game_id="not-a-uuid", player_id=uuid4())

    with pytest.raises(ValidationError):
        SecretQuery(game_id=uuid4(), player_id="not-a-uuid")
