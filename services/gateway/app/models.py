from typing import Literal

from pydantic import BaseModel


class User(BaseModel):
    id: str
    name: str
    preferred_language: str
    role: Literal["elder", "moderator", "admin"]
