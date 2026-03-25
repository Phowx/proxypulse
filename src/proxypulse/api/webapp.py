from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, datetime
from urllib.parse import parse_qsl

from fastapi import HTTPException, status

from proxypulse.services.reports import format_bytes

WEBAPP_HTML = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, viewport-fit=cover" />
  <title>ProxyPulse</title>
  <script src="https://telegram.org/js/telegram-web-app.js"></script>
  <style>
    :root {
      --bg: #0c1220;
      --panel: rgba(19, 28, 45, 0.86);
      --panel-strong: #182238;
      --text: #ecf2ff;
      --muted: #98a6c3;
      --line: rgba(255,255,255,0.08);
      --accent: #6dd3ff;
      --accent-strong: #2aa7ff;
      --good: #59d66f;
      --warn: #ffbf57;
      --bad: #ff6b6b;
      --shadow: 0 20px 48px rgba(0, 0, 0, 0.32);
      --radius: 22px;
      --radius-sm: 16px;
      --font-sans: "Avenir Next", "SF Pro Display", "Segoe UI", sans-serif;
      --font-serif: "Iowan Old Style", "Palatino Linotype", serif;
    }

    * { box-sizing: border-box; }
    html, body { margin: 0; min-height: 100%; background:
      radial-gradient(circle at top left, rgba(42,167,255,0.18), transparent 28%),
      radial-gradient(circle at top right, rgba(109,211,255,0.16), transparent 26%),
      linear-gradient(180deg, #0b1120 0%, #121a2e 100%);
      color: var(--text);
      font-family: var(--font-sans);
    }
    body {
      padding: 18px 16px 28px;
    }
    .shell {
      max-width: 960px;
      margin: 0 auto;
      display: grid;
      gap: 16px;
    }
    .hero {
      padding: 22px;
      border-radius: 28px;
      background: linear-gradient(145deg, rgba(24,34,56,0.96), rgba(15,21,36,0.92));
      box-shadow: var(--shadow);
      border: 1px solid rgba(255,255,255,0.08);
    }
    .eyebrow {
      color: var(--accent);
      font-size: 12px;
      letter-spacing: 0.18em;
      text-transform: uppercase;
      margin-bottom: 10px;
    }
    .title {
      margin: 0;
      font-size: 34px;
      line-height: 1;
      font-family: var(--font-serif);
      font-weight: 700;
    }
    .subtitle {
      margin-top: 10px;
      color: var(--muted);
      font-size: 14px;
      line-height: 1.5;
    }
    .stats {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 12px;
      margin-top: 18px;
    }
    .stat {
      padding: 14px 16px;
      border-radius: var(--radius-sm);
      background: rgba(255,255,255,0.04);
      border: 1px solid rgba(255,255,255,0.06);
    }
    .stat-label {
      color: var(--muted);
      font-size: 12px;
      margin-bottom: 8px;
      text-transform: uppercase;
      letter-spacing: 0.08em;
    }
    .stat-value {
      font-size: 22px;
      font-weight: 700;
    }
    .toolbar {
      display: flex;
      gap: 10px;
      align-items: center;
      justify-content: space-between;
      flex-wrap: wrap;
    }
    .toolbar h2 {
      margin: 0;
      font-size: 18px;
      font-weight: 700;
    }
    .toolbar button {
      border: 0;
      background: linear-gradient(135deg, var(--accent), var(--accent-strong));
      color: #08111f;
      border-radius: 999px;
      padding: 10px 16px;
      font-weight: 700;
      font-size: 13px;
      cursor: pointer;
    }
    .grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
      gap: 14px;
    }
    .card, .detail {
      background: var(--panel);
      border: 1px solid rgba(255,255,255,0.08);
      border-radius: 24px;
      box-shadow: var(--shadow);
      overflow: hidden;
    }
    .card { cursor: pointer; transition: transform .18s ease, border-color .18s ease; }
    .card:hover { transform: translateY(-2px); border-color: rgba(109, 211, 255, 0.32); }
    .card-head, .detail-head {
      padding: 18px 18px 14px;
      border-bottom: 1px solid var(--line);
    }
    .name-row {
      display: flex;
      justify-content: space-between;
      align-items: start;
      gap: 10px;
    }
    .name {
      margin: 0;
      font-size: 20px;
      font-weight: 700;
    }
    .status {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      padding: 6px 10px;
      border-radius: 999px;
      font-size: 12px;
      font-weight: 700;
      background: rgba(255,255,255,0.05);
      color: var(--text);
    }
    .status-dot {
      width: 8px;
      height: 8px;
      border-radius: 50%;
      background: var(--muted);
      box-shadow: 0 0 0 4px rgba(255,255,255,0.05);
    }
    .status.online .status-dot { background: var(--good); }
    .status.pending .status-dot { background: var(--warn); }
    .status.offline .status-dot { background: var(--bad); }
    .meta {
      margin-top: 10px;
      color: var(--muted);
      font-size: 13px;
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
    }
    .meta span::after {
      content: "·";
      margin-left: 8px;
      color: rgba(255,255,255,0.24);
    }
    .meta span:last-child::after { display: none; }
    .card-body, .detail-body {
      padding: 16px 18px 18px;
      display: grid;
      gap: 14px;
    }
    .section {
      display: grid;
      gap: 10px;
    }
    .section-title {
      color: var(--muted);
      font-size: 11px;
      text-transform: uppercase;
      letter-spacing: 0.12em;
    }
    .pairs {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 10px 14px;
    }
    .pair.full {
      grid-column: 1 / -1;
    }
    .pair {
      padding: 10px 12px;
      border-radius: 16px;
      background: rgba(255,255,255,0.03);
      border: 1px solid rgba(255,255,255,0.05);
    }
    .pair-label {
      color: var(--muted);
      font-size: 11px;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      margin-bottom: 6px;
    }
    .pair-value {
      font-size: 15px;
      line-height: 1.4;
      font-weight: 600;
      word-break: break-word;
    }
    .detail {
      display: none;
    }
    .detail.active {
      display: block;
    }
    .empty, .error {
      padding: 18px;
      border-radius: 18px;
      background: rgba(255,255,255,0.04);
      color: var(--muted);
      text-align: center;
    }
    .alerts {
      display: grid;
      gap: 10px;
    }
    .alert {
      padding: 12px 14px;
      border-radius: 16px;
      background: rgba(255,255,255,0.03);
      border: 1px solid rgba(255,255,255,0.05);
      line-height: 1.45;
    }
    .alert.critical { border-color: rgba(255,107,107,0.4); }
    .alert.warning { border-color: rgba(255,191,87,0.4); }
    @media (max-width: 720px) {
      body { padding: 14px 12px 24px; }
      .title { font-size: 28px; }
      .stats, .pairs { grid-template-columns: 1fr 1fr; }
    }
  </style>
</head>
<body>
  <div class="shell">
    <section class="hero">
      <div class="eyebrow">ProxyPulse Web App</div>
      <h1 class="title">VPS 总览面板</h1>
      <div class="subtitle">保留 Telegram 文本入口，用更适合屏幕的方式看节点、告警和趋势。</div>
      <div class="stats" id="stats"></div>
    </section>

    <section class="toolbar">
      <h2>节点列表</h2>
      <button id="refresh">刷新数据</button>
    </section>

    <section class="grid" id="cards"></section>
    <section class="detail" id="detail"></section>
  </div>

  <script>
    const tg = window.Telegram?.WebApp;
    if (tg) {
      tg.ready();
      tg.expand();
      tg.setHeaderColor('#0f1728');
      tg.setBackgroundColor('#0c1220');
    }

    const statsEl = document.getElementById('stats');
    const cardsEl = document.getElementById('cards');
    const detailEl = document.getElementById('detail');
    const refreshBtn = document.getElementById('refresh');

    const statusClass = { '🟢 在线': 'online', '🟡 待接入': 'pending', '🔴 离线': 'offline' };

    function pair(label, value, full = false) {
      return `
        <div class="pair ${full ? 'full' : ''}">
          <div class="pair-label">${label}</div>
          <div class="pair-value">${value}</div>
        </div>
      `;
    }

    function renderStats(data) {
      statsEl.innerHTML = [
        ['在线', data.online_count],
        ['离线', data.offline_count],
        ['待接入', data.pending_count],
        ['活动告警', data.active_alert_count],
        ['24h 总流量', data.total_traffic_24h],
      ].map(([label, value]) => `
        <div class="stat">
          <div class="stat-label">${label}</div>
          <div class="stat-value">${value}</div>
        </div>
      `).join('');
    }

    function renderCards(nodes) {
      cardsEl.innerHTML = nodes.map((node) => `
        <article class="card" data-node="${node.name}">
          <div class="card-head">
            <div class="name-row">
              <h3 class="name">${node.name}</h3>
              <div class="status ${statusClass[node.status_label] || ''}">
                <span class="status-dot"></span>
                <span>${node.status_label}</span>
              </div>
            </div>
            <div class="meta">
              <span>${node.last_seen}</span>
              <span>${node.network_interface}</span>
              <span>${node.alert_badge}</span>
            </div>
          </div>
          <div class="card-body">
            <div class="section">
              <div class="section-title">资源</div>
              <div class="pairs">
                ${pair('CPU', node.cpu)}
                ${pair('内存', node.memory)}
                ${pair('磁盘', node.disk)}
                ${pair('1h 流量', node.traffic_1h)}
              </div>
            </div>
            <div class="section">
              <div class="section-title">流量</div>
              <div class="pairs">
                ${pair('下行', node.rx_rate)}
                ${pair('上行', node.tx_rate)}
                ${pair('24h 下行', node.rx_24h)}
                ${pair('24h 上行', node.tx_24h)}
              </div>
            </div>
          </div>
        </article>
      `).join('');
    }

    function renderDetail(detail) {
      detailEl.classList.add('active');
      detailEl.innerHTML = `
        <div class="detail-head">
          <div class="name-row">
            <h3 class="name">${detail.name}</h3>
            <div class="status ${statusClass[detail.status_label] || ''}">
              <span class="status-dot"></span>
              <span>${detail.status_label}</span>
            </div>
          </div>
          <div class="meta">
            <span>${detail.last_seen}</span>
            <span>${detail.network_interface}</span>
            <span>${detail.alert_badge}</span>
          </div>
        </div>
        <div class="detail-body">
          ${detail.sections.map((section) => `
            <div class="section">
              <div class="section-title">${section.title}</div>
              <div class="pairs">
                ${section.items.map((item) => pair(item.label, item.value, item.full)).join('')}
              </div>
            </div>
          `).join('')}
          <div class="section">
            <div class="section-title">当前告警</div>
            <div class="alerts">
              ${detail.alerts.length
                ? detail.alerts.map((alert) => `<div class="alert ${alert.severity}">${alert.text}</div>`).join('')
                : '<div class="empty">当前没有活动告警。</div>'
              }
            </div>
          </div>
        </div>
      `;
    }

    async function api(path) {
      const initData = tg?.initData || '';
      const response = await fetch(path, {
        headers: {
          'X-Telegram-Init-Data': initData,
        },
      });
      if (!response.ok) {
        const text = await response.text();
        throw new Error(text || `HTTP ${response.status}`);
      }
      return response.json();
    }

    async function loadOverview() {
      try {
        const data = await api('/app/data/overview');
        renderStats(data.overview);
        renderCards(data.nodes);
        if (data.nodes[0]) {
          await loadDetail(data.nodes[0].name);
        }
      } catch (error) {
        statsEl.innerHTML = `<div class="error">加载失败：${error.message}</div>`;
        cardsEl.innerHTML = '';
        detailEl.innerHTML = '';
        detailEl.classList.remove('active');
      }
    }

    async function loadDetail(name) {
      try {
        const detail = await api(`/app/data/nodes/${encodeURIComponent(name)}`);
        renderDetail(detail);
      } catch (error) {
        detailEl.classList.add('active');
        detailEl.innerHTML = `<div class="error">节点详情加载失败：${error.message}</div>`;
      }
    }

    cardsEl.addEventListener('click', (event) => {
      const card = event.target.closest('.card');
      if (card) {
        loadDetail(card.dataset.node);
      }
    });
    refreshBtn.addEventListener('click', loadOverview);
    loadOverview();
  </script>
</body>
</html>
"""


def validate_telegram_webapp_init_data(init_data: str, bot_token: str) -> dict:
    if not init_data:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing Telegram init data.")
    if not bot_token:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Bot token not configured.")

    values = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = values.pop("hash", "")
    if not received_hash:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing Telegram hash.")

    data_check_string = "\n".join(f"{key}={values[key]}" for key in sorted(values))
    secret_key = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()
    expected_hash = hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected_hash, received_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid Telegram signature.")

    user_payload = values.get("user")
    if not user_payload:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing Telegram user.")
    return json.loads(user_payload)


def format_relative_time(value: datetime | None) -> str:
    if value is None:
        return "暂无上报"
    aware_value = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    delta_seconds = max(int((datetime.now(UTC) - aware_value.astimezone(UTC)).total_seconds()), 0)
    if delta_seconds < 60:
        return f"{delta_seconds}s 前"
    if delta_seconds < 3600:
        return f"{delta_seconds // 60}m 前"
    if delta_seconds < 86400:
        hours, remainder = divmod(delta_seconds, 3600)
        return f"{hours}h {remainder // 60}m 前"
    days, remainder = divmod(delta_seconds, 86400)
    return f"{days}d {remainder // 3600}h 前"


def format_metric_value(value: float | None, suffix: str = "%") -> str:
    if value is None:
        return "暂无"
    return f"{value:.1f}{suffix}"
