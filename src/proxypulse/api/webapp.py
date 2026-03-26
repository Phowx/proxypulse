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
      --bg-top: #101826;
      --bg-bottom: #090e16;
      --panel: rgba(17, 24, 37, 0.88);
      --panel-soft: rgba(255, 255, 255, 0.04);
      --line: rgba(255, 255, 255, 0.08);
      --text: #f2f5fb;
      --muted: #97a4bb;
      --accent: #ffaf4a;
      --accent-strong: #ff8b39;
      --good: #55d17a;
      --warn: #ffc65a;
      --bad: #ff6e6e;
      --shadow: 0 20px 50px rgba(0, 0, 0, 0.34);
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
        radial-gradient(circle at top left, rgba(255, 175, 74, 0.18), transparent 28%),
        radial-gradient(circle at top right, rgba(85, 209, 122, 0.12), transparent 26%),
        linear-gradient(180deg, var(--bg-top) 0%, var(--bg-bottom) 100%);
    }
    body { padding: 12px 10px 22px; }
    .shell {
      max-width: 760px;
      margin: 0 auto;
      display: grid;
      gap: 14px;
    }
    .panel {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: var(--radius);
      box-shadow: var(--shadow);
      overflow: hidden;
    }
    .hero {
      padding: 18px;
      position: relative;
    }
    .hero::after {
      content: "";
      position: absolute;
      right: -30px;
      bottom: -60px;
      width: 160px;
      height: 160px;
      border-radius: 50%;
      background: radial-gradient(circle, rgba(255, 175, 74, 0.18), transparent 68%);
      pointer-events: none;
    }
    .eyebrow {
      color: var(--accent);
      font-size: 12px;
      letter-spacing: 0.16em;
      text-transform: uppercase;
      margin-bottom: 10px;
    }
    .title-row {
      display: flex;
      gap: 12px;
      align-items: flex-start;
      justify-content: space-between;
      flex-wrap: wrap;
    }
    .title {
      margin: 0;
      font-size: 28px;
      line-height: 1.08;
      font-weight: 800;
    }
    .subtitle {
      margin-top: 10px;
      color: var(--muted);
      font-size: 14px;
      line-height: 1.55;
      max-width: 540px;
    }
    .refresh {
      border: 0;
      border-radius: 999px;
      padding: 11px 15px;
      background: linear-gradient(135deg, var(--accent), var(--accent-strong));
      color: #1a1208;
      font-size: 13px;
      font-weight: 800;
      cursor: pointer;
      box-shadow: 0 10px 24px rgba(255, 139, 57, 0.24);
    }
    .overview-grid {
      padding: 0 18px 18px;
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 10px;
    }
    .overview-card {
      border: 1px solid rgba(255, 255, 255, 0.06);
      background: rgba(255, 255, 255, 0.04);
      border-radius: var(--radius-sm);
      padding: 14px;
      text-align: left;
      color: var(--text);
      cursor: pointer;
      min-height: 108px;
      transition: transform 0.18s ease, border-color 0.18s ease, background 0.18s ease;
    }
    .overview-card.active {
      background: linear-gradient(180deg, rgba(255, 175, 74, 0.1), rgba(255, 255, 255, 0.04));
      border-color: rgba(255, 175, 74, 0.34);
      box-shadow: inset 0 0 0 1px rgba(255, 175, 74, 0.08);
    }
    .overview-label {
      color: var(--muted);
      font-size: 12px;
      line-height: 1.4;
      margin-bottom: 14px;
    }
    .overview-value {
      font-size: 24px;
      line-height: 1.12;
      font-weight: 800;
      margin-bottom: 10px;
      word-break: break-word;
    }
    .overview-meta {
      color: rgba(255, 255, 255, 0.64);
      font-size: 12px;
      line-height: 1.4;
    }
    .section-head {
      padding: 16px 18px 14px;
      border-bottom: 1px solid var(--line);
      display: flex;
      gap: 12px;
      justify-content: space-between;
      align-items: start;
    }
    .section-title {
      margin: 0;
      font-size: 18px;
      font-weight: 800;
    }
    .section-subtitle {
      margin-top: 8px;
      color: var(--muted);
      font-size: 13px;
      line-height: 1.45;
    }
    .ghost-button {
      border: 1px solid rgba(255, 255, 255, 0.08);
      background: rgba(255, 255, 255, 0.04);
      color: var(--text);
      border-radius: 999px;
      padding: 9px 13px;
      font-size: 12px;
      font-weight: 700;
      cursor: pointer;
      flex-shrink: 0;
    }
    .list {
      padding: 12px;
      display: grid;
      gap: 10px;
    }
    .node-card {
      padding: 14px;
      border-radius: 20px;
      border: 1px solid rgba(255, 255, 255, 0.05);
      background: rgba(255, 255, 255, 0.03);
      cursor: pointer;
      transition: transform 0.18s ease, border-color 0.18s ease;
    }
    .node-card.active {
      border-color: rgba(255, 175, 74, 0.34);
      background: linear-gradient(180deg, rgba(255, 175, 74, 0.08), rgba(255, 255, 255, 0.03));
    }
    .node-card:hover {
      transform: translateY(-1px);
      border-color: rgba(255, 175, 74, 0.28);
    }
    .row {
      display: flex;
      justify-content: space-between;
      gap: 12px;
      align-items: start;
    }
    .name {
      margin: 0;
      font-size: 18px;
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
    .tags {
      margin-top: 12px;
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
    }
    .tag {
      padding: 6px 10px;
      border-radius: 999px;
      background: rgba(255, 255, 255, 0.04);
      color: var(--muted);
      font-size: 12px;
    }
    .metrics {
      margin-top: 12px;
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 10px;
    }
    .metric {
      padding: 10px 11px;
      border-radius: 16px;
      background: rgba(255, 255, 255, 0.03);
      border: 1px solid rgba(255, 255, 255, 0.05);
    }
    .metric-label {
      color: var(--muted);
      font-size: 11px;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      margin-bottom: 6px;
    }
    .metric-value {
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
    .summary-grid,
    .pairs {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 10px;
    }
    .summary-item,
    .pair {
      padding: 12px 13px;
      border-radius: 18px;
      background: rgba(255, 255, 255, 0.03);
      border: 1px solid rgba(255, 255, 255, 0.05);
    }
    .summary-item.full,
    .pair.full {
      grid-column: 1 / -1;
    }
    .summary-label,
    .pair-label {
      color: var(--muted);
      font-size: 11px;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      margin-bottom: 6px;
    }
    .summary-value,
    .pair-value {
      font-size: 15px;
      line-height: 1.45;
      font-weight: 700;
      word-break: break-word;
    }
    .group {
      display: grid;
      gap: 10px;
    }
    .group-title {
      color: var(--muted);
      font-size: 12px;
      letter-spacing: 0.1em;
      text-transform: uppercase;
    }
    .alerts {
      display: grid;
      gap: 10px;
    }
    .alert {
      padding: 12px 13px;
      border-radius: 18px;
      border: 1px solid rgba(255, 255, 255, 0.06);
      background: rgba(255, 255, 255, 0.03);
      line-height: 1.5;
    }
    .alert.critical {
      border-color: rgba(255, 110, 110, 0.34);
      background: rgba(255, 110, 110, 0.08);
    }
    .alert.warning {
      border-color: rgba(255, 198, 90, 0.34);
      background: rgba(255, 198, 90, 0.08);
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
      margin: 12px;
      padding: 18px;
      border-radius: 18px;
      text-align: center;
      color: var(--muted);
      background: rgba(255, 255, 255, 0.04);
      border: 1px solid rgba(255, 255, 255, 0.05);
    }
    @media (min-width: 720px) {
      body { padding: 16px 14px 26px; }
      .overview-grid { grid-template-columns: repeat(3, minmax(0, 1fr)); }
    }
    @media (max-width: 520px) {
      .title-row { align-items: stretch; }
      .refresh { width: 100%; }
    }
  </style>
</head>
<body>
  <div class="shell">
    <section class="panel">
      <div class="hero">
        <div class="eyebrow">ProxyPulse Web App</div>
        <div class="title-row">
          <div>
            <h1 class="title">节点总览</h1>
            <div class="subtitle">默认只看 6 张总览卡片。点哪一张，再往下一层看对应节点，再点节点才展开详细状态。</div>
          </div>
          <button class="refresh" id="refresh">刷新数据</button>
        </div>
      </div>
      <div class="overview-grid" id="overview"></div>
    </section>

    <section class="panel" id="list-panel">
      <div class="section-head">
        <div>
          <h2 class="section-title" id="list-title">下一层</h2>
          <div class="section-subtitle" id="list-subtitle">从上面的卡片进入节点列表。</div>
        </div>
        <button class="ghost-button" id="back" hidden>返回总览</button>
      </div>
      <div class="list" id="list"></div>
    </section>

    <section class="panel detail" id="detail"></section>
  </div>

  <script>
    const tg = window.Telegram?.WebApp;
    if (tg) {
      tg.ready();
      tg.expand();
      tg.setHeaderColor('#141b2a');
      tg.setBackgroundColor('#0c1119');
    }

    const overviewEl = document.getElementById('overview');
    const listEl = document.getElementById('list');
    const listTitleEl = document.getElementById('list-title');
    const listSubtitleEl = document.getElementById('list-subtitle');
    const detailEl = document.getElementById('detail');
    const refreshBtn = document.getElementById('refresh');
    const backBtn = document.getElementById('back');
    const statusClass = { '在线': 'online', '待接入': 'pending', '离线': 'offline' };
    const searchParams = new URLSearchParams(window.location.search);

    const overviewConfig = [
      { id: 'all', label: '节点总数', valueKey: 'node_count', meta: '全部节点' },
      { id: 'online', label: '在线', valueKey: 'online_count', meta: '当前在线节点' },
      { id: 'offline', label: '离线', valueKey: 'offline_count', meta: '当前离线节点' },
      { id: 'pending', label: '待接入', valueKey: 'pending_count', meta: '尚未完成接入' },
      { id: 'traffic', label: '24h 合计', valueKey: 'total_traffic_24h', meta: '按 24h 总量排序' },
      { id: 'alerts', label: '活动告警', valueKey: 'active_alert_count', meta: '仅显示有告警节点' },
    ];

    let latestOverview = null;
    let latestNodes = [];
    let selectedOverview = null;
    let selectedNode = null;

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

    function getListMeta(id) {
      if (id === 'all') return ['全部节点', '查看所有节点，按名称排序。'];
      if (id === 'online') return ['在线节点', '只显示当前在线的节点。'];
      if (id === 'offline') return ['离线节点', '优先处理最近掉线的节点。'];
      if (id === 'pending') return ['待接入节点', '这些节点还没有完成注册或上报。'];
      if (id === 'traffic') return ['24h 流量排行', '按近 24 小时总流量从高到低排序。'];
      return ['活动告警', '只显示当前存在活动告警的节点。'];
    }

    function getNodesForOverview(id) {
      const nodes = [...latestNodes];
      if (id === 'all') {
        return nodes.sort((left, right) => left.name.localeCompare(right.name));
      }
      if (id === 'online') {
        return nodes.filter((node) => node.is_online).sort((left, right) => left.name.localeCompare(right.name));
      }
      if (id === 'offline') {
        return nodes.filter((node) => node.status === 'offline').sort((left, right) => left.name.localeCompare(right.name));
      }
      if (id === 'pending') {
        return nodes.filter((node) => node.status === 'pending').sort((left, right) => left.name.localeCompare(right.name));
      }
      if (id === 'traffic') {
        return nodes.sort((left, right) => (right.total_24h_bytes || 0) - (left.total_24h_bytes || 0));
      }
      return nodes
        .filter((node) => node.active_alert_count > 0)
        .sort((left, right) => right.active_alert_count - left.active_alert_count);
    }

    function renderOverview() {
      if (!latestOverview) {
        overviewEl.innerHTML = '<div class="error">总览加载失败。</div>';
        return;
      }
      overviewEl.innerHTML = overviewConfig.map((card) => `
        <button class="overview-card ${selectedOverview === card.id ? 'active' : ''}" data-overview="${card.id}">
          <div class="overview-label">${escapeHtml(card.label)}</div>
          <div class="overview-value">${escapeHtml(latestOverview[card.valueKey])}</div>
          <div class="overview-meta">${escapeHtml(card.meta)}</div>
        </button>
      `).join('');
    }

    function renderList() {
      if (!selectedOverview) {
        listTitleEl.textContent = '下一层';
        listSubtitleEl.textContent = '从上面的总览卡片进入节点列表。';
        backBtn.hidden = true;
        listEl.innerHTML = '<div class="empty">先点一张总览卡片，再进入下一层。</div>';
        detailEl.classList.remove('active');
        detailEl.innerHTML = '';
        return;
      }

      const [title, subtitle] = getListMeta(selectedOverview);
      const nodes = getNodesForOverview(selectedOverview);
      listTitleEl.textContent = title;
      listSubtitleEl.textContent = subtitle;
      backBtn.hidden = false;

      if (!nodes.length) {
        listEl.innerHTML = '<div class="empty">这个分组下当前没有节点。</div>';
        detailEl.classList.remove('active');
        detailEl.innerHTML = '';
        return;
      }

      listEl.innerHTML = nodes.map((node) => `
        <article class="node-card ${selectedNode === node.name ? 'active' : ''}" data-node="${escapeHtml(node.name)}">
          <div class="row">
            <div>
              <h3 class="name">${escapeHtml(node.name)}</h3>
              <div class="identity">${escapeHtml(node.hostname)}<br>${escapeHtml(node.platform)}</div>
            </div>
            ${statusBadge(node.status_label)}
          </div>
          <div class="tags">
            <span class="tag">${escapeHtml(node.last_seen)}</span>
            <span class="tag">${escapeHtml(node.network_interface)}</span>
            <span class="tag">${escapeHtml(node.alert_badge)}</span>
          </div>
          <div class="metrics">
            <div class="metric">
              <div class="metric-label">资源</div>
              <div class="metric-value">CPU ${escapeHtml(node.cpu)}<br>内存 ${escapeHtml(node.memory)}</div>
            </div>
            <div class="metric">
              <div class="metric-label">流量</div>
              <div class="metric-value">24h ${escapeHtml(node.total_24h)}<br>↓ ${escapeHtml(node.rx_rate)} / ↑ ${escapeHtml(node.tx_rate)}</div>
            </div>
          </div>
        </article>
      `).join('');
    }

    function renderDetail(detail) {
      detailEl.classList.add('active');
      detailEl.innerHTML = `
        <div class="section-head">
          <div>
            <h2 class="section-title">${escapeHtml(detail.name)}</h2>
            <div class="section-subtitle">${escapeHtml(detail.hostname)} · ${escapeHtml(detail.platform)}</div>
          </div>
          ${statusBadge(detail.status_label)}
        </div>
        <div class="detail-body">
          <div class="summary-grid">
            ${detail.highlights.map((item) => `
              <div class="summary-item">
                <div class="summary-label">${escapeHtml(item.label)}</div>
                <div class="summary-value">${escapeHtml(item.value)}</div>
              </div>
            `).join('')}
            <div class="summary-item full">
              <div class="summary-label">IP</div>
              <div class="summary-value">${escapeHtml(detail.ips)}</div>
            </div>
          </div>

          ${detail.sections.map((section) => `
            <div class="group">
              <div class="group-title">${escapeHtml(section.title)}</div>
              <div class="pairs">
                ${section.items.map((item) => pair(item.label, item.value, item.full)).join('')}
              </div>
            </div>
          `).join('')}

          <div class="group">
            <div class="group-title">当前告警</div>
            <div class="alerts">
              ${detail.alerts.length
                ? detail.alerts.map((alert) => `
                    <div class="alert ${escapeHtml(alert.severity)}">
                      <strong>${alert.severity === 'critical' ? 'Critical' : 'Warning'}</strong>
                      <div>${escapeHtml(alert.text)}</div>
                    </div>
                  `).join('')
                : '<div class="empty" style="margin:0;">当前没有活动告警。</div>'
              }
            </div>
          </div>
        </div>
      `;
    }

    async function api(path) {
      const initData = tg?.initData || '';
      const headers = { 'X-Telegram-Init-Data': initData };
      const fallbackUser = searchParams.get('uid');
      const fallbackToken = searchParams.get('sig');
      if (!initData && fallbackUser && fallbackToken) {
        headers['X-ProxyPulse-Web-User'] = fallbackUser;
        headers['X-ProxyPulse-Web-Token'] = fallbackToken;
      }
      const response = await fetch(path, { headers });
      if (!response.ok) {
        const text = await response.text();
        throw new Error(text || `HTTP ${response.status}`);
      }
      return response.json();
    }

    async function loadOverview() {
      try {
        const data = await api('/app/data/overview');
        latestOverview = data.overview;
        latestNodes = data.nodes;
        if (selectedNode && !latestNodes.some((node) => node.name === selectedNode)) {
          selectedNode = null;
        }
        renderOverview();
        renderList();
      } catch (error) {
        overviewEl.innerHTML = `<div class="error">总览加载失败：${escapeHtml(error.message)}</div>`;
        listEl.innerHTML = '<div class="empty">暂时无法加载节点列表。</div>';
        detailEl.classList.remove('active');
        detailEl.innerHTML = '';
      }
    }

    async function loadDetail(name) {
      selectedNode = name;
      renderList();
      try {
        const detail = await api(`/app/data/nodes/${encodeURIComponent(name)}`);
        renderDetail(detail);
      } catch (error) {
        detailEl.classList.add('active');
        detailEl.innerHTML = `<div class="error">节点详情加载失败：${escapeHtml(error.message)}</div>`;
      }
    }

    overviewEl.addEventListener('click', (event) => {
      const card = event.target.closest('.overview-card');
      if (!card) {
        return;
      }
      selectedOverview = card.dataset.overview;
      selectedNode = null;
      renderOverview();
      renderList();
      window.scrollTo({ top: document.getElementById('list-panel').offsetTop - 8, behavior: 'smooth' });
    });

    listEl.addEventListener('click', async (event) => {
      const card = event.target.closest('.node-card');
      if (!card) {
        return;
      }
      await loadDetail(card.dataset.node);
    });

    backBtn.addEventListener('click', () => {
      selectedOverview = null;
      selectedNode = null;
      renderOverview();
      renderList();
      window.scrollTo({ top: 0, behavior: 'smooth' });
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
