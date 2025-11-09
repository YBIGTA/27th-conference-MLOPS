#!/bin/bash
set -euo pipefail

if systemctl list-units --full -all | grep -q "collector.service"; then
  systemctl stop collector.service || true
fi
