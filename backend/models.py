"""Modelos de entrada (Pydantic). MatchIn sustituye al $jsonSchema que MongoDB no admite en TimeSeries."""
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field


class MatchIn(BaseModel):
    player_id: int = Field(ge=1)
    date: datetime
    competition_id: str = Field(min_length=1, max_length=8, examples=["ES1"])
    club_id: Optional[int] = None
    goals: int = Field(0, ge=0, le=10)
    assists: int = Field(0, ge=0, le=10)
    minutes: int = Field(90, ge=0, le=130)
    yellow: int = Field(0, ge=0, le=2)
    red: int = Field(0, ge=0, le=1)
    result: Literal["W", "D", "L"]


class TitleIn(BaseModel):
    competition_id: str = Field(min_length=1, max_length=8)
    name: str = Field(min_length=1, max_length=60)
    weight: float = Field(ge=0, le=10)


class StatsPatch(BaseModel):
    market_value_eur: Optional[float] = Field(None, ge=0)
    add_title: Optional[TitleIn] = None
