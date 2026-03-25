#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="${ROOT_DIR}/.venv"
ENV_DIR="/etc/proxypulse"
ENV_FILE="${ENV_DIR}/agent.env"
STATE_DIR="/var/lib/proxypulse"
SYSTEMD_DIR="/etc/systemd/system"

sed \
  -e "s|__WORKDIR__|${ROOT_DIR}|g" \
  -e "s|__ENV_FILE__|${ENV_FILE}|g" \
  -e "s|__PYTHON__|${VENV_DIR}/bin/python|g" \
  "${ROOT_DIR}/deploy/systemd/proxypulse-agent.service.in" | sudo tee "${SYSTEMD_DIR}/proxypulse-agent.service" >/dev/null

python3 -m venv "${VENV_DIR}"
"${VENV_DIR}/bin/pip" install --upgrade pip
"${VENV_DIR}/bin/pip" install .

sudo install -d -m 0755 "${ENV_DIR}"
sudo install -d -m 0755 "${STATE_DIR}"
if [[ ! -f "${ENV_FILE}" ]]; then
  sudo install -m 0600 "${ROOT_DIR}/deploy/env/agent.env.example" "${ENV_FILE}"
fi

sudo systemctl daemon-reload
sudo systemctl enable proxypulse-agent.service

cat <<EOF
Agent installation complete.

Next steps:
1. Edit ${ENV_FILE}
2. sudo systemctl restart proxypulse-agent
3. sudo systemctl status proxypulse-agent
EOF
