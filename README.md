# ProxyPulse

ProxyPulse 是一个以 Telegram 为核心入口的私人代理节点监控工具，当前由三个进程组成：

- `proxypulse.api`：控制面，负责接收 Agent 注册、心跳和指标上报。
- `proxypulse.bot`：Telegram Bot，负责节点接入、查询、告警通知和日常操作。
- `proxypulse.agent`：部署在每台节点上的轻量采集器。

## 已实现功能

- 在 Telegram 中生成一次性接入令牌。
- Agent 注册后分配长期 `agent token`。
- 心跳上报和资源指标快照。
- Telegram 内查看节点列表、节点详情、告警和接入信息。
- CPU、内存、磁盘资源阈值告警。
- 节点离线检测，以及离线/恢复通知。
- 最近 24 小时流量汇总和每日流量日报。
- 按节点配置流量套餐，支持按月重置、按固定天数重置和手动校准已用流量。
- 默认使用 SQLite，数据模型兼容 PostgreSQL。
- 提供服务端和 Agent 的 `systemd` 安装脚本。
- 支持在 Telegram 中删除节点，并清理关联数据。

## 快速开始

1. 创建虚拟环境并安装依赖：

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

2. 将 `.env.example` 复制为 `.env`，至少填写：

- `PROXYPULSE_BOT_TOKEN`
- `PROXYPULSE_ADMIN_TELEGRAM_IDS`

3. 启动控制面：

```bash
python -m proxypulse.api
```

4. 另开一个终端启动 Telegram Bot：

```bash
python -m proxypulse.bot
```

5. 在 Telegram 中发送：

```text
/enroll my-node
```

Bot 会返回接入令牌和一条 Agent 启动示例命令。

6. 在目标节点上运行 Agent：

```bash
PROXYPULSE_SERVER_URL=http://YOUR_SERVER:8080 \
PROXYPULSE_AGENT_NAME=my-node \
PROXYPULSE_AGENT_ENROLLMENT_TOKEN=TOKEN_FROM_TELEGRAM \
python -m proxypulse.agent
```

7. 常用 Telegram 命令：

- `/nodes`
- `/node my-node`
- `/delete_node my-node`
- `/alerts`
- `/traffic`
- `/daily`
- `/quota my-node`

## 命令说明

- `/start`：打开控制台首页。
- `/menu`：打开控制台菜单。
- `/enroll <node_name>`：创建或刷新一次性接入令牌。
- `/nodes`：查看已接入节点和最新资源指标。
- `/node <node_name>`：查看单个节点详情。
- `/delete_node <node_name>`：删除节点，并在确认后清理其历史指标、告警和流量套餐配置。
- `/alerts`：查看当前活动告警。
- `/traffic`：查看最近 24 小时流量汇总。
- `/daily`：查看上一自然日流量日报。
- `/quota <node_name>`：查看节点当前流量套餐状态。
- `/quota_monthly <node_name> <limitGiB> <reset_day> <HH:MM>`：设置按月重置的流量套餐。
- `/quota_interval <node_name> <limitGiB> <days> <YYYY-MM-DDTHH:MM>`：设置按固定天数循环重置的流量套餐。
- `/quota_calibrate <node_name> <usedGiB>`：手动校准本周期已用流量。
- `/quota_clear <node_name>`：清除节点流量套餐配置。

## 告警行为

- 当 CPU、内存、磁盘使用率超过阈值时，会触发资源告警。
- 当节点超过 `PROXYPULSE_OFFLINE_AFTER_SECONDS` 未上报时，会触发离线告警。
- 告警触发和恢复都会通过 Telegram 发送给所有管理员。

关键环境变量：

- `PROXYPULSE_RESOURCE_ALERTS_ENABLED`
- `PROXYPULSE_CPU_ALERT_THRESHOLD`
- `PROXYPULSE_MEMORY_ALERT_THRESHOLD`
- `PROXYPULSE_DISK_ALERT_THRESHOLD`
- `PROXYPULSE_OFFLINE_AFTER_SECONDS`
- `PROXYPULSE_ALERT_SCAN_INTERVAL_SECONDS`
- `PROXYPULSE_REPORT_TIMEZONE`
- `PROXYPULSE_DAILY_REPORT_HOUR`
- `PROXYPULSE_DAILY_REPORT_MINUTE`

## 流量报表

- `/traffic` 会根据累计 `RX/TX` 快照计算滚动最近 24 小时流量。
- `/daily` 会根据配置时区展示上一自然日的日报。
- 到达配置的日报时间后，Bot 也会自动推送一次日报。
- 报表按节点分组展示，不做节点排行。

## 流量套餐

- 流量套餐以节点为单位配置。
- 每个节点可以选择按月重置，或每隔 N 天重置。
- 套餐使用量基于当前套餐周期内的累计 `RX/TX` 快照增量计算。
- 手动校准会记录“当前周期已用流量”，后续新的上报会继续在此基础上累加。
- 节点详情和 `/quota <node_name>` 都会显示套餐状态。
- 未显式带时区的时间，按 `PROXYPULSE_REPORT_TIMEZONE` 解释。

## 资源告警开关

- `PROXYPULSE_RESOURCE_ALERTS_ENABLED=true` 时，会启用 CPU、内存、磁盘阈值告警。
- 设置为 `false` 后，会保留离线告警，但不再生成新的资源阈值告警。
- 关闭后，当前处于活动状态的 CPU、内存、磁盘告警会在下一次指标上报时自动恢复关闭。

## 使用 `systemd` 部署

仓库内已包含安装脚本，会自动创建本地虚拟环境、安装包、渲染 `systemd` unit 文件，并把环境变量模板复制到 `/etc/proxypulse`。

服务端：

```bash
sudo bash deploy/install-server.sh
sudoedit /etc/proxypulse/server.env
sudo systemctl restart proxypulse-api proxypulse-bot
```

Agent 端：

```bash
sudo bash deploy/install-agent.sh
sudoedit /etc/proxypulse/agent.env
sudo systemctl restart proxypulse-agent
```

模板和 unit 文件位置：

- `deploy/env/server.env.example`
- `deploy/env/agent.env.example`
- `deploy/systemd/proxypulse-api.service.in`
- `deploy/systemd/proxypulse-bot.service.in`
- `deploy/systemd/proxypulse-agent.service.in`

## 说明

- 第一版不包含 `Docker` 管理能力。
- 认证模型是单管理员、基于 Telegram 用户 ID。
- Agent 只负责上报监控数据，不执行任意远程命令。
