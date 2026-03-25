#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="${ROOT_DIR}/.venv"
ENV_DIR="/etc/proxypulse"
ENV_FILE="${ENV_DIR}/server.env"
SYSTEMD_DIR="/etc/systemd/system"

render_unit() {
  local template="$1"
  local destination="$2"
  sed \
    -e "s|__WORKDIR__|${ROOT_DIR}|g" \
    -e "s|__ENV_FILE__|${ENV_FILE}|g" \
    -e "s|__PYTHON__|${VENV_DIR}/bin/python|g" \
    "${template}" | sudo tee "${destination}" >/dev/null
}

python3 -m venv "${VENV_DIR}"
"${VENV_DIR}/bin/pip" install --upgrade pip
"${VENV_DIR}/bin/pip" install .

sudo install -d -m 0755 "${ENV_DIR}"
if [[ ! -f "${ENV_FILE}" ]]; then
  sudo install -m 0600 "${ROOT_DIR}/deploy/env/server.env.example" "${ENV_FILE}"
fi

render_unit "${ROOT_DIR}/deploy/systemd/proxypulse-api.service.in" "${SYSTEMD_DIR}/proxypulse-api.service"
render_unit "${ROOT_DIR}/deploy/systemd/proxypulse-bot.service.in" "${SYSTEMD_DIR}/proxypulse-bot.service"

sudo systemctl daemon-reload
sudo systemctl enable proxypulse-api.service proxypulse-bot.service

cat <<EOF
Server installation complete.

Next steps:
1. Edit ${ENV_FILE}
2. sudo systemctl restart proxypulse-api proxypulse-bot
3. sudo systemctl status proxypulse-api proxypulse-bot
EOF

