"""The AI Query endpoint — edge auth → rate limit → LLM gateway → grounded answer."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.auth import Principal, require_principal
from app.guardrails import check_rate_limit
from app.llm.gateway import get_gateway
from app.schemas import QueryRequest, QueryResponse

router = APIRouter()


@router.post("/api/ai/query", response_model=QueryResponse)
def query(req: QueryRequest, principal: Principal = Depends(require_principal)) -> QueryResponse:
    check_rate_limit(principal.tenant_id)                   # guardrail: per-tenant LLM budget → 429
    answer, provider, tools_used = get_gateway().answer(req.question, principal)
    return QueryResponse(answer=answer, provider=provider, tools_used=tools_used)
