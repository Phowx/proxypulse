from __future__ import annotations

from fastapi import Depends, FastAPI, Header, HTTPException, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
import uvicorn

from proxypulse.core.config import get_settings
from proxypulse.core.db import get_session, init_db
from proxypulse.core.schemas import (
    AgentRegisterRequest,
    AgentRegisterResponse,
    HeartbeatRequest,
    MetricSnapshotIn,
    NodeDetail,
    NodeSummary,
)
from proxypulse.services.nodes import (
    NodeServiceError,
    get_node_by_agent_token,
    get_node_by_name,
    list_nodes,
    record_heartbeat,
    record_metrics,
    register_agent,
)

settings = get_settings()
app = FastAPI(title=settings.app_name)


@app.on_event("startup")
async def on_startup() -> None:
    await init_db()


def extract_bearer_token(authorization: str | None) -> str:
    if not authorization:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing Authorization header.")
    parts = authorization.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid Authorization header.")
    return parts[1].strip()


@app.get("/health")
async def healthcheck() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/live")
async def liveness() -> dict[str, str]:
    return {"status": "live"}


@app.get("/health/ready")
async def readiness(session: AsyncSession = Depends(get_session)) -> dict[str, str]:
    await session.execute(text("SELECT 1"))
    return {"status": "ready"}


@app.post("/agent/register", response_model=AgentRegisterResponse)
async def agent_register(payload: AgentRegisterRequest, session: AsyncSession = Depends(get_session)) -> AgentRegisterResponse:
    try:
        node = await register_agent(session, payload)
    except NodeServiceError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return AgentRegisterResponse(node_id=node.id, agent_token=node.agent_token or "")


@app.post("/agent/heartbeat", response_model=NodeSummary)
async def agent_heartbeat(
    payload: HeartbeatRequest,
    authorization: str | None = Header(default=None),
    session: AsyncSession = Depends(get_session),
) -> NodeSummary:
    try:
        node = await get_node_by_agent_token(session, extract_bearer_token(authorization))
        node = await record_heartbeat(session, node, payload)
    except NodeServiceError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    return NodeSummary.model_validate(node)


@app.post("/agent/metrics")
async def ingest_metrics(
    payload: MetricSnapshotIn,
    authorization: str | None = Header(default=None),
    session: AsyncSession = Depends(get_session),
) -> dict[str, str]:
    try:
        node = await get_node_by_agent_token(session, extract_bearer_token(authorization))
        await record_metrics(session, node, payload)
    except NodeServiceError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    return {"status": "accepted"}


@app.get("/nodes", response_model=list[NodeSummary])
async def get_nodes(session: AsyncSession = Depends(get_session)) -> list[NodeSummary]:
    nodes = await list_nodes(session)
    return [NodeSummary.model_validate(node) for node in nodes]


@app.get("/nodes/{name}", response_model=NodeDetail)
async def get_node(name: str, session: AsyncSession = Depends(get_session)) -> NodeDetail:
    node = await get_node_by_name(session, name)
    if node is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Node not found.")
    return NodeDetail.model_validate(node)


def main() -> None:
    uvicorn.run(
        "proxypulse.api.main:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=False,
    )
