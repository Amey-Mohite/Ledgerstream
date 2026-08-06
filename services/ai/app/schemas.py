"""Request/response contracts for the AI Query API."""

from __future__ import annotations

from pydantic import BaseModel, Field


class QueryRequest(BaseModel):
    question: str = Field(min_length=1, max_length=1000)


class QueryResponse(BaseModel):
    answer: str
    provider: str                    # which LLM answered ("claude" | "openai" | "mock")
    tools_used: list[str] = []       # names of the read tools the model called
