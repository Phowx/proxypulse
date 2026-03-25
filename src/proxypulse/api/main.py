from __future__ import annotations

from fastapi import Depends, FastAPI, Header, HTTPException, status
from fastapi.responses import HTMLResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
import uvicorn

from proxypulse.api.webapp import WEBAPP_HTML, format_metric_value, format_relative_time, validate_telegram_webapp_init_data
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
from proxypulse.core.webapp_auth import validate_webapp_access_token
from proxypulse.services.alerts import list_active_alerts_for_node
from proxypulse.services.dashboard import build_node_detail_summary, build_nodes_dashboard
from proxypulse.services.nodes import (
    NodeServiceError,
    get_node_by_agent_token,
    get_node_by_name,
    list_nodes,
    record_heartbeat,
    record_metrics,
    register_agent,
)
from proxypulse.services.quota import get_quota_status
from proxypulse.services.reports import format_bytes

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


def resolve_webapp_url() -> str:
    if settings.webapp_url:
        return settings.webapp_url.rstrip("/")
    return f"{settings.server_url.rstrip('/')}/app"


def require_webapp_admin(
    init_data: str | None,
    fallback_user: str | None = None,
    fallback_token: str | None = None,
) -> dict:
    if init_data:
        user = validate_telegram_webapp_init_data(init_data, settings.bot_token)
        if user.get("id") not in settings.admin_telegram_ids:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden.")
        return user

    if fallback_user and fallback_token and settings.bot_token:
        try:
            user_id = int(fallback_user)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid web app user.") from exc
        if user_id not in settings.admin_telegram_ids:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden.")
        if not validate_webapp_access_token(user_id, fallback_token, settings.bot_token):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid web app token.")
        return {"id": user_id}

    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing Telegram init data.")


def webapp_status_label(node) -> str:
    if node.is_online:
        return "在线"
    if node.status.value == "pending":
        return "待接入"
    return "离线"


def webapp_network_interface(value: str | None) -> str:
    if not value:
        return "暂无"
    if value == "aggregate":
        return "汇总"
    return value


def webapp_bytes(value: int | None) -> str:
    if value is None:
        return "暂无"
    return format_bytes(value)


def webapp_rate(value: float | None) -> str:
    if value is None:
        return "暂无"
    return f"{format_bytes(max(int(value), 0))}/s"


def webapp_count(value: int | None) -> str:
    if value is None:
        return "暂无"
    return f"{value:,}"


def webapp_uptime(value: int | None) -> str:
    if value is None:
        return "暂无"
    days, remainder = divmod(value, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes = remainder // 60
    if days > 0:
        return f"{days}d {hours}h"
    if hours > 0:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def webapp_resource_usage(used_bytes: int | None, total_bytes: int | None, percent: float | None) -> str:
    if used_bytes is not None and total_bytes is not None:
        return f"{format_bytes(used_bytes)} / {format_bytes(total_bytes)} ({format_metric_value(percent)})"
    if percent is not None:
        return format_metric_value(percent)
    return "暂无"


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


@app.get("/app", response_class=HTMLResponse)
async def webapp_index() -> HTMLResponse:
    return HTMLResponse(WEBAPP_HTML)


@app.get("/app/data/overview")
async def webapp_overview(
    x_telegram_init_data: str | None = Header(default=None),
    x_proxypulse_web_user: str | None = Header(default=None),
    x_proxypulse_web_token: str | None = Header(default=None),
    session: AsyncSession = Depends(get_session),
) -> dict:
    require_webapp_admin(x_telegram_init_data, x_proxypulse_web_user, x_proxypulse_web_token)
    nodes = await list_nodes(session)
    overview, cards = await build_nodes_dashboard(session, nodes)
    detail_summaries = {node.name: await build_node_detail_summary(session, node) for node in nodes}
    return {
        "webapp_url": resolve_webapp_url(),
        "overview": {
            "node_count": len(nodes),
            "online_count": overview.online_count,
            "offline_count": overview.offline_count,
            "pending_count": overview.pending_count,
            "active_alert_count": overview.active_alert_count,
            "total_down_24h": format_bytes(overview.total_rx_bytes_24h),
            "total_up_24h": format_bytes(overview.total_tx_bytes_24h),
            "total_traffic_24h": format_bytes(overview.total_bytes_24h),
        },
        "nodes": [
            {
                "name": card.node.name,
                "status_label": webapp_status_label(card.node),
                "last_seen": format_relative_time(card.node.last_seen_at),
                "hostname": card.node.hostname or "未上报",
                "platform": card.node.platform or "未上报",
                "network_interface": webapp_network_interface(card.node.latest_network_interface),
                "alert_badge": f"{card.active_alert_count} 条告警" if card.active_alert_count else "无告警",
                "cpu": format_metric_value(card.node.latest_cpu_percent),
                "memory": format_metric_value(card.node.latest_memory_percent),
                "disk": format_metric_value(card.node.latest_disk_percent),
                "uptime": webapp_uptime(card.node.latest_uptime_seconds),
                "rx_rate": webapp_rate(card.current_rate.rx_bps),
                "tx_rate": webapp_rate(card.current_rate.tx_bps),
                "rx_24h": format_bytes(card.traffic_24h.rx_bytes),
                "tx_24h": format_bytes(card.traffic_24h.tx_bytes),
                "total_24h": format_bytes(card.traffic_24h.total_bytes),
                "traffic_1h": format_bytes(
                    detail_summaries[card.node.name].trend_1h.rx_bytes + detail_summaries[card.node.name].trend_1h.tx_bytes
                ),
            }
            for card in cards
        ],
    }


@app.get("/app/data/nodes/{name}")
async def webapp_node_detail(
    name: str,
    x_telegram_init_data: str | None = Header(default=None),
    x_proxypulse_web_user: str | None = Header(default=None),
    x_proxypulse_web_token: str | None = Header(default=None),
    session: AsyncSession = Depends(get_session),
) -> dict:
    require_webapp_admin(x_telegram_init_data, x_proxypulse_web_user, x_proxypulse_web_token)
    node = await get_node_by_name(session, name)
    if node is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Node not found.")

    detail_summary = await build_node_detail_summary(session, node)
    quota_status = await get_quota_status(session, node)
    active_alerts = await list_active_alerts_for_node(session, node.id)
    quota_items = [{"label": "套餐", "value": "未配置", "full": False}]
    if quota_status.configured:
        quota_items = [
            {"label": "上限", "value": format_bytes(quota_status.limit_bytes or 0), "full": False},
            {"label": "已用", "value": format_bytes(quota_status.used_bytes), "full": False},
            {"label": "剩余", "value": format_bytes(quota_status.remaining_bytes or 0), "full": False},
            {"label": "进度", "value": f"{quota_status.percent_used:.1f}%" if quota_status.percent_used is not None else "暂无", "full": False},
            {"label": "周期", "value": quota_status.cycle_description or "未配置", "full": True},
        ]

    return {
        "name": node.name,
        "status_label": webapp_status_label(node),
        "last_seen": format_relative_time(node.last_seen_at),
        "hostname": node.hostname or "未上报",
        "platform": node.platform or "未上报",
        "ips": ", ".join(node.ips) if node.ips else "未上报",
        "uptime": webapp_uptime(node.latest_uptime_seconds),
        "network_interface": webapp_network_interface(node.latest_network_interface),
        "alert_badge": f"{detail_summary.active_alert_count} 条告警" if detail_summary.active_alert_count else "无告警",
        "highlights": [
            {"label": "24h 合计", "value": format_bytes(detail_summary.traffic_24h.total_bytes)},
            {"label": "当前下行", "value": webapp_rate(detail_summary.current_rate.rx_bps)},
            {"label": "当前上行", "value": webapp_rate(detail_summary.current_rate.tx_bps)},
            {"label": "在线时长", "value": webapp_uptime(node.latest_uptime_seconds)},
        ],
        "sections": [
            {
                "title": "基础信息",
                "items": [
                    {"label": "主机", "value": node.hostname or "未上报", "full": False},
                    {"label": "接口", "value": webapp_network_interface(node.latest_network_interface), "full": False},
                    {"label": "系统", "value": node.platform or "未上报", "full": True},
                    {"label": "IP", "value": ", ".join(node.ips) if node.ips else "未上报", "full": True},
                ],
            },
            {
                "title": "实时状态",
                "items": [
                    {"label": "CPU", "value": format_metric_value(node.latest_cpu_percent), "full": False},
                    {"label": "核心", "value": webapp_count(node.latest_cpu_count), "full": False},
                    {"label": "负载", "value": f"{node.latest_load_avg_1m:.2f}" if node.latest_load_avg_1m is not None else "暂无", "full": False},
                    {"label": "运行", "value": webapp_uptime(node.latest_uptime_seconds), "full": False},
                    {"label": "内存", "value": webapp_resource_usage(node.latest_memory_used_bytes, node.latest_memory_total_bytes, node.latest_memory_percent), "full": True},
                    {"label": "磁盘", "value": webapp_resource_usage(node.latest_disk_used_bytes, node.latest_disk_total_bytes, node.latest_disk_percent), "full": True},
                ],
            },
            {
                "title": "网络流量",
                "items": [
                    {"label": "下行", "value": webapp_rate(detail_summary.current_rate.rx_bps), "full": False},
                    {"label": "上行", "value": webapp_rate(detail_summary.current_rate.tx_bps), "full": False},
                    {"label": "累计下行", "value": webapp_bytes(node.latest_rx_bytes), "full": False},
                    {"label": "累计上行", "value": webapp_bytes(node.latest_tx_bytes), "full": False},
                    {"label": "收包", "value": webapp_count(node.latest_rx_packets), "full": False},
                    {"label": "发包", "value": webapp_count(node.latest_tx_packets), "full": False},
                    {"label": "RX 错/丢", "value": f"{webapp_count(node.latest_rx_errors)} / {webapp_count(node.latest_rx_dropped)}", "full": False},
                    {"label": "TX 错/丢", "value": f"{webapp_count(node.latest_tx_errors)} / {webapp_count(node.latest_tx_dropped)}", "full": False},
                ],
            },
            {
                "title": "近 1 小时趋势",
                "items": [
                    {"label": "CPU", "value": f"均值 {format_metric_value(detail_summary.trend_1h.avg_cpu_percent)} / 峰值 {format_metric_value(detail_summary.trend_1h.peak_cpu_percent)}", "full": True},
                    {"label": "内存", "value": f"均值 {format_metric_value(detail_summary.trend_1h.avg_memory_percent)} / 峰值 {format_metric_value(detail_summary.trend_1h.peak_memory_percent)}", "full": True},
                    {"label": "磁盘", "value": f"均值 {format_metric_value(detail_summary.trend_1h.avg_disk_percent)} / 峰值 {format_metric_value(detail_summary.trend_1h.peak_disk_percent)}", "full": True},
                    {"label": "1h 下行", "value": format_bytes(detail_summary.trend_1h.rx_bytes), "full": False},
                    {"label": "1h 上行", "value": format_bytes(detail_summary.trend_1h.tx_bytes), "full": False},
                    {"label": "样本", "value": str(detail_summary.trend_1h.sample_count), "full": False},
                ],
            },
            {
                "title": "近 24 小时 / 套餐",
                "items": [
                    {"label": "24h 下行", "value": format_bytes(detail_summary.traffic_24h.rx_bytes), "full": False},
                    {"label": "24h 上行", "value": format_bytes(detail_summary.traffic_24h.tx_bytes), "full": False},
                    {"label": "24h 合计", "value": format_bytes(detail_summary.traffic_24h.total_bytes), "full": True},
                    *quota_items,
                ],
            },
        ],
        "alerts": [
            {
                "severity": "critical" if alert.severity == "critical" else "warning",
                "text": alert.summary,
            }
            for alert in active_alerts
        ],
    }


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
