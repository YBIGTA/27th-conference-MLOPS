#!/bin/bash
set -euo pipefail

APP_ROOT="/opt/collector"
SRC_DIR="${APP_ROOT}/src"
VENV_DIR="${APP_ROOT}/venv"
SERVICE_DEST="/etc/systemd/system/collector.service"

mkdir -p "${APP_ROOT}"
python3 -m venv "${VENV_DIR}"
"${VENV_DIR}/bin/pip" install --upgrade pip
"${VENV_DIR}/bin/pip" install -r "${SRC_DIR}/requirements.txt"

cp "${SRC_DIR}/collector.service" "${SERVICE_DEST}"
chown root:root "${SERVICE_DEST}"
chmod 644 "${SERVICE_DEST}"

if [[ ! -f "${APP_ROOT}/.env" ]]; then
  echo "WARNING: ${APP_ROOT}/.env not found. Collector will fail until environment file is provided." >&2
fi

systemctl daemon-reload
