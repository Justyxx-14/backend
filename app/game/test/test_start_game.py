import json
import pytest
from uuid import UUID, uuid4
from datetime import date

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.game.models import Game
from app.game.dtos import GameInDTO
from app.game.service import GameService
from app.player.dtos import PlayerInDTO
from app.player.service import PlayerService
from app.card.service import CardService
from app.card.enums import CardOwner

# =======================
# Fixtures de DB en memoria
# =======================
@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


# =======================
# Fixture JSON de cartas
# =======================
@pytest.fixture
def deck_json():
    with open("app/card/deck.json", "r", encoding="utf-8") as f:
        return json.load(f)


# =======================
# Test principal del flujo
# =======================
def test_full_game_flow(db_session, deck_json):
    # ---- Crear Game ----
    game_service = GameService(db_session)
    host_id = uuid4()
    game_in = GameInDTO(
        name="Test Game",
        host_name=str(host_id),
        birthday=date(2000, 1, 1),
        min_players=2,
        max_players=4
    )
    game_out = game_service.create_game(game_in)

    assert isinstance(game_out.id, UUID)
    assert game_out.ready is False
    assert game_out.host_id in game_out.players_ids
    assert len(game_out.players_ids) == 1  # solo el host por ahora

    # ---- Agregar jugadores ----
    player_service = PlayerService(db_session)
    player2 = player_service.create_player(PlayerInDTO(name="Alice", birthday=date(2001, 2, 2)))
    player3 = player_service.create_player(PlayerInDTO(name="Bob", birthday=date(2002, 3, 3)))

    player_service.assign_game_to_player(player2.id, game_out.id)
    player_service.assign_game_to_player(player3.id, game_out.id)

    players = player_service.get_players_by_game_id(game_out.id)
    assert len(players) == 3

    jugadores_ids = [p.id for p in players]

    # ---- Iniciar el juego usando deck_json directamente ----
    success = game_service.start_game(game_out.id, deck_json=deck_json)
    assert success is True

    # ---- Comprobar que cada jugador recibió 6 cartas ----
    print("\n=== Cartas por jugador ===")
    for pid in jugadores_ids:
        cartas_jugador = CardService.get_cards_by_owner(db_session, game_out.id, CardOwner.PLAYER, player_id=pid)
        print(f"Jugador {pid} tiene {len(cartas_jugador)} cartas:")
        for c in cartas_jugador:
            print(f"  - {c.name} ({c.type})")
        assert len(cartas_jugador) == 6  # verificación exacta

    # ---- Mostrar cartas que quedan en el mazo ----
    cartas_mazo = CardService.get_cards_by_owner(db_session, game_out.id, CardOwner.DECK)
    print(f"\nCartas restantes en el mazo: {len(cartas_mazo)}")
    for c in cartas_mazo:
        print(f"  - {c.name} ({c.type})")

    # Juego debería estar listo
    game_entity = db_session.query(Game).filter(Game.id == game_out.id).first()
    assert game_entity.ready is True
