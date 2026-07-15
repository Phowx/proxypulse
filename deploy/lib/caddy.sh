#!/usr/bin/env bash

if [[ -z "${ROOT_DIR:-}" ]]; then
  ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
fi

CADDY_CONFIG_DIR="${PROXYPULSE_CADDY_CONFIG_DIR:-/etc/caddy}"
CADDYFILE="${PROXYPULSE_CADDYFILE:-${CADDY_CONFIG_DIR}/Caddyfile}"
CADDY_SITES_DIR="${PROXYPULSE_CADDY_SITES_DIR:-${CADDY_CONFIG_DIR}/sites-enabled}"
CADDY_SITE_FILE="${PROXYPULSE_CADDY_SITE_FILE:-${CADDY_SITES_DIR}/proxypulse.caddy}"
CADDY_IMPORT_MARKER="${PROXYPULSE_CADDY_IMPORT_MARKER:-${ENV_DIR}/caddy-import-managed}"
CADDY_IMPORT_BEGIN="# BEGIN PROXYPULSE MANAGED IMPORT"
CADDY_IMPORT_END="# END PROXYPULSE MANAGED IMPORT"
CADDY_IMPORT_LINE="import sites-enabled/*.caddy"

validate_port() {
  [[ "$1" =~ ^[0-9]+$ ]] && (( 10#$1 >= 1 && 10#$1 <= 65535 ))
}

validate_public_server_url() {
  local value="$1"
  [[ "${value}" =~ ^https?://(\[[0-9A-Fa-f:.%]+\]|[A-Za-z0-9][A-Za-z0-9.-]*)(:[0-9]{1,5})?/?$ ]] || return 1
  validate_port "$(server_url_port "${value}")"
}

server_url_port() {
  local value="$1"
  local scheme="${value%%://*}"
  local authority="${value#*://}"
  authority="${authority%/}"
  local tail=""

  if [[ "${authority}" == \[* ]]; then
    tail="${authority#*]}"
    if [[ "${tail}" == :* ]]; then
      printf '%s' "${tail#:}"
      return 0
    fi
  elif [[ "${authority}" == *:* ]]; then
    printf '%s' "${authority##*:}"
    return 0
  fi

  if [[ "${scheme}" == "https" ]]; then
    printf '443'
  else
    printf '80'
  fi
}

server_url_with_port() {
  local value="$1"
  local port="$2"
  local scheme="${value%%://*}"
  local authority="${value#*://}"
  authority="${authority%/}"
  local host=""

  if [[ "${authority}" == \[* ]]; then
    host="${authority%%]*}]"
  else
    host="${authority%%:*}"
  fi
  printf '%s://%s:%s' "${scheme}" "${host}" "${port}"
}

ensure_caddy() {
  if command -v caddy >/dev/null 2>&1; then
    return 0
  fi

  local distro_id=""
  if [[ -r /etc/os-release ]]; then
    distro_id="$(. /etc/os-release && printf '%s' "${ID:-}")"
  fi
  if [[ "${distro_id}" != "debian" && "${distro_id}" != "ubuntu" && "${distro_id}" != "raspbian" ]]; then
    echo "错误：未找到 Caddy，当前发行版不支持自动安装。" >&2
    echo "请先安装 Caddy，再重新运行 Server 安装。" >&2
    return 1
  fi

  echo "未找到 Caddy，正在安装官方稳定版软件包。"
  sudo apt-get update
  sudo apt-get install -y debian-keyring debian-archive-keyring apt-transport-https curl gpg

  local temp_dir key_file keyring_file source_file
  temp_dir="$(mktemp -d)"
  key_file="${temp_dir}/caddy.gpg.key"
  keyring_file="${temp_dir}/caddy-stable-archive-keyring.gpg"
  source_file="${temp_dir}/caddy-stable.list"
  curl -fsSL -o "${key_file}" https://dl.cloudsmith.io/public/caddy/stable/gpg.key
  gpg --dearmor --batch --yes --output "${keyring_file}" "${key_file}"
  curl -fsSL -o "${source_file}" https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt
  sudo install -m 0644 "${keyring_file}" /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  sudo install -m 0644 "${source_file}" /etc/apt/sources.list.d/caddy-stable.list
  rm -rf -- "${temp_dir}"

  sudo apt-get update
  sudo apt-get install -y caddy
}

remove_managed_caddy_import() {
  if sudo test -f "${CADDYFILE}"; then
    sudo sed -i "/^${CADDY_IMPORT_BEGIN}$/,/^${CADDY_IMPORT_END}$/d" "${CADDYFILE}"
  fi
  sudo rm -f -- "${CADDY_IMPORT_MARKER}"
}

install_caddy_proxy() {
  local server_url="$1"
  local local_port="$2"
  validate_public_server_url "${server_url}" || {
    echo "错误：Caddy 公网 Server URL 无效：${server_url}" >&2
    return 1
  }
  validate_port "${local_port}" || {
    echo "错误：API 本地监听端口无效：${local_port}" >&2
    return 1
  }
  if [[ "$(server_url_port "${server_url}")" == "${local_port}" ]]; then
    echo "错误：API 本地端口不能与 Caddy 公网端口相同。" >&2
    return 1
  fi

  ensure_caddy
  sudo install -d -m 0755 "${CADDY_CONFIG_DIR}" "${CADDY_SITES_DIR}" "${ENV_DIR}"
  if ! sudo test -f "${CADDYFILE}"; then
    printf '# Caddy configuration\n' | sudo tee "${CADDYFILE}" >/dev/null
  fi

  local temp_site backup_site import_added=false site_existed=false
  temp_site="$(mktemp)"
  backup_site="$(mktemp)"
  printf '%s {\n\treverse_proxy 127.0.0.1:%s\n}\n' "${server_url}" "${local_port}" > "${temp_site}"
  sudo caddy validate --config "${temp_site}" --adapter caddyfile >/dev/null

  if sudo test -f "${CADDY_SITE_FILE}"; then
    sudo cp -- "${CADDY_SITE_FILE}" "${backup_site}"
    site_existed=true
  fi
  sudo install -m 0644 "${temp_site}" "${CADDY_SITE_FILE}"

  if ! sudo grep -Fqx "${CADDY_IMPORT_LINE}" "${CADDYFILE}"; then
    printf '\n%s\n%s\n%s\n' \
      "${CADDY_IMPORT_BEGIN}" "${CADDY_IMPORT_LINE}" "${CADDY_IMPORT_END}" \
      | sudo tee -a "${CADDYFILE}" >/dev/null
    sudo touch "${CADDY_IMPORT_MARKER}"
    import_added=true
  fi

  if ! sudo caddy validate --config "${CADDYFILE}" --adapter caddyfile; then
    if [[ "${site_existed}" == true ]]; then
      sudo cp -- "${backup_site}" "${CADDY_SITE_FILE}"
    else
      sudo rm -f -- "${CADDY_SITE_FILE}"
    fi
    if [[ "${import_added}" == true ]]; then
      remove_managed_caddy_import
    fi
    rm -f -- "${temp_site}" "${backup_site}"
    echo "错误：Caddy 配置校验失败，已恢复原配置。" >&2
    return 1
  fi
  rm -f -- "${temp_site}" "${backup_site}"

  sudo systemctl enable --now caddy.service
  sudo systemctl reload caddy.service
}

remove_caddy_proxy() {
  sudo rm -f -- "${CADDY_SITE_FILE}"
  sudo rmdir -- "${CADDY_SITES_DIR}" 2>/dev/null || true
  if sudo test -f "${CADDY_IMPORT_MARKER}"; then
    remove_managed_caddy_import
  fi

  if command -v caddy >/dev/null 2>&1 && sudo test -f "${CADDYFILE}"; then
    if sudo caddy validate --config "${CADDYFILE}" --adapter caddyfile >/dev/null; then
      sudo systemctl reload caddy.service 2>/dev/null || true
    else
      echo "警告：移除 ProxyPulse 配置后 Caddy 校验失败，未重新加载 Caddy。" >&2
    fi
  fi
}
