"""FastAPI application. Phase 0 is a keyless skeleton: a health probe, a mock
ask endpoint, and enough shape to prove the request/response and error contracts
before tenancy (Phase 1) and real retrieval (Phase 3+) land.
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from knowledge_desk import __version__
from knowledge_desk.config import settings
from knowledge_desk.providers import get_provider

app = FastAPI(title="Knowledge Desk", version=__version__)

# Placeholder corpus registry until Phase 2 ingestion exists. Lets the 404
# contract be real (unknown collection) rather than a routing accident.
_KNOWN_COLLECTIONS = {"demo"}


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=500)


class AskResponse(BaseModel):
    answer: str
    provider: str


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok", "version": __version__, "provider": settings.provider}


@app.get("/collections/{name}")
def get_collection(name: str) -> dict:
    if name not in _KNOWN_COLLECTIONS:
        raise HTTPException(status_code=404, detail=f"unknown collection: {name}")
    return {"name": name}


@app.post("/ask", response_model=AskResponse)
def ask(req: AskRequest) -> AskResponse:
    provider = get_provider()
    return AskResponse(answer=provider.answer(req.question), provider=provider.name)
