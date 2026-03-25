# ProxyPulse

ProxyPulse is a Telegram-first monitoring stack for personal proxy nodes. It is built around three processes:

- `proxypulse.api`: control plane that accepts agent registration and metric uploads.
- `proxypulse.bot`: Telegram bot for node enrollment and monitoring queries.
- `proxypulse.agent`: lightweight collector installed on each node.

## What is implemented

- Agent enrollment with one-time tokens generated from Telegram.
- Agent registration and persistent agent tokens.
- Heartbeat updates and metric snapshots.
- Telegram commands for node listing, node details, enrollment, and alert viewing.
- Threshold alerts for CPU, memory, and disk usage.
- Offline detection with Telegram notifications for down and recovery events.
- Rolling 24-hour traffic summary and scheduled daily traffic reports.
- SQLite-by-default storage with PostgreSQL-compatible SQLAlchemy models.
- `systemd` install helpers for the server and agent.

## Quick start

1. Create a virtualenv and install dependencies:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

2. Copy `.env.example` to `.env` and fill in `PROXYPULSE_BOT_TOKEN` and `PROXYPULSE_ADMIN_TELEGRAM_IDS`.

3. Start the control plane:

```bash
python -m proxypulse.api
```

4. Start the Telegram bot in another shell:

```bash
python -m proxypulse.bot
```

5. In Telegram, send:

```text
/enroll my-node
```

The bot will return an enrollment token and an example agent launch command.

6. On the target node, run the agent:

```bash
PROXYPULSE_SERVER_URL=http://YOUR_SERVER:8080 \
PROXYPULSE_AGENT_NAME=my-node \
PROXYPULSE_AGENT_ENROLLMENT_TOKEN=TOKEN_FROM_TELEGRAM \
python -m proxypulse.agent
```

7. Use Telegram commands:

- `/nodes`
- `/node my-node`
- `/alerts`
- `/traffic`
- `/daily`

## Command summary

- `/start`: show help.
- `/enroll <node_name>`: create or refresh a one-time enrollment token.
- `/nodes`: list known nodes and latest metrics.
- `/node <node_name>`: show details for a node.
- `/alerts`: show active alerts.
- `/traffic`: show the last 24 hours of traffic totals per node.
- `/daily`: show the previous daily traffic report on demand.

## Alerting behavior

- CPU, memory, and disk alerts are triggered when usage crosses the configured thresholds.
- Offline alerts are triggered when a node has not reported within `PROXYPULSE_OFFLINE_AFTER_SECONDS`.
- The bot sends both trigger and recovery notifications to every admin Telegram ID.

Key environment variables:

- `PROXYPULSE_RESOURCE_ALERTS_ENABLED`
- `PROXYPULSE_CPU_ALERT_THRESHOLD`
- `PROXYPULSE_MEMORY_ALERT_THRESHOLD`
- `PROXYPULSE_DISK_ALERT_THRESHOLD`
- `PROXYPULSE_OFFLINE_AFTER_SECONDS`
- `PROXYPULSE_ALERT_SCAN_INTERVAL_SECONDS`
- `PROXYPULSE_REPORT_TIMEZONE`
- `PROXYPULSE_DAILY_REPORT_HOUR`
- `PROXYPULSE_DAILY_REPORT_MINUTE`

## Traffic reports

- `/traffic` calculates a rolling 24-hour summary from stored cumulative RX/TX snapshots.
- `/daily` shows the previous calendar day's report in the configured timezone.
- The bot also sends one automatic daily report after the configured report time.
- Reports are grouped by node name; they do not rank nodes.

## Resource alert switch

- `PROXYPULSE_RESOURCE_ALERTS_ENABLED=true` enables CPU, memory, and disk threshold alerts.
- Setting it to `false` keeps offline alerts enabled but disables new resource threshold alerts.
- When disabled, existing active CPU/memory/disk alerts are resolved on the next metric upload.

## `systemd` deployment

The repository now includes install helpers that build a local virtualenv, install the package, render `systemd` unit files, and copy env templates into `/etc/proxypulse`.

Server side:

```bash
sudo bash deploy/install-server.sh
sudoedit /etc/proxypulse/server.env
sudo systemctl restart proxypulse-api proxypulse-bot
```

Agent side:

```bash
sudo bash deploy/install-agent.sh
sudoedit /etc/proxypulse/agent.env
sudo systemctl restart proxypulse-agent
```

Templates and unit files:

- `deploy/env/server.env.example`
- `deploy/env/agent.env.example`
- `deploy/systemd/proxypulse-api.service.in`
- `deploy/systemd/proxypulse-bot.service.in`
- `deploy/systemd/proxypulse-agent.service.in`

## Notes

- `Docker` control is intentionally out of scope for this first cut.
- Authentication is single-admin and Telegram ID based.
- The agent only exposes monitoring data; it does not execute arbitrary commands.
