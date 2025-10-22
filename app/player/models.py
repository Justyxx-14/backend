import uuid
from datetime import date
from sqlalchemy import Column, String, Date, UUID, ForeignKey, Boolean
from sqlalchemy.orm import validates, relationship, Mapped, mapped_column

from app.db import Base
# from app.secret.models import Secrets

class Player(Base):
    __tablename__ = "players"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String, nullable=False)
    birthday: Mapped[date] = mapped_column(Date, nullable=False)
    game_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("games.id", use_alter=True), nullable=True)
    social_disgrace: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    game = relationship(
        "Game", 
        back_populates="players",
        foreign_keys=[game_id])
    
    secrets = relationship(
        "Secrets",
        back_populates="players",
        cascade="all, delete-orphan"
    )
    

    @validates("name")
    def validate_name(self, key, value):
        if not value or value.strip() == "":
            raise ValueError("El nombre del jugador no puede estar vacío")
        return value
    
    @validates("birthday")
    def validate_birthday(self, key, value):
        if value > date.today():
            raise ValueError("La fecha de nacimiento no puede ser futura")
        return value
