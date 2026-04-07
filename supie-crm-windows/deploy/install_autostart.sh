#!/usr/bin/env bash

set -euo pipefail

SERVICE_NAME="supie-crm.service"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
SERVICE_SOURCE="${SCRIPT_DIR}/${SERVICE_NAME}"
SERVICE_TARGET="/etc/systemd/system/${SERVICE_NAME}"
CONTROL_SCRIPT="${PROJECT_ROOT}/scripts/service_control.sh"

if [[ "$(id -u)" -ne 0 ]]; then
    echo "Please run this installer as root." >&2
    exit 1
fi

if [[ ! -f "${SERVICE_SOURCE}" ]]; then
    echo "Missing service file: ${SERVICE_SOURCE}" >&2
    exit 1
fi

if [[ ! -x "${CONTROL_SCRIPT}" ]]; then
    chmod +x "${CONTROL_SCRIPT}"
fi

install -m 644 "${SERVICE_SOURCE}" "${SERVICE_TARGET}"
systemctl daemon-reload
systemctl enable "${SERVICE_NAME}"
systemctl restart "${SERVICE_NAME}"
systemctl status "${SERVICE_NAME}" --no-pager
