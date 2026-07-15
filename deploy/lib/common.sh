#!/usr/bin/env bash

if [[ -z "${ROOT_DIR:-}" ]]; then
  ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
fi

VENV_DIR="${PROXYPULSE_VENV_DIR:-${ROOT_DIR}/.venv}"
ENV_DIR="${PROXYPULSE_ENV_DIR:-/etc/proxypulse}"
STATE_DIR="${PROXYPULSE_STATE_DIR:-/var/lib/proxypulse}"
SYSTEMD_DIR="${PROXYPULSE_SYSTEMD_DIR:-/etc/systemd/system}"

confirm() {
  local prompt="$1"
  local answer=""
  read -r -p "${prompt} [y/N] " answer || true
  answer="${answer,,}"
  [[ "${answer}" == "y" || "${answer}" == "yes" ]]
}

require_sudo() {
  if ! command -v sudo >/dev/null 2>&1; then
    echo "错误：未找到 sudo。" >&2
    return 1
  fi
  sudo -v
}

validate_url() {
  [[ "$1" =~ ^https?://[^[:space:]]+$ ]]
}

validate_admin_ids() {
  [[ "$1" =~ ^[0-9]+([,][0-9]+)*$ ]]
}

validate_agent_name() {
  [[ -n "$1" && ${#1} -le 120 && ! "$1" =~ [[:space:]] ]]
}

python_dependencies_ready() {
  command -v python3 >/dev/null 2>&1 \
    && python3 -m venv --help >/dev/null 2>&1 \
    && python3 -m pip --version >/dev/null 2>&1
}

ensure_python_dependencies() {
  if python_dependencies_ready; then
    return 0
  fi

  local distro_id=""
  if [[ -r /etc/os-release ]]; then
    distro_id="$(. /etc/os-release && printf '%s' "${ID:-}")"
  fi
  if [[ "${distro_id}" != "debian" && "${distro_id}" != "ubuntu" ]]; then
    echo "缺少 Python 运行依赖：python3、python3-venv、python3-pip。" >&2
    echo "当前发行版不支持自动安装，请先使用系统包管理器安装。" >&2
    return 1
  fi

  echo "需要安装：python3、python3-venv、python3-pip。"
  if ! confirm "现在安装这些依赖吗？"; then
    echo "已取消依赖安装。"
    return 1
  fi
  require_sudo
  sudo apt-get update
  sudo apt-get install -y python3 python3-venv python3-pip
}

service_installed() {
  sudo systemctl cat "$1" >/dev/null 2>&1
}

print_service_status() {
  local unit="$1"
  local installed="未安装"
  local enabled="未启用"
  local active="未运行"

  if service_installed "${unit}"; then
    installed="已安装"
  fi
  if sudo systemctl is-enabled --quiet "${unit}" 2>/dev/null; then
    enabled="已启用"
  fi
  if sudo systemctl is-active --quiet "${unit}" 2>/dev/null; then
    active="运行中"
  fi

  printf '%-28s %-8s %-8s %s\n' "${unit}" "${installed}" "${enabled}" "${active}"
  if [[ "${installed}" == "已安装" && "${active}" != "运行中" ]]; then
    printf '  日志：sudo journalctl -u %s -n 100 --no-pager\n' "${unit}"
  fi
}

restart_or_explain() {
  local unit="$1"
  if ! sudo systemctl restart "${unit}"; then
    echo "服务 ${unit} 启动失败。" >&2
    echo "请运行：sudo journalctl -u ${unit} -n 100 --no-pager" >&2
    return 1
  fi
}
