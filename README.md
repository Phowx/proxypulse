# ProxyPulse

ProxyPulse 是一个以 Telegram 为操作入口的私人节点监控工具：Server 提供 API 与 Bot，Agent 负责按需采集资源和流量数据。

## 功能

- 节点注册、在线状态、资源指标与流量统计
- 每日流量报告和流量套餐周期管理
- Telegram 节点管理、诊断与 Cloudflare DNS 管理
- 可选采集范围与隐私模式
- SQLite（默认）或 PostgreSQL
- Server / Agent 的交互式安装、状态查看和分角色卸载

## 快速部署

要求 Linux、systemd、Git 和 Python 3.11+。Debian/Ubuntu 缺少 Python 或 Caddy 时，交互脚本会安装所需组件。

```bash
git clone https://github.com/Phowx/proxypulse.git /opt/proxypulse
cd /opt/proxypulse
bash deploy/manage.sh
```

菜单将各项操作分开：

```text
1  安装或更新 Server
2  安装或更新 Agent
3  重新配置 Agent 采集范围
4  查看服务状态
5  卸载 Server
6  卸载 Agent
7  完全卸载 Server 与 Agent
0  退出
```

首次使用先安装 Server，按提示填写 Bot Token、管理员 Telegram ID、API 本地端口、公网 Server URL 和 Caddy 公网端口。API 默认只监听 `127.0.0.1:8080`，Caddy 自动反代公网入口。然后向 Bot 发送 `/enroll my-node` 获取 Agent 接入令牌，再在节点机器安装 Agent。

公网 URL 只填写协议和域名/IP，不带路径；脚本会按选择的公网端口生成最终 URL。例如域名 `https://monitor.example.com` 配合端口 `8443`，最终入口为 `https://monitor.example.com:8443`。请在云安全组或防火墙中放行该端口；使用公网 HTTPS 域名时，还需确保域名解析正确，并允许 Caddy 完成证书签发所需的验证连接。

## 采集范围

安装或重新配置 Agent 时可选择：

- 标准：主机身份、CPU、内存、根磁盘、网络流量、运行时长
- 隐私：关闭主机名、系统信息和本地 IP，其余保持启用
- 流量：仅采集网络累计流量和运行时长
- 自定义：从以上六项逐项选择

网络流量依赖运行时长识别重启和计数器重置；选择网络时会自动启用运行时长。未启用的项目在 Bot 中显示为“未启用”，不会显示伪造的零值。

## 自动化入口

现有无菜单脚本仍可单独调用；配置不存在时会从模板创建，已有配置不会被覆盖：

```bash
bash deploy/install-server.sh
bash deploy/install-agent.sh

bash deploy/uninstall.sh --server
bash deploy/uninstall.sh --agent
bash deploy/uninstall.sh --all
```

非交互卸载可加 `--yes`。仅卸载 Server 时默认保留 SQLite 数据，需要同时删除时使用：

```bash
bash deploy/uninstall.sh --server --yes --delete-data
```

`bash deploy/uninstall.sh --yes` 为兼容入口，等同完全卸载。

## 状态与日志

可通过交互菜单查看 ProxyPulse 服务的安装、启用和运行状态，也可直接运行：

```bash
sudo systemctl status proxypulse-api proxypulse-bot proxypulse-agent caddy
sudo journalctl -u proxypulse-api -u proxypulse-bot -f
sudo journalctl -u proxypulse-agent -f
```

## 卸载说明

- 卸载 Server：移除 API/Bot、Server 配置和 ProxyPulse 的 Caddy 反代，可选择是否删除默认 SQLite 数据
- 卸载 Agent：移除 Agent、Agent 配置和本地接入状态
- 完全卸载：移除 Server、Agent、全部配置、状态、默认数据库和共享虚拟环境

源码仓库不会自动删除。所有角色卸载完成且没有保留数据库时，脚本会打印经过转义的源码删除命令。Caddy 软件包、Caddy 的其他站点配置、外部数据库、防火墙规则和系统 Python 不在清理范围内。

配置模板：

- Server：`deploy/env/server.env.example`
- Agent：`deploy/env/agent.env.example`
