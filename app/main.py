from fastapi import FastAPI
from app.card.endpoints import cards_router
from app.player.endpoints import player_router
from app.game.endpoints import router as game_router
from app.secret.endpoints import secret_router
from app.set.endpoints import sets_router
from app.websocket.web_socket import router as ws_router
from app.db import Base, engine
from app.player.models import Player
from app.game.models import Game
from app.card.models import Card
from app.secret.models import Secrets
from fastapi.middleware.cors import CORSMiddleware


app = FastAPI(title="My API")


# Registrar routers
@app.get("/")
async def root():
    return {"message": "Hello World"}


origins = ["*"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Incluir routers
app.include_router(cards_router)
app.include_router(secret_router)
app.include_router(sets_router)
app.include_router(player_router)
app.include_router(game_router)
app.include_router(ws_router)

Base.metadata.create_all(bind=engine)
