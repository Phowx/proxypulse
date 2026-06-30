# ProxyPulse

ProxyPulse 是一个以 Telegram 为核心入口的私人代理节点监控工具，当前由三个进程组成：

- `proxypulse.api`：控制面，负责接收 Agent 注册、心跳和指标上报。
- `proxypulse.bot`：Telegram Bot，负责节点接入、查询、日报推送和日常操作。
- `proxypulse.agent`：部署在每台节点上的轻量采集器。

## 已实现功能

- 在 Telegram 中生成一次性接入令牌。
- Agent 注册后分配长期 `agent token`。
- Agent 启动时同步主机身份，运行期间上报精简资源指标快照。
- Telegram 内通过原生富文本卡片查看节点概览与详情、流量报表、套餐状态、诊断结果、DNS 管理和接入信息。
- 节点在线状态检测（仅用于界面状态，不发送告警）。
- 最近 24 小时流量汇总和每日流量日报。
- 按节点配置流量套餐，支持按月重置、按固定天数重置和手动校准已用流量。
- 在 Telegram 中管理 Cloudflare DNS，支持查看、创建、更新和删除 `A` / `AAAA` / `CNAME` / `TXT` 记录。
- 默认使用 SQLite，数据模型兼容 PostgreSQL。
- 提供服务端和 Agent 的 `systemd` 安装脚本。
- 支持在 Telegram 中删除节点，并清理关联数据。

## 使用 `systemd` 部署

仓库内已包含安装脚本，会自动创建本地虚拟环境、安装 Python 包、渲染 `systemd` unit 文件，并把环境变量模板复制到 `/etc/proxypulse`。

适用环境：

- Linux 服务器，使用 `systemd` 管理服务。
- Python 3.11 或更高版本。
- 服务端需要能被 Agent 访问；可以直接开放 `8080` 端口，也可以用 Nginx/Caddy 反向代理到 `127.0.0.1:8080`。

### 安装 Server

Server 由两个服务组成：

- `proxypulse-api`：接收 Agent 注册、心跳和指标上报。
- `proxypulse-bot`：Telegram Bot，负责菜单、查询、日报推送和 DNS 管理。

1. 准备运行环境。

Debian / Ubuntu 可以先安装基础依赖：

```bash
sudo apt update
sudo apt install -y git python3 python3-venv python3-pip
```

确认 Python 版本：

```bash
python3 --version
```

2. 获取代码。

建议把服务端代码放在固定目录，例如：

```bash
sudo mkdir -p /opt/proxypulse
sudo chown "$USER":"$USER" /opt/proxypulse
git clone <repo-url> /opt/proxypulse
cd /opt/proxypulse
```

如果服务器上已经有这个仓库，直接进入仓库目录即可。

3. 执行安装脚本。

```bash
bash deploy/install-server.sh
```

脚本会完成这些操作：

- 在当前仓库目录创建 `.venv`。
- 执行 `pip install .` 安装 ProxyPulse。
- 首次安装时复制 `deploy/env/server.env.example` 到 `/etc/proxypulse/server.env`。
- 生成并安装：
  - `/etc/systemd/system/proxypulse-api.service`
  - `/etc/systemd/system/proxypulse-bot.service`
- 执行 `systemctl daemon-reload`。
- 设置 `proxypulse-api` 和 `proxypulse-bot` 开机自启。

4. 编辑服务端配置。

```bash
sudoedit /etc/proxypulse/server.env
```

至少需要填写：

```env
PROXYPULSE_BOT_TOKEN=你的 Telegram Bot Token
PROXYPULSE_ADMIN_TELEGRAM_IDS=你的 Telegram 用户 ID
PROXYPULSE_SERVER_URL=https://你的服务端地址
```

常用配置说明：

| 配置项 | 说明 |
|---|---|
| `PROXYPULSE_DATABASE_URL` | 数据库地址；默认使用仓库目录下的 SQLite 文件。 |
| `PROXYPULSE_BOT_TOKEN` | Telegram Bot Token，从 BotFather 获取。 |
| `PROXYPULSE_ADMIN_TELEGRAM_IDS` | 允许使用 Bot 的 Telegram 用户 ID，多个 ID 用英文逗号分隔。 |
| `PROXYPULSE_SERVER_URL` | Agent 访问 Server 的地址；如果有反向代理，填公网 HTTPS 地址。 |
| `PROXYPULSE_EXTERNAL_NOTIFY_SECRET` | 外部网络通知接口的 Bearer Token；不使用可留空。 |
| `PROXYPULSE_CLOUDFLARE_API_TOKEN` | Cloudflare DNS 管理 API Token；不使用 DNS 管理可留空。 |
| `PROXYPULSE_CLOUDFLARE_ZONES` | 可管理的 Cloudflare Zone JSON；不使用 DNS 管理可保留 `{}`。 |
| `PROXYPULSE_REPORT_TIMEZONE` | 日报和套餐周期使用的时区，默认 `Asia/Shanghai`。 |
| `PROXYPULSE_DAILY_REPORT_HOUR` / `PROXYPULSE_DAILY_REPORT_MINUTE` | 自动日报默认推送时间。 |

Cloudflare Zone 示例：

```env
PROXYPULSE_CLOUDFLARE_ZONES={"main":{"zone_id":"xxx","zone_name":"example.com"}}
```

5. 启动服务。

```bash
sudo systemctl restart proxypulse-api proxypulse-bot
```

检查状态：

```bash
sudo systemctl status proxypulse-api proxypulse-bot
```

查看日志：

```bash
sudo journalctl -u proxypulse-api -f
sudo journalctl -u proxypulse-bot -f
```

本机健康检查：

```bash
curl http://127.0.0.1:8080/health
```

返回 `{"status":"ok"}` 说明 API 已启动。

6. 在 Telegram 中验证。

向 Bot 发送：

```text
/start
```

如果能看到 ProxyPulse 控制台菜单，说明 Server 和 Bot 都已可用。

7. 生成 Agent 接入令牌。

```text
/enroll my-node
```

Bot 会返回一次性接入令牌和 Agent 启动示例命令。后续安装 Agent 时会用到这个令牌。

### 安装 Agent

Agent 部署在每台被监控节点上，负责采集 CPU、内存、磁盘、负载、运行时间、网卡和收发字节，并定时上报到 Server。

1. 准备运行环境。

```bash
sudo apt update
sudo apt install -y git python3 python3-venv python3-pip
```

确认 Python 版本为 3.11 或更高：

```bash
python3 --version
```

2. 获取代码。

Agent 端也需要一份 ProxyPulse 代码。建议放在固定目录，例如：

```bash
sudo mkdir -p /opt/proxypulse
sudo chown "$USER":"$USER" /opt/proxypulse
git clone <repo-url> /opt/proxypulse
cd /opt/proxypulse
```

如果节点上已经有仓库，直接进入仓库目录即可。

3. 执行安装脚本。

```bash
bash deploy/install-agent.sh
```

脚本会完成这些操作：

- 在当前仓库目录创建 `.venv`。
- 执行 `pip install .` 安装 ProxyPulse。
- 创建 `/var/lib/proxypulse` 用于保存 Agent 本地状态。
- 首次安装时复制 `deploy/env/agent.env.example` 到 `/etc/proxypulse/agent.env`。
- 生成并安装 `/etc/systemd/system/proxypulse-agent.service`。
- 执行 `systemctl daemon-reload`。
- 设置 `proxypulse-agent` 开机自启。

4. 编辑 Agent 配置。

```bash
sudoedit /etc/proxypulse/agent.env
```

首次接入至少需要填写：

```env
PROXYPULSE_SERVER_URL=https://你的服务端地址
PROXYPULSE_AGENT_NAME=my-node
PROXYPULSE_AGENT_ENROLLMENT_TOKEN=Telegram 里 /enroll 返回的一次性令牌
PROXYPULSE_AGENT_STATE_PATH=/var/lib/proxypulse/agent-state.json
PROXYPULSE_POLL_INTERVAL_SECONDS=30
```

常用配置说明：

| 配置项 | 说明 |
|---|---|
| `PROXYPULSE_SERVER_URL` | Server 地址，必须和服务端可访问地址一致。 |
| `PROXYPULSE_AGENT_NAME` | 节点名称；建议和 `/enroll <节点名>` 保持一致。 |
| `PROXYPULSE_AGENT_ENROLLMENT_TOKEN` | 首次注册使用的一次性令牌。注册成功后 Agent 会把长期 token 写入本地状态文件。 |
| `PROXYPULSE_AGENT_STATE_PATH` | Agent 本地状态文件路径，默认建议使用 `/var/lib/proxypulse/agent-state.json`。 |
| `PROXYPULSE_POLL_INTERVAL_SECONDS` | 指标上报间隔，默认 `30` 秒。 |
| `PROXYPULSE_NETWORK_INTERFACE` | 指定网卡名称，例如 `eth0`；留空时按策略自动选择。 |
| `PROXYPULSE_NETWORK_INTERFACE_STRATEGY` | 网卡统计策略：`auto`、`fixed` 或 `aggregate`。 |

网卡配置建议：

- 大多数普通 VPS 保持默认即可：

```env
PROXYPULSE_NETWORK_INTERFACE=
PROXYPULSE_NETWORK_INTERFACE_STRATEGY=auto
```

- 如果节点有多块网卡，且你只想统计公网网卡，建议固定网卡：

```env
PROXYPULSE_NETWORK_INTERFACE=eth0
PROXYPULSE_NETWORK_INTERFACE_STRATEGY=fixed
```

- 如果你明确想统计所有非本地网卡的合计流量，可以使用：

```env
PROXYPULSE_NETWORK_INTERFACE=
PROXYPULSE_NETWORK_INTERFACE_STRATEGY=aggregate
```

5. 启动 Agent。

```bash
sudo systemctl restart proxypulse-agent
```

检查状态：

```bash
sudo systemctl status proxypulse-agent
```

查看日志：

```bash
sudo journalctl -u proxypulse-agent -f
```

首次启动成功后，Agent 会：

1. 使用 `PROXYPULSE_AGENT_ENROLLMENT_TOKEN` 向 Server 注册。
2. 把长期 `agent token` 写入 `PROXYPULSE_AGENT_STATE_PATH`。
3. 开始按 `PROXYPULSE_POLL_INTERVAL_SECONDS` 上报指标。

注册成功后，通常不需要再更换 `PROXYPULSE_AGENT_ENROLLMENT_TOKEN`；只要本地状态文件还在，后续重启会直接使用长期 token。

6. 在 Telegram 中确认节点上线。

发送：

```text
/nodes
```

如果能看到新节点为在线状态，说明 Agent 接入完成。

### 升级

服务端升级：

```bash
cd /opt/proxypulse
git pull
bash deploy/install-server.sh
sudo systemctl restart proxypulse-api proxypulse-bot
```

Agent 升级：

```bash
cd /opt/proxypulse
git pull
bash deploy/install-agent.sh
sudo systemctl restart proxypulse-agent
```

升级脚本不会覆盖已经存在的 `/etc/proxypulse/server.env` 和 `/etc/proxypulse/agent.env`，已有配置会保留。

### 常见问题

- Bot 启动失败：优先检查 `PROXYPULSE_BOT_TOKEN` 是否填写，以及 `PROXYPULSE_ADMIN_TELEGRAM_IDS` 是否是数字 ID。
- Agent 注册失败：检查 `PROXYPULSE_SERVER_URL` 是否能从 Agent 节点访问，以及 `/enroll` 返回的一次性令牌是否填写正确。
- Agent 一直离线：检查 `proxypulse-agent` 日志、Server 防火墙、反向代理和服务端 `proxypulse-api` 状态。
- `/nodes` 没有指标：确认 Agent 已成功注册，并且 `PROXYPULSE_POLL_INTERVAL_SECONDS` 后至少完成过一次指标上报。
- DNS 管理不可用：检查 `PROXYPULSE_CLOUDFLARE_API_TOKEN` 和 `PROXYPULSE_CLOUDFLARE_ZONES`，每个域名必须配置正确且唯一的 `zone_id`。

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
