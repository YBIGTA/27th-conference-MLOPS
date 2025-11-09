#!/bin/bash
set -euo pipefail

systemctl enable collector.service
systemctl restart collector.service
