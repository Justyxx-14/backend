from uuid import uuid4

from app.set.enums import SetType
from app.set.models import Set


def test_set_model_accepts_enum_type():
    set_id = uuid4()
    game_id = uuid4()
    owner_id = uuid4()

    model = Set(id=set_id, game_id=game_id, type=SetType.MS, owner_player_id=owner_id)

    assert model.id == set_id
    assert model.game_id == game_id
    assert model.owner_player_id == owner_id
    assert model.type is SetType.MS
