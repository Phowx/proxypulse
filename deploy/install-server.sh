#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "${ROOT_DIR}/deploy/lib/common.sh"
source "${ROOT_DIR}/deploy/lib/env.sh"

ENV_FILE="${ENV_DIR}/server.env"

render_unit() {
  local template="$1"
  local destination="$2"
  sed \
    -e "s|__WORKDIR__|${ROOT_DIR}|g" \
    -e "s|__ENV_FILE__|${ENV_FILE}|g" \
    -e "s|__PYTHON__|${VENV_DIR}/bin/python|g" \
    "${template}" | sudo tee "${destination}" >/dev/null
}

if ! python_dependencies_ready; then
  echo "缺少 python3、python3-venv 或 python3-pip。" >&2
  echo "请先运行 bash deploy/manage.sh，由交互向导安装依赖。" >&2
  exit 1
fi
require_sudo

if [[ ! -x "${VENV_DIR}/bin/python" ]]; then
  python3 -m venv "${VENV_DIR}"
fi
"${VENV_DIR}/bin/pip" install --upgrade pip
"${VENV_DIR}/bin/pip" install "${ROOT_DIR}"

if ! sudo test -f "${ENV_FILE}"; then
  install_merged_env "${ROOT_DIR}/deploy/env/server.env.example" "${ENV_FILE}"
fi

render_unit "${ROOT_DIR}/deploy/systemd/proxypulse-api.service.in" "${SYSTEMD_DIR}/proxypulse-api.service"
render_unit "${ROOT_DIR}/deploy/systemd/proxypulse-bot.service.in" "${SYSTEMD_DIR}/proxypulse-bot.service"

sudo systemctl daemon-reload
sudo systemctl enable proxypulse-api.service proxypulse-bot.service
restart_or_explain proxypulse-api.service
restart_or_explain proxypulse-bot.service

cat <<EOF
Server 安装或更新完成。
配置：${ENV_FILE}
状态：sudo systemctl status proxypulse-api proxypulse-bot
EOF
