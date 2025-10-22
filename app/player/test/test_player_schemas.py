# tests/test_player_dtos.py
from datetime import date, datetime
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

# Ajustá los imports si tus clases están en otra ruta
from app.player.schemas import PlayerIn, PlayerOut
from app.player.dtos import PlayerInDTO, PlayerOutDTO


def test_playerin_valid_construction_and_to_dto():
    p = PlayerIn(name="Juan Pérez", birthday=date(2000, 1, 1))
    assert p.name == "Juan Pérez"
    assert p.birthday == date(2000, 1, 1)

    dto = p.to_dto()
    assert isinstance(dto, PlayerInDTO)
    assert dto.name == p.name
    assert dto.birthday == p.birthday


def test_playerin_parses_iso_date_string():
    p = PlayerIn(name="Ana", birthday="1999-12-31")
    assert p.birthday == date(1999, 12, 31)


def test_playerin_invalid_date_string_raises():
    with pytest.raises(ValidationError):
        PlayerIn(name="Ana", birthday="not-a-date")


def test_playerin_empty_name_rejected():
    with pytest.raises(ValidationError):
        PlayerIn(name="", birthday=date(2000, 1, 1))


def test_playerin_whitespace_name_is_allowed_by_min_length():
    # min_length=1 acepta cadenas con espacios; si querés otro comportamiento, hacé un validator.
    p = PlayerIn(name="   ", birthday=date(2000, 1, 1))
    assert p.name == "   "
    dto = p.to_dto()
    assert dto.name == "   "


def test_playerin_missing_fields_raise():
    with pytest.raises(ValidationError):
        PlayerIn(name="SoloNombre")  # falta birthday

    with pytest.raises(ValidationError):
        PlayerIn(birthday="2000-01-01")  # falta name


def test_playerout_valid_uuid_objects_and_to_dto():
    id_ = uuid4()
    game_id = uuid4()
    p = PlayerOut(id=id_, name="Jugador", birthday=date(1990, 2, 3), game_id=game_id, social_disgrace=True)

    assert isinstance(p.id, UUID)
    assert isinstance(p.game_id, UUID)
    assert p.social_disgrace is True

    dto = p.to_dto()
    assert isinstance(dto, PlayerOutDTO)
    assert dto.id == p.id
    assert dto.name == p.name
    assert dto.birthday == p.birthday
    assert dto.game_id == p.game_id
    assert dto.social_disgrace is True


def test_playerout_accepts_uuid_strings_and_parses_them():
    id_str = str(uuid4())
    game_id_str = str(uuid4())
    p = PlayerOut(id=id_str, name="Otro", birthday="1985-05-05", game_id=game_id_str)

    assert isinstance(p.id, UUID)
    assert str(p.id) == id_str

    assert isinstance(p.game_id, UUID)
    assert str(p.game_id) == game_id_str
    assert p.social_disgrace is False

    dto = p.to_dto()
    assert dto.id == p.id
    assert dto.game_id == p.game_id
    assert dto.social_disgrace is False


def test_playerout_invalid_uuid_raises():
    with pytest.raises(ValidationError):
        PlayerOut(id="not-a-uuid", name="X", birthday="1990-01-01")


def test_playerout_name_length_limits():
    # exactamente 40 caracteres -> OK
    name_40 = "a" * 40
    p = PlayerOut(id=uuid4(), name=name_40, birthday=date(1995, 6, 7))
    assert p.name == name_40

    # 41 caracteres -> falla por max_length=40
    name_41 = "a" * 41
    with pytest.raises(ValidationError):
        PlayerOut(id=uuid4(), name=name_41, birthday=date(1995, 6, 7))



