#!/usr/bin/env bash

check_python_install_resources() {
  [[ -r /proc/meminfo ]] || return 0
  local available_kb swap_free_kb usable_kb
  available_kb="$(awk '/^MemAvailable:/ {print $2}' /proc/meminfo)"
  swap_free_kb="$(awk '/^SwapFree:/ {print $2}' /proc/meminfo)"
  available_kb="${available_kb:-0}"
  swap_free_kb="${swap_free_kb:-0}"
  usable_kb=$((available_kb + swap_free_kb))

  if (( usable_kb < 262144 )); then
    echo "错误：当前可用内存加空闲 swap 不足 256 MiB，已停止 Python 安装以避免系统失联。" >&2
    echo "请先释放内存或配置 swap；可用 free -h 查看当前状态。" >&2
    return 1
  fi
  if (( usable_kb < 524288 )); then
    echo "警告：当前可用内存加空闲 swap 低于 512 MiB，安装速度可能较慢。" >&2
  fi
}

run_pip_step() {
  local python="$1"
  shift
  local timeout_seconds="${PROXYPULSE_PIP_TIMEOUT_SECONDS:-600}"
  local status=0
  local -a command=(
    env
    PIP_DISABLE_PIP_VERSION_CHECK=1
    PIP_DEFAULT_TIMEOUT=30
    PIP_RETRIES=2
  )
  if command -v timeout >/dev/null 2>&1; then
    command+=(timeout --foreground "${timeout_seconds}s")
  fi
  command+=("${python}" -m pip "$@")
  "${command[@]}" || status=$?
  if (( status == 124 )); then
    echo "错误：pip 步骤超过 ${timeout_seconds} 秒，已终止；请检查服务器到 PyPI 的网络。" >&2
  fi
  return "${status}"
}

install_python_project() {
  check_python_install_resources
  local python="${VENV_DIR}/bin/python"
  echo "[1/3] 检查 Python 构建工具..."
  run_pip_step "${python}" install --no-cache-dir --prefer-binary "setuptools>=69"
  echo "[2/3] 安装 ProxyPulse 及缺失的运行依赖..."
  run_pip_step "${python}" install --no-cache-dir --prefer-binary \
    --no-build-isolation --editable "${ROOT_DIR}"
  echo "[3/3] 校验 Python 依赖..."
  "${python}" -m pip check
}
