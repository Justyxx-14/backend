import pytest
from datetime import date
from uuid import uuid4
from pydantic import ValidationError

from app.game.schemas import GameIn, GameOut


def test_game_in_allows_valid_payload():
    host_id = uuid4()
    payload = GameIn(
        name="Valid name",
        host=host_id,
        birthday=date(2000, 1, 1),
        min_players=2,
        max_players=4,
    )

    assert payload.host == host_id
    assert payload.min_players == 2
    assert payload.max_players == 4


def test_game_in_to_dto_requires_host_name_field():
    payload = GameIn(
        name="Missing host name",
        host=uuid4(),
        birthday=date(2000, 1, 1),
        min_players=2,
        max_players=4,
    )

    with pytest.raises(ValidationError):
        payload.to_dto()


def test_game_in_rejects_max_lower_than_min():
    with pytest.raises(ValidationError):
        GameIn(
            name="Bad limits",
            host=uuid4(),
            birthday=date(2000, 1, 1),
            min_players=5,
            max_players=3,
        )


def test_game_out_inserts_host_when_missing():
    host_id = uuid4()
    another_player = uuid4()

    schema = GameOut(
        id=uuid4(),
        name="Lobby match",
        host_id=host_id,
        min_players=2,
        max_players=4,
        player_ids=[another_player],
    )

    assert schema.player_ids[0] == host_id
    assert schema.player_ids[1] == another_player

    dto = schema.to_dto()
    assert dto.players_ids[0] == host_id
    assert another_player in dto.players_ids


def test_game_out_does_not_duplicate_host_when_present():
    host_id = uuid4()
    extra_player = uuid4()

    schema = GameOut(
        id=uuid4(),
        name="Host already present",
        host_id=host_id,
        min_players=2,
        max_players=4,
        player_ids=[host_id, extra_player],
    )

    assert schema.player_ids.count(host_id) == 1
    assert schema.player_ids[0] == host_id


def test_game_out_rejects_invalid_limits():
    host_id = uuid4()

    with pytest.raises(ValidationError):
        GameOut(
            id=uuid4(),
            name="Invalid limits",
            host_id=host_id,
            min_players=4,
            max_players=2,
        )
