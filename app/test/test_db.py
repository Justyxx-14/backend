from pathlib import Path
import importlib
from sqlalchemy import MetaData, Table, Column, Integer, String, create_engine


def test_db_path_points_inside_app():
    import app.db as db
    app_dir = Path(db.__file__).resolve().parent
    assert db.DB_PATH.name == "app.db"
    assert db.DB_PATH.parent == app_dir   # debe quedar en app/

def test_engine_creates_sqlite_file(tmp_path, monkeypatch):
    # monkeypatch es un fixture de pytest que permite "parchar" atributos/funciones/entorno
    # de forma temporal durante un test. 

    # Forzamos a que DB_PATH apunte a un tmp para no tocar el proyecto 
    import app.db as db
    importlib.reload(db)  # por si hay caché

    fake_db_path = tmp_path / "app.db"
    # parcheamos los atributos y rearmamos el engine con esa ruta
    monkeypatch.setattr(db, "DB_PATH", fake_db_path, raising=False)
    monkeypatch.setattr(db, "DATABASE_URL", f"sqlite:///{fake_db_path}", raising=False)
    # reconstruimos engine y SessionLocal
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    new_engine = create_engine(db.DATABASE_URL, future=True)
    monkeypatch.setattr(db, "engine", new_engine, raising=False)
    monkeypatch.setattr(db, "SessionLocal", sessionmaker(bind=new_engine, expire_on_commit=False, future=True), raising=False)

    assert not fake_db_path.exists()
    # abrir/cerrar conexión crea el archivo sqlite
    conn = db.engine.connect()
    conn.close()
    assert fake_db_path.exists()

def test_get_db_yields_and_closes_session(monkeypatch):
    import app.db as db

    # Fake Session para comprobar que se cierra
    closed_flag = {"closed": False}

    class FakeSession:
        def close(self):
            closed_flag["closed"] = True

    def FakeSessionLocal():
        return FakeSession()

    monkeypatch.setattr(db, "SessionLocal", FakeSessionLocal, raising=False)

    gen = db.get_db()
    session = next(gen)        # obtiene la "sesión"
    assert isinstance(session, FakeSession)
    # cerrar el generador debe invocar close()
    try:
        next(gen)
    except StopIteration:
        pass

    assert closed_flag["closed"] is True


def test_metadata_create_all_creates_table(tmp_path, monkeypatch):
    """
    Usa el engine de app.db (parcheado a un archivo temporal) y verifica que
    metadata.create_all(engine) crea efectivamente una tabla.
    """
    import app.db as db
    importlib.reload(db)  # por si quedó cacheado

    # 1) Redirigimos la DB a un archivo temporal para no tocar la real
    fake_db_path = tmp_path / "app.db"
    fake_url = f"sqlite:///{fake_db_path}"

    # Reemplazamos símbolos del módulo para que apunten a la DB temporal
    monkeypatch.setattr(db, "DB_PATH", fake_db_path, raising=False)
    monkeypatch.setattr(db, "DATABASE_URL", fake_url, raising=False)

    # Re-creamos engine y SessionLocal con la URL temporal
    new_engine = create_engine(fake_url, future=True)
    monkeypatch.setattr(db, "engine", new_engine, raising=False)

    from sqlalchemy.orm import sessionmaker
    monkeypatch.setattr(
        db,
        "SessionLocal",
        sessionmaker(bind=new_engine, expire_on_commit=False, future=True),
        raising=False,
    )

    # 2) Definimos un metadata/tabla "de prueba" 
    test_md = MetaData()
    _ = Table(
        "tmp_healthcheck",
        test_md,
        Column("id", Integer, primary_key=True),
        Column("name", String(50), nullable=False),
    )

    # 3) Ejecutamos create_all contra el engine del módulo
    assert not fake_db_path.exists()
    test_md.create_all(db.engine)  
    assert fake_db_path.exists()

    # 4) Verificamos que la tabla fue creada en SQLite
    with db.engine.connect() as conn:
        res = conn.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='tmp_healthcheck'"
        ).fetchone()
        assert res is not None  # la tabla existe
