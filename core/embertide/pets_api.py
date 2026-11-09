"""Pets on the bed (owner-only to change): name, kind, weight, whether they sleep on the bed. Ember & Tide uses this
only to recognise readings that are the pet rather than a person (engine/pets.py)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, ValidationError

from .models import Pet


class PetsBody(BaseModel):
    pets: list[dict] = Field(default_factory=list, max_length=12)
    ignore: bool | None = None            # "Ignore readings that look like a pet"


def build_pets_router(state) -> APIRouter:
    r = APIRouter(prefix="/api")

    def ctl():
        return state.controller

    def view() -> dict:
        hh = ctl().household
        return {"pets": [p.model_dump() for p in hh.pets], "ignore": hh.pets_ignore}

    @r.get("/pets")
    async def get_pets():
        return view()

    @r.put("/pets")
    async def put_pets(b: PetsBody):
        try:
            pets = [Pet(**{k: v for k, v in p.items() if k in Pet.model_fields}) for p in b.pets]
        except (ValidationError, TypeError) as e:
            first = e.errors()[0] if isinstance(e, ValidationError) else {"msg": str(e)}
            raise HTTPException(422, f"Check the pet details: {first.get('msg', 'invalid value')}")
        seen: set[str] = set()
        for p in pets:                                       # ids must be unique (new pets get fresh ones)
            while p.id in seen:
                p.id = Pet(name=p.name, weight_kg=p.weight_kg).id
            seen.add(p.id)
        hh = ctl().household
        hh.pets = pets
        if b.ignore is not None:
            hh.pets_ignore = b.ignore
        ctl().reload_household(hh)
        return view()

    return r
