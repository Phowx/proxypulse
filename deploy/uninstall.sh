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
