#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "${ROOT_DIR}/deploy/lib/common.sh"
source "${ROOT_DIR}/deploy/lib/env.sh"
source "${ROOT_DIR}/deploy/lib/caddy.sh"

SERVER_ENV="${ENV_DIR}/server.env"
AGENT_ENV="${ENV_DIR}/agent.env"
STANDARD_COLLECTIONS="identity,cpu,memory,disk,network,uptime"
PRIVACY_COLLECTIONS="cpu,memory,disk,network,uptime"
TRAFFIC_COLLECTIONS="network,uptime"

prompt_plain() {
  local label="$1"
  local default_value="${2:-}"
  local value=""
  if [[ -n "${default_value}" ]]; then
    read -r -p "${label} [${default_value}]: " value || true
    printf '%s' "${value:-${default_value}}"
  else
    read -r -p "${label}: " value || true
    printf '%s' "${value}"
  fi
}

prompt_secret() {
  local label="$1"
  local current_value="${2:-}"
  local value=""
  if [[ -n "${current_value}" ]]; then
    read -r -s -p "${label}（已配置，留空保持不变）: " value || true
  else
    read -r -s -p "${label}: " value || true
  fi
  printf '\n' >&2
  printf '%s' "${value:-${current_value}}"
}

prompt_required() {
  local label="$1"
  local default_value="${2:-}"
  local value=""
  while [[ -z "${value}" ]]; do
    value="$(prompt_plain "${label}" "${default_value}")"
    [[ -n "${value}" ]] || echo "此项不能为空。" >&2
  done
  printf '%s' "${value}"
}

prompt_required_secret() {
  local label="$1"
  local current_value="${2:-}"
  local value=""
  while [[ -z "${value}" ]]; do
    value="$(prompt_secret "${label}" "${current_value}")"
    [[ -n "${value}" ]] || echo "此项不能为空。" >&2
  done
  printf '%s' "${value}"
}

prompt_url() {
  local label="$1"
  local default_value="$2"
  local value=""
  while true; do
    value="$(prompt_required "${label}" "${default_value}")"
    if validate_url "${value}"; then
      printf '%s' "${value}"
      return 0
    fi
    echo "请输入以 http:// 或 https:// 开头的有效 URL。" >&2
  done
}

prompt_port() {
  local label="$1"
  local default_value="${2:-}"
  local value=""
  while true; do
    value="$(prompt_required "${label}" "${default_value}")"
    if validate_port "${value}"; then
      printf '%s' "${value}"
      return 0
    fi
    echo "请输入 1-65535 范围内的端口。" >&2
  done
}

prompt_public_server_url() {
  local default_value="${1:-}"
  local value=""
  while true; do
    value="$(prompt_required "公网 Server URL（如 https://monitor.example.com）" "${default_value}")"
    if validate_public_server_url "${value}"; then
      printf '%s' "${value%/}"
      return 0
    fi
    echo "请输入只包含协议、域名或 IP 及可选端口的 URL；不支持路径和查询参数。" >&2
  done
}

select_collections() {
  local current="${1:-${STANDARD_COLLECTIONS}}"
  local choice=""
  local default_choice="4"
  case "${current}" in
    "${STANDARD_COLLECTIONS}") default_choice="1" ;;
    "${PRIVACY_COLLECTIONS}") default_choice="2" ;;
    "${TRAFFIC_COLLECTIONS}") default_choice="3" ;;
  esac

  while true; do
    echo "" >&2
    echo "采集范围：" >&2
    echo "  1) 标准：主机身份、CPU、内存、根磁盘、网络、运行时长" >&2
    echo "  2) 隐私：不采集主机名、系统和本地 IP" >&2
    echo "  3) 流量：仅采集网络累计流量和运行时长" >&2
    echo "  4) 自定义：从 1-6 中逐项选择" >&2
    read -r -p "请选择 [${default_choice}]: " choice || true
    choice="${choice:-${default_choice}}"
    case "${choice}" in
      1) printf '%s' "${STANDARD_COLLECTIONS}"; return 0 ;;
      2) printf '%s' "${PRIVACY_COLLECTIONS}"; return 0 ;;
      3) printf '%s' "${TRAFFIC_COLLECTIONS}"; return 0 ;;
      4) select_custom_collections; return 0 ;;
      *) echo "无效选择，请输入 1-4。" >&2 ;;
    esac
  done
}

select_custom_collections() {
  local raw=""
  local item=""
  local -A selected=()
  local -a names=(identity cpu memory disk network uptime)
  while true; do
    echo "1 主机身份；2 CPU；3 内存；4 根磁盘；5 网络；6 运行时长" >&2
    read -r -p "输入编号，逗号分隔（例如 2,3,5）: " raw || true
    selected=()
    local valid=true
    IFS=',' read -ra items <<< "${raw}"
    for item in "${items[@]}"; do
      item="${item//[[:space:]]/}"
      if [[ "${item}" =~ ^[1-6]$ ]]; then
        selected["${item}"]=1
      elif [[ -n "${item}" ]]; then
        valid=false
      fi
    done
    if [[ "${valid}" != true || ${#selected[@]} -eq 0 ]]; then
      echo "请至少选择一个 1-6 范围内的编号。" >&2
      continue
    fi
    if [[ -n "${selected[5]+x}" && -z "${selected[6]+x}" ]]; then
      selected[6]=1
      echo "网络流量依赖运行时长判断重启，已自动启用第 6 项。" >&2
    fi
    local result=""
    local index
    for index in 1 2 3 4 5 6; do
      if [[ -n "${selected[${index}]+x}" ]]; then
        result+="${result:+,}${names[index-1]}"
      fi
    done
    printf '%s' "${result}"
    return 0
  done
}

select_network_settings() {
  local current_strategy="${1:-auto}"
  local current_interface="${2:-}"
  local choice=""
  local default_choice="1"
  case "${current_strategy}" in
    fixed) default_choice="2" ;;
    aggregate) default_choice="3" ;;
  esac
  while true; do
    echo "网络接口策略：1 自动识别主接口；2 指定接口；3 汇总有效接口" >&2
    read -r -p "请选择 [${default_choice}]: " choice || true
    choice="${choice:-${default_choice}}"
    case "${choice}" in
      1) printf 'auto|'; return 0 ;;
      2)
        local interface
        interface="$(prompt_required "网络接口名" "${current_interface}")"
        printf 'fixed|%s' "${interface}"
        return 0
        ;;
      3) printf 'aggregate|'; return 0 ;;
      *) echo "无效选择，请输入 1-3。" >&2 ;;
    esac
  done
}

configure_server() {
  echo "将安装或更新 API、Telegram Bot 与 Caddy 反向代理，并写入 ${SERVER_ENV}。"
  echo "API 仅监听 127.0.0.1；Agent 通过 Caddy 公网端口访问。"
  echo "现有数据库和未修改的高级配置会保留。"
  ensure_python_dependencies
  require_sudo

  local bot_token admin_ids server_url local_port public_port current_url current_local_port
  bot_token="$(prompt_required_secret "Telegram Bot Token" "$(read_env_value "${SERVER_ENV}" PROXYPULSE_BOT_TOKEN)")"
  while true; do
    admin_ids="$(prompt_required "管理员 Telegram ID（多个用逗号分隔）" "$(read_env_value "${SERVER_ENV}" PROXYPULSE_ADMIN_TELEGRAM_IDS)")"
    validate_admin_ids "${admin_ids}" && break
    echo "管理员 ID 必须是纯数字，多个 ID 使用英文逗号分隔。" >&2
  done
  current_local_port="$(read_env_value "${SERVER_ENV}" PROXYPULSE_API_PORT || true)"
  local_port="$(prompt_port "API 本地监听端口" "${current_local_port:-8080}")"
  current_url="$(read_env_value "${SERVER_ENV}" PROXYPULSE_SERVER_URL || true)"
  server_url="$(prompt_public_server_url "${current_url}")"
  while true; do
    public_port="$(prompt_port "Caddy 公网监听端口" "$(server_url_port "${server_url}")")"
    if [[ "${public_port}" != "${local_port}" ]]; then
      break
    fi
    echo "Caddy 公网端口不能与 API 本地端口相同。" >&2
  done
  server_url="$(server_url_with_port "${server_url}" "${public_port}")"

  install_merged_env "${ROOT_DIR}/deploy/env/server.env.example" "${SERVER_ENV}" \
    PROXYPULSE_BOT_TOKEN "${bot_token}" \
    PROXYPULSE_ADMIN_TELEGRAM_IDS "${admin_ids}" \
    PROXYPULSE_API_HOST "127.0.0.1" \
    PROXYPULSE_API_PORT "${local_port}" \
    PROXYPULSE_SERVER_URL "${server_url}"
  bash "${ROOT_DIR}/deploy/install-server.sh"
}

configure_agent() {
  echo "将安装或更新 Agent，写入连接信息并选择采集范围。"
  echo "接入令牌需要先通过 Server 的 Telegram Bot 创建。"
  ensure_python_dependencies
  require_sudo

  local server_url agent_name enrollment_token collections network_result strategy interface
  server_url="$(prompt_url "Server URL" "$(read_env_value "${AGENT_ENV}" PROXYPULSE_SERVER_URL || true)")"
  while true; do
    agent_name="$(prompt_required "Agent 名称" "$(read_env_value "${AGENT_ENV}" PROXYPULSE_AGENT_NAME || true)")"
    validate_agent_name "${agent_name}" && break
    echo "Agent 名称不能为空、不能包含空格，且最多 120 个字符。" >&2
  done
  enrollment_token="$(prompt_required_secret "Agent 接入令牌" "$(read_env_value "${AGENT_ENV}" PROXYPULSE_AGENT_ENROLLMENT_TOKEN || true)")"
  collections="$(select_collections "$(read_env_value "${AGENT_ENV}" PROXYPULSE_COLLECTIONS || true)")"
  strategy="auto"
  interface=""
  if [[ ",${collections}," == *,network,* ]]; then
    network_result="$(select_network_settings \
      "$(read_env_value "${AGENT_ENV}" PROXYPULSE_NETWORK_INTERFACE_STRATEGY || true)" \
      "$(read_env_value "${AGENT_ENV}" PROXYPULSE_NETWORK_INTERFACE || true)")"
    strategy="${network_result%%|*}"
    interface="${network_result#*|}"
  fi

  install_merged_env "${ROOT_DIR}/deploy/env/agent.env.example" "${AGENT_ENV}" \
    PROXYPULSE_SERVER_URL "${server_url}" \
    PROXYPULSE_AGENT_NAME "${agent_name}" \
    PROXYPULSE_AGENT_ENROLLMENT_TOKEN "${enrollment_token}" \
    PROXYPULSE_COLLECTIONS "${collections}" \
    PROXYPULSE_NETWORK_INTERFACE_STRATEGY "${strategy}" \
    PROXYPULSE_NETWORK_INTERFACE "${interface}"
  bash "${ROOT_DIR}/deploy/install-agent.sh"
}

reconfigure_agent_collections() {
  echo "只修改 Agent 采集范围；Server 配置和 Agent 接入状态保持不变。"
  require_sudo
  if ! sudo test -f "${AGENT_ENV}"; then
    echo "尚未安装 Agent，请先选择“安装或更新 Agent”。"
    return 0
  fi
  local collections network_result strategy interface
  collections="$(select_collections "$(read_env_value "${AGENT_ENV}" PROXYPULSE_COLLECTIONS)")"
  strategy="auto"
  interface=""
  if [[ ",${collections}," == *,network,* ]]; then
    network_result="$(select_network_settings \
      "$(read_env_value "${AGENT_ENV}" PROXYPULSE_NETWORK_INTERFACE_STRATEGY)" \
      "$(read_env_value "${AGENT_ENV}" PROXYPULSE_NETWORK_INTERFACE)")"
    strategy="${network_result%%|*}"
    interface="${network_result#*|}"
  fi
  install_merged_env "${ROOT_DIR}/deploy/env/agent.env.example" "${AGENT_ENV}" \
    PROXYPULSE_COLLECTIONS "${collections}" \
    PROXYPULSE_NETWORK_INTERFACE_STRATEGY "${strategy}" \
    PROXYPULSE_NETWORK_INTERFACE "${interface}"
  if service_installed proxypulse-agent.service; then
    restart_or_explain proxypulse-agent.service
  fi
  echo "Agent 采集范围已更新：${collections}"
}

show_status() {
  echo "分别显示 systemd 单元的安装、开机启动和运行状态。"
  require_sudo
  print_service_status proxypulse-api.service
  print_service_status proxypulse-bot.service
  print_service_status proxypulse-agent.service
  print_service_status caddy.service
}

show_menu() {
  cat <<'EOF'

ProxyPulse 一键管理
  1) 安装或更新 Server
  2) 安装或更新 Agent
  3) 重新配置 Agent 采集范围
  4) 查看服务状态
  5) 卸载 Server
  6) 卸载 Agent
  7) 完全卸载 Server 与 Agent
  0) 退出
EOF
}

main() {
  local choice=""
  while true; do
    show_menu
    read -r -p "请选择: " choice || return 0
    case "${choice}" in
      1) configure_server ;;
      2) configure_agent ;;
      3) reconfigure_agent_collections ;;
      4) show_status ;;
      5) bash "${ROOT_DIR}/deploy/uninstall.sh" --server ;;
      6) bash "${ROOT_DIR}/deploy/uninstall.sh" --agent ;;
      7) bash "${ROOT_DIR}/deploy/uninstall.sh" --all ;;
      0) echo "已退出。"; return 0 ;;
      *) echo "无效选择，请输入 0-7。" ;;
    esac
  done
}

main "$@"
