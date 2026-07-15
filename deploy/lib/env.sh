#!/usr/bin/env bash

read_env_value() {
  local file="$1"
  local wanted_key="$2"
  local line key

  [[ -f "${file}" ]] || sudo test -f "${file}" 2>/dev/null || return 0
  while IFS= read -r line; do
    [[ "${line}" == *=* ]] || continue
    key="${line%%=*}"
    if [[ "${key}" == "${wanted_key}" ]]; then
      printf '%s' "${line#*=}"
      return 0
    fi
  done < <(sudo cat "${file}" 2>/dev/null)
}

install_merged_env() {
  local template="$1"
  local target="$2"
  shift 2

  if (( $# % 2 != 0 )); then
    echo "install_merged_env 需要 KEY VALUE 成对参数。" >&2
    return 2
  fi

  umask 077
  local temp_file
  temp_file="$(mktemp)"
  local -a order=()
  local -A values=()
  local -A seen=()
  local line key value

  while IFS= read -r line || [[ -n "${line}" ]]; do
    [[ "${line}" == *=* ]] || continue
    key="${line%%=*}"
    value="${line#*=}"
    if [[ -z "${seen[${key}]+x}" ]]; then
      order+=("${key}")
      seen["${key}"]=1
    fi
    values["${key}"]="${value}"
  done < "${template}"

  if sudo test -f "${target}" 2>/dev/null; then
    while IFS= read -r line || [[ -n "${line}" ]]; do
      [[ "${line}" == *=* ]] || continue
      key="${line%%=*}"
      value="${line#*=}"
      if [[ -z "${seen[${key}]+x}" ]]; then
        order+=("${key}")
        seen["${key}"]=1
      fi
      values["${key}"]="${value}"
    done < <(sudo cat "${target}")
  fi

  while (( $# > 0 )); do
    key="$1"
    value="$2"
    shift 2
    if [[ "${value}" == *$'\n'* || "${key}" != PROXYPULSE_* ]]; then
      rm -f -- "${temp_file}"
      echo "配置键或值无效。" >&2
      return 2
    fi
    if [[ -z "${seen[${key}]+x}" ]]; then
      order+=("${key}")
      seen["${key}"]=1
    fi
    values["${key}"]="${value}"
  done

  for key in "${order[@]}"; do
    printf '%s=%s\n' "${key}" "${values[${key}]}" >> "${temp_file}"
  done

  sudo install -d -m 0755 "$(dirname "${target}")"
  if sudo test -f "${target}" 2>/dev/null; then
    sudo cp -p -- "${target}" "${target}.bak.$(date +%Y%m%d%H%M%S)"
  fi
  local staged="${target}.tmp.$$"
  sudo install -m 0600 "${temp_file}" "${staged}"
  sudo mv -f -- "${staged}" "${target}"
  rm -f -- "${temp_file}"
}
