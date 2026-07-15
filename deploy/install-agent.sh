#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "${ROOT_DIR}/deploy/lib/common.sh"
source "${ROOT_DIR}/deploy/lib/env.sh"

ENV_FILE="${ENV_DIR}/agent.env"

render_unit() {
  sed \
    -e "s|__WORKDIR__|${ROOT_DIR}|g" \
    -e "s|__ENV_FILE__|${ENV_FILE}|g" \
    -e "s|__PYTHON__|${VENV_DIR}/bin/python|g" \
    "${ROOT_DIR}/deploy/systemd/proxypulse-agent.service.in" \
    | sudo tee "${SYSTEMD_DIR}/proxypulse-agent.service" >/dev/null
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

sudo install -d -m 0755 "${STATE_DIR}"
if ! sudo test -f "${ENV_FILE}"; then
  install_merged_env "${ROOT_DIR}/deploy/env/agent.env.example" "${ENV_FILE}"
fi

render_unit
sudo systemctl daemon-reload
sudo systemctl enable proxypulse-agent.service
restart_or_explain proxypulse-agent.service

cat <<EOF
Agent 安装或更新完成。
配置：${ENV_FILE}
采集范围：$(read_env_value "${ENV_FILE}" PROXYPULSE_COLLECTIONS)
状态：sudo systemctl status proxypulse-agent
EOF
