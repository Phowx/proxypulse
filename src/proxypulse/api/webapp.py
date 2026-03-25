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
      --bg-top: #121826;
      --bg-bottom: #0b1018;
      --panel: rgba(16, 23, 36, 0.84);
      --panel-strong: #161f2f;
      --panel-soft: rgba(255, 255, 255, 0.04);
      --line: rgba(255, 255, 255, 0.08);
      --line-strong: rgba(255, 255, 255, 0.14);
      --text: #f4f7fb;
      --muted: #99a7be;
      --accent: #ffb14a;
      --accent-strong: #ff8a33;
      --good: #5fd17a;
      --warn: #ffc45a;
      --bad: #ff6f6f;
      --shadow: 0 24px 60px rgba(0, 0, 0, 0.34);
      --radius: 24px;
      --radius-sm: 18px;
      --font-sans: "Avenir Next", "SF Pro Display", "Segoe UI", sans-serif;
    }

    * { box-sizing: border-box; }
    html, body {
      margin: 0;
      min-height: 100%;
      color: var(--text);
      font-family: var(--font-sans);
      background:
        radial-gradient(circle at top left, rgba(255, 177, 74, 0.18), transparent 26%),
        radial-gradient(circle at top right, rgba(85, 209, 122, 0.12), transparent 24%),
        linear-gradient(180deg, var(--bg-top) 0%, var(--bg-bottom) 100%);
    }
    body { padding: 16px 14px 26px; }
    .shell {
      max-width: 1100px;
      margin: 0 auto;
      display: grid;
      gap: 16px;
    }
    .hero {
      padding: 22px;
      border-radius: 28px;
      background: linear-gradient(160deg, rgba(25, 32, 48, 0.96), rgba(12, 17, 28, 0.96));
      border: 1px solid var(--line);
      box-shadow: var(--shadow);
      overflow: hidden;
      position: relative;
    }
    .hero::after {
      content: "";
      position: absolute;
      inset: auto -40px -60px auto;
      width: 180px;
      height: 180px;
      border-radius: 50%;
      background: radial-gradient(circle, rgba(255, 177, 74, 0.18), transparent 68%);
      pointer-events: none;
    }
    .eyebrow {
      color: var(--accent);
      font-size: 12px;
      letter-spacing: 0.16em;
      text-transform: uppercase;
      margin-bottom: 12px;
    }
    .title-row {
      display: flex;
      justify-content: space-between;
      gap: 16px;
      align-items: start;
      flex-wrap: wrap;
    }
    .title {
      margin: 0;
      font-size: 30px;
      line-height: 1.05;
      font-weight: 800;
    }
    .subtitle {
      margin-top: 10px;
      max-width: 640px;
      color: var(--muted);
      font-size: 14px;
      line-height: 1.55;
    }
    .refresh {
      border: 0;
      border-radius: 999px;
      padding: 11px 16px;
      color: #1a1208;
      background: linear-gradient(135deg, var(--accent), var(--accent-strong));
      font-weight: 800;
      font-size: 13px;
      cursor: pointer;
      box-shadow: 0 12px 28px rgba(255, 138, 51, 0.24);
    }
    .stats {
      margin-top: 18px;
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 12px;
    }
    .stat {
      padding: 14px 15px;
      border-radius: var(--radius-sm);
      background: rgba(255, 255, 255, 0.04);
      border: 1px solid rgba(255, 255, 255, 0.06);
      min-height: 88px;
    }
    .stat-label {
      color: var(--muted);
      font-size: 11px;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      margin-bottom: 10px;
    }
    .stat-value {
      font-size: 24px;
      line-height: 1.15;
      font-weight: 800;
      word-break: break-word;
    }
    .workspace {
      display: grid;
      grid-template-columns: minmax(0, 340px) minmax(0, 1fr);
      gap: 16px;
      align-items: start;
    }
    .panel {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: var(--radius);
      box-shadow: var(--shadow);
      overflow: hidden;
    }
    .sidebar-head,
    .detail-head {
      padding: 18px 18px 16px;
      border-bottom: 1px solid var(--line);
    }
    .panel-title {
      margin: 0;
      font-size: 18px;
      font-weight: 800;
    }
    .panel-subtitle {
      margin-top: 8px;
      color: var(--muted);
      font-size: 13px;
      line-height: 1.45;
    }
    .cards {
      display: grid;
      gap: 10px;
      padding: 12px;
    }
    .card {
      padding: 14px;
      border-radius: 20px;
      background: rgba(255, 255, 255, 0.03);
      border: 1px solid rgba(255, 255, 255, 0.05);
      cursor: pointer;
      transition: transform 0.18s ease, border-color 0.18s ease, background 0.18s ease;
    }
    .card:hover {
      transform: translateY(-1px);
      border-color: rgba(255, 177, 74, 0.28);
    }
    .card.active {
      background: linear-gradient(180deg, rgba(255, 177, 74, 0.08), rgba(255, 255, 255, 0.04));
      border-color: rgba(255, 177, 74, 0.32);
      box-shadow: inset 0 0 0 1px rgba(255, 177, 74, 0.12);
    }
    .name-row {
      display: flex;
      justify-content: space-between;
      gap: 12px;
      align-items: start;
    }
    .name {
      margin: 0;
      font-size: 19px;
      font-weight: 800;
    }
    .identity {
      margin-top: 6px;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.5;
    }
    .status {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      padding: 6px 10px;
      border-radius: 999px;
      background: rgba(255, 255, 255, 0.05);
      border: 1px solid rgba(255, 255, 255, 0.06);
      font-size: 12px;
      font-weight: 800;
      white-space: nowrap;
    }
    .status-dot {
      width: 8px;
      height: 8px;
      border-radius: 50%;
      background: var(--muted);
      box-shadow: 0 0 0 5px rgba(255, 255, 255, 0.04);
    }
    .status.online .status-dot { background: var(--good); }
    .status.pending .status-dot { background: var(--warn); }
    .status.offline .status-dot { background: var(--bad); }
    .meta {
      margin-top: 12px;
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
    }
    .meta span {
      padding: 6px 10px;
      border-radius: 999px;
      background: rgba(255, 255, 255, 0.04);
      color: var(--muted);
      font-size: 12px;
    }
    .mini-grid,
    .pairs,
    .highlights {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 10px;
    }
    .mini-grid { margin-top: 14px; }
    .mini {
      padding: 11px 12px;
      border-radius: 16px;
      background: rgba(255, 255, 255, 0.03);
      border: 1px solid rgba(255, 255, 255, 0.05);
    }
    .mini-label,
    .pair-label,
    .highlight-label {
      color: var(--muted);
      font-size: 11px;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      margin-bottom: 6px;
    }
    .mini-value,
    .pair-value,
    .highlight-value {
      font-size: 15px;
      line-height: 1.4;
      font-weight: 700;
      word-break: break-word;
    }
    .detail {
      display: none;
    }
    .detail.active {
      display: block;
    }
    .detail-body {
      padding: 16px 18px 18px;
      display: grid;
      gap: 16px;
    }
    .summary-strip {
      display: grid;
      gap: 14px;
      padding: 16px;
      border-radius: 20px;
      background: linear-gradient(160deg, rgba(255, 177, 74, 0.08), rgba(255, 255, 255, 0.03));
      border: 1px solid rgba(255, 177, 74, 0.16);
    }
    .summary-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 10px;
    }
    .summary-item {
      padding: 12px 13px;
      border-radius: 16px;
      background: rgba(255, 255, 255, 0.04);
      border: 1px solid rgba(255, 255, 255, 0.05);
    }
    .summary-item.full,
    .pair.full {
      grid-column: 1 / -1;
    }
    .section {
      display: grid;
      gap: 10px;
    }
    .section-title {
      color: var(--muted);
      font-size: 12px;
      letter-spacing: 0.1em;
      text-transform: uppercase;
    }
    .pair {
      padding: 12px 13px;
      border-radius: 18px;
      background: rgba(255, 255, 255, 0.03);
      border: 1px solid rgba(255, 255, 255, 0.05);
    }
    .alerts {
      display: grid;
      gap: 10px;
    }
    .alert {
      padding: 13px 14px;
      border-radius: 18px;
      border: 1px solid rgba(255, 255, 255, 0.06);
      background: rgba(255, 255, 255, 0.03);
      line-height: 1.5;
    }
    .alert.critical {
      border-color: rgba(255, 111, 111, 0.38);
      background: rgba(255, 111, 111, 0.08);
    }
    .alert.warning {
      border-color: rgba(255, 196, 90, 0.34);
      background: rgba(255, 196, 90, 0.08);
    }
    .alert strong {
      display: inline-block;
      margin-bottom: 6px;
      font-size: 12px;
      letter-spacing: 0.08em;
      text-transform: uppercase;
    }
    .empty,
    .error {
      padding: 18px;
      border-radius: 18px;
      text-align: center;
      color: var(--muted);
      background: rgba(255, 255, 255, 0.04);
      border: 1px solid rgba(255, 255, 255, 0.05);
    }
    @media (max-width: 920px) {
      .workspace {
        grid-template-columns: 1fr;
      }
    }
    @media (max-width: 720px) {
      body { padding: 12px 10px 22px; }
      .hero { padding: 18px; }
      .title { font-size: 26px; }
      .stats,
      .summary-grid,
      .pairs,
      .highlights,
      .mini-grid {
        grid-template-columns: 1fr 1fr;
      }
    }
    @media (max-width: 520px) {
      .stats {
        grid-template-columns: 1fr 1fr;
      }
      .title-row {
        align-items: stretch;
      }
      .refresh {
        width: 100%;
      }
    }
  </style>
</head>
<body>
  <div class="shell">
    <section class="hero">
      <div class="eyebrow">ProxyPulse Web App</div>
      <div class="title-row">
        <div>
          <h1 class="title">节点总览</h1>
          <div class="subtitle">把 Telegram 文本入口留给通知，把更完整的状态、趋势和套餐视图放进一个真正可读的面板里。</div>
        </div>
        <button class="refresh" id="refresh">刷新数据</button>
      </div>
      <div class="stats" id="stats"></div>
    </section>

    <section class="workspace">
      <aside class="panel">
        <div class="sidebar-head">
          <h2 class="panel-title">节点轨道</h2>
          <div class="panel-subtitle">先看异常，再点进单节点详情。当前速率、24h 总量和在线时长都直接展示在卡片里。</div>
        </div>
        <div class="cards" id="cards"></div>
      </aside>

      <section class="panel detail" id="detail"></section>
    </section>
  </div>

  <script>
    const tg = window.Telegram?.WebApp;
    if (tg) {
      tg.ready();
      tg.expand();
      tg.setHeaderColor('#151c2c');
      tg.setBackgroundColor('#0d131d');
    }

    const statsEl = document.getElementById('stats');
    const cardsEl = document.getElementById('cards');
    const detailEl = document.getElementById('detail');
    const refreshBtn = document.getElementById('refresh');
    const statusClass = { '在线': 'online', '待接入': 'pending', '离线': 'offline' };
    const searchParams = new URLSearchParams(window.location.search);

    let selectedNode = null;
    let latestNodes = [];

    function escapeHtml(value) {
      return String(value ?? '')
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;')
        .replaceAll("'", '&#39;');
    }

    function statusBadge(label) {
      return `
        <div class="status ${statusClass[label] || ''}">
          <span class="status-dot"></span>
          <span>${escapeHtml(label)}</span>
        </div>
      `;
    }

    function pair(label, value, full = false) {
      return `
        <div class="pair ${full ? 'full' : ''}">
          <div class="pair-label">${escapeHtml(label)}</div>
          <div class="pair-value">${escapeHtml(value)}</div>
        </div>
      `;
    }

    function stat(label, value) {
      return `
        <div class="stat">
          <div class="stat-label">${escapeHtml(label)}</div>
          <div class="stat-value">${escapeHtml(value)}</div>
        </div>
      `;
    }

    function renderStats(data) {
      statsEl.innerHTML = [
        ['节点总数', data.node_count],
        ['在线', data.online_count],
        ['离线', data.offline_count],
        ['待接入', data.pending_count],
        ['24h 合计', data.total_traffic_24h],
        ['活动告警', data.active_alert_count],
      ].map(([label, value]) => stat(label, value)).join('');
    }

    function renderCards(nodes) {
      if (!nodes.length) {
        cardsEl.innerHTML = '<div class="empty">还没有接入任何节点。</div>';
        return;
      }
      cardsEl.innerHTML = nodes.map((node) => `
        <article class="card ${selectedNode === node.name ? 'active' : ''}" data-node="${escapeHtml(node.name)}">
          <div class="name-row">
            <div>
              <h3 class="name">${escapeHtml(node.name)}</h3>
              <div class="identity">${escapeHtml(node.hostname)}<br>${escapeHtml(node.platform)}</div>
            </div>
            ${statusBadge(node.status_label)}
          </div>
          <div class="meta">
            <span>${escapeHtml(node.last_seen)}</span>
            <span>${escapeHtml(node.network_interface)}</span>
            <span>${escapeHtml(node.alert_badge)}</span>
          </div>
          <div class="mini-grid">
            <div class="mini">
              <div class="mini-label">CPU / 内存</div>
              <div class="mini-value">${escapeHtml(node.cpu)} / ${escapeHtml(node.memory)}</div>
            </div>
            <div class="mini">
              <div class="mini-label">磁盘 / 运行</div>
              <div class="mini-value">${escapeHtml(node.disk)} / ${escapeHtml(node.uptime)}</div>
            </div>
            <div class="mini">
              <div class="mini-label">当前速率</div>
              <div class="mini-value">↓ ${escapeHtml(node.rx_rate)}<br>↑ ${escapeHtml(node.tx_rate)}</div>
            </div>
            <div class="mini">
              <div class="mini-label">24h 总量</div>
              <div class="mini-value">${escapeHtml(node.total_24h)}<br><span style="color:var(--muted);font-size:12px;">1h ${escapeHtml(node.traffic_1h)}</span></div>
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
            <div>
              <h3 class="name">${escapeHtml(detail.name)}</h3>
              <div class="identity">${escapeHtml(detail.hostname)}<br>${escapeHtml(detail.platform)}</div>
            </div>
            ${statusBadge(detail.status_label)}
          </div>
          <div class="meta">
            <span>${escapeHtml(detail.last_seen)}</span>
            <span>${escapeHtml(detail.network_interface)}</span>
            <span>${escapeHtml(detail.alert_badge)}</span>
          </div>
        </div>
        <div class="detail-body">
          <div class="summary-strip">
            <div class="section-title">当前摘要</div>
            <div class="summary-grid">
              ${detail.highlights.map((item) => `
                <div class="summary-item">
                  <div class="highlight-label">${escapeHtml(item.label)}</div>
                  <div class="highlight-value">${escapeHtml(item.value)}</div>
                </div>
              `).join('')}
              <div class="summary-item full">
                <div class="highlight-label">IP</div>
                <div class="highlight-value">${escapeHtml(detail.ips)}</div>
              </div>
            </div>
          </div>

          ${detail.sections.map((section) => `
            <div class="section">
              <div class="section-title">${escapeHtml(section.title)}</div>
              <div class="pairs">
                ${section.items.map((item) => pair(item.label, item.value, item.full)).join('')}
              </div>
            </div>
          `).join('')}

          <div class="section">
            <div class="section-title">当前告警</div>
            <div class="alerts">
              ${detail.alerts.length
                ? detail.alerts.map((alert) => `
                    <div class="alert ${escapeHtml(alert.severity)}">
                      <strong>${alert.severity === 'critical' ? 'Critical' : 'Warning'}</strong>
                      <div>${escapeHtml(alert.text)}</div>
                    </div>
                  `).join('')
                : '<div class="empty">当前没有活动告警。</div>'
              }
            </div>
          </div>
        </div>
      `;
    }

    async function api(path) {
      const initData = tg?.initData || '';
      const headers = {
        'X-Telegram-Init-Data': initData,
      };
      const fallbackUser = searchParams.get('uid');
      const fallbackToken = searchParams.get('sig');
      if (!initData && fallbackUser && fallbackToken) {
        headers['X-ProxyPulse-Web-User'] = fallbackUser;
        headers['X-ProxyPulse-Web-Token'] = fallbackToken;
      }
      const response = await fetch(path, {
        headers,
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
        latestNodes = data.nodes;
        if (!selectedNode || !data.nodes.some((node) => node.name === selectedNode)) {
          selectedNode = data.nodes[0]?.name || null;
        }
        renderCards(latestNodes);
        if (selectedNode) {
          await loadDetail(selectedNode);
        } else {
          detailEl.classList.remove('active');
          detailEl.innerHTML = '<div class="empty">选择左侧节点查看详情。</div>';
        }
      } catch (error) {
        statsEl.innerHTML = `<div class="error">加载失败：${escapeHtml(error.message)}</div>`;
        cardsEl.innerHTML = '';
        detailEl.classList.add('active');
        detailEl.innerHTML = `<div class="error">概览加载失败：${escapeHtml(error.message)}</div>`;
      }
    }

    async function loadDetail(name) {
      selectedNode = name;
      try {
        const detail = await api(`/app/data/nodes/${encodeURIComponent(name)}`);
        renderCards(latestNodes);
        renderDetail(detail);
      } catch (error) {
        detailEl.classList.add('active');
        detailEl.innerHTML = `<div class="error">节点详情加载失败：${escapeHtml(error.message)}</div>`;
      }
    }

    cardsEl.addEventListener('click', async (event) => {
      const card = event.target.closest('.card');
      if (!card) {
        return;
      }
      selectedNode = card.dataset.node;
      renderCards(latestNodes);
      await loadDetail(selectedNode);
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
