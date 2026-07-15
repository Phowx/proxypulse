#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "${ROOT_DIR}/deploy/lib/common.sh"

scope=""
assume_yes=false
delete_data=false

usage() {
  cat <<'EOF'
Usage: bash deploy/uninstall.sh [--yes|-y]
       bash deploy/uninstall.sh [--server|--agent|--all] [--yes|-y] [--delete-data]

--server       仅卸载 API 与 Bot；默认保留 SQLite 数据。
--agent        仅卸载 Agent 及其本地接入状态。
--all          完全卸载 Server 与 Agent，并删除默认 SQLite 数据。
--delete-data  与 --server 一起使用时同时删除默认 SQLite 数据。
--yes, -y      跳过普通确认；单独使用保持兼容，等同 --all --yes。

源码始终保留；没有角色再依赖源码时会打印手工删除命令。
EOF
}

set_scope() {
  local requested="$1"
  if [[ -n "${scope}" && "${scope}" != "${requested}" ]]; then
    echo "只能选择一个卸载范围。" >&2
    exit 2
  fi
  scope="${requested}"
}

for argument in "$@"; do
  case "${argument}" in
    --server) set_scope server ;;
    --agent) set_scope agent ;;
    --all) set_scope all ;;
    --delete-data) delete_data=true ;;
    --yes|-y) assume_yes=true ;;
    --help|-h) usage; exit 0 ;;
    *)
      printf 'Unknown argument: %s\n' "${argument}" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ -z "${scope}" ]]; then
  if [[ "${assume_yes}" == true ]]; then
    scope="all"
  else
    cat <<'EOF'
请选择卸载范围：
  1) Server（API 与 Bot）
  2) Agent
  3) Server 与 Agent（完全卸载）
  0) 取消
EOF
    selection=""
    read -r -p "请选择: " selection || true
    case "${selection}" in
      1) scope="server" ;;
      2) scope="agent" ;;
      3) scope="all" ;;
      0|"") echo "Uninstall cancelled."; exit 0 ;;
      *) echo "无效选择，卸载已取消。 Uninstall cancelled."; exit 0 ;;
    esac
  fi
fi

if [[ "${delete_data}" == true && "${scope}" == "agent" ]]; then
  echo "--delete-data 不能与 --agent 一起使用。" >&2
  exit 2
fi

describe_scope() {
  case "$1" in
    server)
      echo "将删除 API/Bot 服务和 server.env；Agent 保持不变。"
      ;;
    agent)
      echo "将删除 Agent 服务、agent.env 和 Agent 本地接入状态；Server 保持不变。"
      ;;
    all)
      echo "将删除 Server、Agent、全部配置、状态、默认 SQLite 数据和共享虚拟环境。"
      ;;
  esac
  echo "源码仓库不会自动删除。"
}

describe_scope "${scope}"
if [[ "${assume_yes}" != true ]] && ! confirm "继续卸载吗？"; then
  echo "Uninstall cancelled."
  exit 0
fi

require_sudo

remove_units() {
  local -a units=("$@")
  local -a unit_files=()
  local unit
  for unit in "${units[@]}"; do
    unit_files+=("${SYSTEMD_DIR}/${unit}")
  done
  sudo systemctl disable --now "${units[@]}" 2>/dev/null || true
  sudo rm -f -- "${unit_files[@]}"
  sudo systemctl daemon-reload
  sudo systemctl reset-failed "${units[@]}" 2>/dev/null || true
}

server_remains=false
agent_remains=false

case "${scope}" in
  all)
    remove_units proxypulse-api.service proxypulse-bot.service proxypulse-agent.service
    sudo rm -rf -- "${ENV_DIR}" "${STATE_DIR}"
    rm -rf -- "${VENV_DIR}"
    rm -f -- "${ROOT_DIR}/proxypulse.db"
    ;;
  server)
    remove_units proxypulse-api.service proxypulse-bot.service
    sudo rm -f -- "${ENV_DIR}/server.env"
    if [[ "${assume_yes}" != true && "${delete_data}" != true ]]; then
      if confirm "同时删除默认 SQLite 数据 ${ROOT_DIR}/proxypulse.db 吗？"; then
        delete_data=true
      fi
    fi
    if [[ "${delete_data}" == true ]]; then
      rm -f -- "${ROOT_DIR}/proxypulse.db"
    fi
    sudo rmdir -- "${ENV_DIR}" 2>/dev/null || true
    if sudo test -f "${SYSTEMD_DIR}/proxypulse-agent.service" \
      || sudo test -f "${ENV_DIR}/agent.env"; then
      agent_remains=true
    fi
    ;;
  agent)
    remove_units proxypulse-agent.service
    sudo rm -f -- "${ENV_DIR}/agent.env" "${STATE_DIR}/agent-state.json"
    sudo rmdir -- "${STATE_DIR}" "${ENV_DIR}" 2>/dev/null || true
    if sudo test -f "${SYSTEMD_DIR}/proxypulse-api.service" \
      || sudo test -f "${SYSTEMD_DIR}/proxypulse-bot.service" \
      || sudo test -f "${ENV_DIR}/server.env"; then
      server_remains=true
    fi
    ;;
esac

if [[ "${scope}" != "all" && "${server_remains}" != true && "${agent_remains}" != true ]]; then
  rm -rf -- "${VENV_DIR}"
fi

case "${scope}" in
  server) echo "Server 已卸载。" ;;
  agent) echo "Agent 已卸载。" ;;
  all)
    echo "ProxyPulse Server and Agent have been completely uninstalled."
    echo "ProxyPulse Server 与 Agent 已完全卸载。"
    ;;
esac

if [[ "${server_remains}" == true || "${agent_remains}" == true ]]; then
  echo "源码和共享虚拟环境已保留，因为另一个角色仍在使用。"
elif [[ -f "${ROOT_DIR}/proxypulse.db" ]]; then
  echo "默认 SQLite 数据已保留在源码目录；如需保留数据，请勿删除源码目录。"
else
  echo "源码仓库已保留。如需一并删除，请手工运行："
  printf '  sudo rm -rf -- %q\n' "${ROOT_DIR}"
fi
