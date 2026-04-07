#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
PID_FILE="${PROJECT_ROOT}/logs/service_runner.pid"
LOG_FILE="${PROJECT_ROOT}/logs/service_runner.log"
PYTHON_BIN="${PROJECT_ROOT}/.venv/bin/python"
APP_ENTRY="${PROJECT_ROOT}/ops/service_runner.py"
PROCESS_PATTERN="service_runner.py"

pid_matches_service() {
    local pid="$1"
    local cmdline
    local exe_arg
    local entry_arg
    local cwd=""
    local entry_path=""

    [[ -n "${pid}" ]] || return 1
    kill -0 "${pid}" 2>/dev/null || return 1

    cmdline="$(tr '\0' ' ' < "/proc/${pid}/cmdline" 2>/dev/null || true)"
    [[ -n "${cmdline}" ]] || return 1

    read -r -a argv <<< "${cmdline}"
    exe_arg="${argv[0]:-}"
    entry_arg="${argv[1]:-}"

    [[ "${exe_arg}" == */.venv/bin/python ]] || return 1
    [[ "${entry_arg}" == *service_runner.py ]] || return 1

    if [[ "${entry_arg}" = /* ]]; then
        entry_path="$(readlink -f "${entry_arg}" 2>/dev/null || true)"
        [[ "${entry_path}" == "${APP_ENTRY}" ]] || return 1
        return 0
    fi

    cwd="$(readlink -f "/proc/${pid}/cwd" 2>/dev/null || true)"
    entry_path="$(readlink -f "${cwd}/${entry_arg}" 2>/dev/null || true)"
    [[ "${entry_path}" == "${APP_ENTRY}" ]] || return 1
}

find_running_pids() {
    local pids=()

    if [[ -f "${PID_FILE}" ]]; then
        local pid
        pid="$(cat "${PID_FILE}")"
        if [[ -n "${pid}" ]] && kill -0 "${pid}" 2>/dev/null; then
            pids+=("${pid}")
        else
            rm -f "${PID_FILE}"
        fi
    fi

    while IFS= read -r pid; do
        [[ -z "${pid}" ]] && continue
        if ! pid_matches_service "${pid}"; then
            continue
        fi
        if [[ " ${pids[*]} " != *" ${pid} "* ]]; then
            pids+=("${pid}")
        fi
    done < <(pgrep -f "${PROCESS_PATTERN}" || true)

    if [[ "${#pids[@]}" -gt 0 ]]; then
        printf '%s\n' "${pids[@]}"
    fi
}

start_service() {
    if [[ ! -x "${PYTHON_BIN}" ]]; then
        echo "Python virtualenv not found: ${PYTHON_BIN}" >&2
        exit 1
    fi

    if [[ ! -f "${APP_ENTRY}" ]]; then
        echo "Application entry not found: ${APP_ENTRY}" >&2
        exit 1
    fi

    mapfile -t existing_pids < <(find_running_pids)
    if [[ "${#existing_pids[@]}" -gt 0 ]]; then
        echo "Service already running with PID ${existing_pids[0]}"
        return 0
    fi

    mkdir -p "${PROJECT_ROOT}/logs"
    : > "${LOG_FILE}"

    (
        cd "${PROJECT_ROOT}"
        setsid "${PYTHON_BIN}" "${APP_ENTRY}" >>"${LOG_FILE}" 2>&1 < /dev/null &
        echo $! > "${PID_FILE}"
    )

    sleep 1
    if [[ -f "${PID_FILE}" ]]; then
        local pid
        pid="$(cat "${PID_FILE}")"
        if [[ -n "${pid}" ]] && kill -0 "${pid}" 2>/dev/null; then
            echo "Service started with PID ${pid}"
            return 0
        fi
    fi

    echo "Service failed to start. Check ${LOG_FILE}" >&2
    rm -f "${PID_FILE}"
    exit 1
}

start_foreground() {
    if [[ ! -x "${PYTHON_BIN}" ]]; then
        echo "Python virtualenv not found: ${PYTHON_BIN}" >&2
        exit 1
    fi

    cd "${PROJECT_ROOT}"
    exec "${PYTHON_BIN}" "${APP_ENTRY}"
}

stop_service() {
    mapfile -t pids < <(find_running_pids)
    if [[ "${#pids[@]}" -eq 0 ]]; then
        echo "Service is not running"
        return 0
    fi

    kill "${pids[@]}"
    for _ in $(seq 1 30); do
        mapfile -t pids < <(find_running_pids)
        if [[ "${#pids[@]}" -eq 0 ]]; then
            rm -f "${PID_FILE}"
            echo "Service stopped"
            return 0
        fi
        sleep 1
    done

    echo "Service did not stop gracefully, sending SIGKILL"
    mapfile -t pids < <(find_running_pids)
    if [[ "${#pids[@]}" -gt 0 ]]; then
        kill -9 "${pids[@]}"
    fi
    rm -f "${PID_FILE}"
    echo "Service stopped"
}

restart_service() {
    stop_service
    start_service
}

status_service() {
    mapfile -t pids < <(find_running_pids)
    if [[ "${#pids[@]}" -gt 0 ]]; then
        echo "Service is running with PID ${pids[0]}"
        return 0
    fi

    echo "Service is not running"
    return 1
}

case "${1:-}" in
    start)
        start_service
        ;;
    start-foreground)
        start_foreground
        ;;
    stop)
        stop_service
        ;;
    restart)
        restart_service
        ;;
    status)
        status_service
        ;;
    *)
        echo "Usage: $0 {start|start-foreground|stop|restart|status}" >&2
        exit 1
        ;;
esac
