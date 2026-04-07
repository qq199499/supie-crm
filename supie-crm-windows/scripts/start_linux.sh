#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
PYTHON_BIN="${PROJECT_ROOT}/.venv/bin/python"
APP_ENTRY="${PROJECT_ROOT}/ops/service_runner.py"

if [[ ! -x "${PYTHON_BIN}" ]]; then
    echo "Virtual environment not found: ${PYTHON_BIN}" >&2
    exit 1
fi

if [[ ! -f "${APP_ENTRY}" ]]; then
    echo "Application entry not found: ${APP_ENTRY}" >&2
    exit 1
fi

cd "${PROJECT_ROOT}"
exec "${PYTHON_BIN}" "${APP_ENTRY}"
