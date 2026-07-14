# ProxyPulse

ProxyPulse 是一个以 Telegram 为操作入口的私人代理节点监控工具：Server 接收指标并提供 Bot 控制台，Agent 负责采集节点资源与流量数据。

## 功能

- 节点注册、在线状态、资源指标与流量统计
- 每日流量报告和流量套餐周期管理
- Telegram 节点管理、诊断与 Cloudflare DNS 管理
- SQLite（默认）或 PostgreSQL
- Server / Agent 的 systemd 部署与完整卸载

## 环境要求

- Linux + systemd
- Python 3.11+
- `git`、`python3-venv`、`python3-pip`
- Server 地址可被 Agent 访问；可开放 `8080`，或反向代理到 `127.0.0.1:8080`

```bash
sudo apt update
sudo apt install -y git python3 python3-venv python3-pip
git clone https://github.com/Phowx/proxypulse.git /opt/proxypulse
cd /opt/proxypulse
```

## 部署 Server

```bash
bash deploy/install-server.sh
sudoedit /etc/proxypulse/server.env
sudo systemctl restart proxypulse-api proxypulse-bot
```

至少配置：

```env
PROXYPULSE_BOT_TOKEN=你的 Telegram Bot Token
PROXYPULSE_ADMIN_TELEGRAM_IDS=你的 Telegram 用户 ID
PROXYPULSE_SERVER_URL=https://你的服务端地址
```

验证：

```bash
sudo systemctl status proxypulse-api proxypulse-bot
curl http://127.0.0.1:8080/health
```

向 Bot 发送 `/start` 打开控制台，发送 `/enroll my-node` 生成 Agent 的一次性接入令牌。

## 部署 Agent

```bash
bash deploy/install-agent.sh
sudoedit /etc/proxypulse/agent.env
sudo systemctl restart proxypulse-agent
```

至少配置：

```env
PROXYPULSE_SERVER_URL=https://你的服务端地址
PROXYPULSE_AGENT_NAME=my-node
PROXYPULSE_AGENT_ENROLLMENT_TOKEN=Bot 返回的一次性令牌
```

验证：

```bash
sudo systemctl status proxypulse-agent
sudo journalctl -u proxypulse-agent -f
```

随后在 Telegram 中发送 `/nodes` 确认节点上线。网卡策略等完整配置见 `deploy/env/agent.env.example`。

## 常用操作

```bash
# Server 日志
sudo journalctl -u proxypulse-api -u proxypulse-bot -f

# 升级当前机器上的角色
git pull
bash deploy/install-server.sh  # Server
bash deploy/install-agent.sh   # Agent
```

安装脚本不会覆盖已有的 `/etc/proxypulse/*.env`。

## 完整卸载

以下命令会同时卸载 Server 和 Agent，删除 systemd 服务、配置、Agent 状态、`.venv` 和默认的 `proxypulse.db`：

```bash
bash deploy/uninstall.sh

# 非交互执行
bash deploy/uninstall.sh --yes
```

源码仓库不会自动删除；卸载完成后脚本会打印源码删除命令。自定义数据库、反向代理、防火墙规则和系统 Python 不在清理范围内。

## 配置模板

- Server：`deploy/env/server.env.example`
- Agent：`deploy/env/agent.env.example`
