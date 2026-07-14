# README and Full Uninstall Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the verbose deployment guide with a concise operational README and add one safe, idempotent command that completely removes both ProxyPulse Server and Agent installation artifacts.

**Architecture:** A standalone Bash entry point owns all uninstall behavior so the two install scripts remain single-purpose. Python `unittest` integration tests execute a copied script in a temporary repository with a fake `sudo`, which verifies destructive command construction without changing the test host.

**Tech Stack:** Bash 4+, systemd command line, Python 3.11 `unittest`, Markdown

## Global Constraints

- Uninstall Server and Agent together; there is no role selector.
- Remove all three systemd services, `/etc/proxypulse`, `/var/lib/proxypulse`, the repository `.venv`, and the default `proxypulse.db`.
- Keep the source repository and print, but never execute, its shell-safe deletion command.
- Require confirmation unless `--yes` or `-y` is supplied.
- Do not delete custom databases, reverse-proxy configuration, firewall rules, system Python, or other external resources.
- Repeated uninstall runs must succeed when installation artifacts are already absent.

---

### Task 1: Complete Server and Agent Uninstaller

**Files:**
- Create: `tests/test_uninstall_script.py`
- Create: `deploy/uninstall.sh`

**Interfaces:**
- Consumes: the install layout created by `deploy/install-server.sh` and `deploy/install-agent.sh`
- Produces: `bash deploy/uninstall.sh [--yes|-y|--help]`; exit `0` for success, cancellation, and help, exit `2` for invalid arguments

- [ ] **Step 1: Write the failing confirmation test**

Create `tests/test_uninstall_script.py` with the initial cancellation contract:

```python
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from unittest import TestCase


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT_ROOT / "deploy" / "uninstall.sh"


class UninstallScriptTests(TestCase):
    def test_declining_confirmation_makes_no_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            command_log = Path(temp_dir) / "commands.log"
            result = subprocess.run(
                ["bash", str(SCRIPT)],
                input="n\n",
                text=True,
                capture_output=True,
                env={**os.environ, "COMMAND_LOG": str(command_log)},
                check=False,
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Uninstall cancelled.", result.stdout)
        self.assertFalse(command_log.exists())
```

- [ ] **Step 2: Run the cancellation test and verify RED**

Run:

```bash
python -m unittest tests.test_uninstall_script.UninstallScriptTests.test_declining_confirmation_makes_no_changes -v
```

Expected: `FAIL` because `deploy/uninstall.sh` does not exist and Bash returns a non-zero status.

- [ ] **Step 3: Implement argument handling and confirmation only**

Create `deploy/uninstall.sh` with `set -euo pipefail`, parse `--yes` / `-y` / `--help`, reject unknown arguments with exit `2`, and return before cleanup unless the answer lower-cases to `y` or `yes`. The cancellation path must print exactly `Uninstall cancelled.`.

```bash
#!/usr/bin/env bash
set -euo pipefail

assume_yes=false

usage() {
  cat <<'EOF'
Usage: bash deploy/uninstall.sh [--yes|-y]

Remove ProxyPulse Server and Agent services, configuration, state, the local
virtual environment, and the default SQLite database. The source is kept.
EOF
}

for argument in "$@"; do
  case "${argument}" in
    --yes|-y)
      assume_yes=true
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      printf 'Unknown argument: %s\n' "${argument}" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ "${assume_yes}" != true ]]; then
  cat <<'EOF'
This removes ProxyPulse Server and Agent services, configuration, and data.
The source repository will be kept.
EOF
  response=""
  read -r -p "Continue? [y/N] " response || true
  response="${response,,}"
  if [[ "${response}" != "y" && "${response}" != "yes" ]]; then
    echo "Uninstall cancelled."
    exit 0
  fi
fi
```

- [ ] **Step 4: Run the cancellation test and verify GREEN**

Run the command from Step 2.

Expected: `OK`.

- [ ] **Step 5: Add the failing full-cleanup and idempotency tests**

Extend the test class with a helper that copies the script to a temporary repository named `Proxy Pulse`, creates `.venv` and `proxypulse.db`, and places this fake `sudo` executable first on `PATH`:

```bash
#!/usr/bin/env bash
printf '%s\n' "$*" >> "${COMMAND_LOG}"
```

Add assertions that `--yes`:

```python
self.assertIn(
    "systemctl disable --now proxypulse-api.service proxypulse-bot.service proxypulse-agent.service",
    commands,
)
self.assertIn(
    "rm -f -- /etc/systemd/system/proxypulse-api.service /etc/systemd/system/proxypulse-bot.service /etc/systemd/system/proxypulse-agent.service",
    commands,
)
self.assertIn("systemctl daemon-reload", commands)
self.assertIn(
    "systemctl reset-failed proxypulse-api.service proxypulse-bot.service proxypulse-agent.service",
    commands,
)
self.assertIn("rm -rf -- /etc/proxypulse /var/lib/proxypulse", commands)
self.assertFalse((repo_dir / ".venv").exists())
self.assertFalse((repo_dir / "proxypulse.db").exists())
escaped_repo_dir = str(repo_dir).replace(" ", chr(92) + " ")
self.assertIn(
    f"sudo rm -rf -- {escaped_repo_dir}",
    result.stdout,
)
```

Run the same copied script a second time with `--yes` and assert a zero exit code to prove idempotency.

- [ ] **Step 6: Run the cleanup tests and verify RED**

Run:

```bash
python -m unittest tests.test_uninstall_script -v
```

Expected: cancellation passes; cleanup fails because the script does not yet remove any installation artifacts.

- [ ] **Step 7: Implement complete cleanup**

Append the following behavior after confirmation:

```bash
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICES=(
  proxypulse-api.service
  proxypulse-bot.service
  proxypulse-agent.service
)
UNIT_FILES=(
  /etc/systemd/system/proxypulse-api.service
  /etc/systemd/system/proxypulse-bot.service
  /etc/systemd/system/proxypulse-agent.service
)

sudo systemctl disable --now "${SERVICES[@]}" 2>/dev/null || true
sudo rm -f -- "${UNIT_FILES[@]}"
sudo systemctl daemon-reload
sudo systemctl reset-failed "${SERVICES[@]}" 2>/dev/null || true
sudo rm -rf -- /etc/proxypulse /var/lib/proxypulse
rm -rf -- "${ROOT_DIR}/.venv"
rm -f -- "${ROOT_DIR}/proxypulse.db"

cat <<'EOF'
ProxyPulse Server and Agent have been completely uninstalled.
The source repository was kept. To remove it too, run:
EOF
printf '  sudo rm -rf -- %q\n' "${ROOT_DIR}"
```

- [ ] **Step 8: Add help and invalid-argument tests**

Add one test asserting `--help` exits `0`, contains the usage line, and produces no command log. Add one test asserting `--unknown` exits `2`, writes `Unknown argument: --unknown` to stderr, and produces no command log.

- [ ] **Step 9: Run the script tests and Bash syntax check**

Run:

```bash
python -m unittest tests.test_uninstall_script -v
bash -n deploy/uninstall.sh
```

Expected: all uninstall tests report `OK`; Bash syntax check exits `0` without output.

- [ ] **Step 10: Mark the uninstaller executable and commit**

```bash
chmod +x deploy/uninstall.sh
git add docs/superpowers/plans/2026-07-14-readme-and-full-uninstall.md deploy/uninstall.sh tests/test_uninstall_script.py
git commit -m "feat: add complete ProxyPulse uninstaller"
```

### Task 2: Concise Deployment README

**Files:**
- Modify: `README.md`

**Interfaces:**
- Consumes: `deploy/install-server.sh`, `deploy/install-agent.sh`, `deploy/uninstall.sh`, and `deploy/env/*.env.example`
- Produces: a short Chinese quick-start guide whose commands match the three scripts

- [ ] **Step 1: Replace the long-form README**

Rewrite `README.md` with these exact sections and commands:

```markdown
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

```bash
sudo apt update
sudo apt install -y git python3 python3-venv python3-pip
git clone <repo-url> /opt/proxypulse
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
```

- [ ] **Step 2: Verify README references and size**

Run:

```bash
rg -n "install-server|install-agent|uninstall|server.env|agent.env|proxypulse.db" README.md
wc -l README.md
```

Expected: every referenced path and cleanup item appears; the README is under 130 lines.

- [ ] **Step 3: Commit the README rewrite**

```bash
git add README.md
git commit -m "docs: streamline deployment guide"
```

### Task 3: Full Verification

**Files:**
- Verify: `deploy/uninstall.sh`
- Verify: `tests/test_uninstall_script.py`
- Verify: `README.md`

**Interfaces:**
- Consumes: all deliverables from Tasks 1 and 2
- Produces: verification evidence for the complete repository state

- [ ] **Step 1: Run all automated tests**

```bash
python -m unittest discover -s tests -v
```

Expected: every test reports `ok` and the command exits `0`.

- [ ] **Step 2: Run static checks**

```bash
bash -n deploy/install-server.sh deploy/install-agent.sh deploy/uninstall.sh
git diff --check HEAD~2..HEAD
```

Expected: both commands exit `0` without output.

- [ ] **Step 3: Inspect final scope**

```bash
git status --short
git log -3 --oneline
```

Expected: the worktree is clean and the three newest commits are the design, uninstaller, and README commits.
