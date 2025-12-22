#!/usr/bin/env bash
set -euo pipefail

PEM="$HOME/Desktop/Github Projects/ybigta-bitcoin-dev.pem"
EC2_USER=ec2-user
EC2_HOST=3.35.170.33
SRC_DIR="$HOME/Desktop/Github Projects/27th-conference-MLOPS/backend"

# 1) backend 폴더를 tar로 묶어 EC2로 복사
tar czf /tmp/backend.tar.gz -C "$SRC_DIR" .
scp -i "$PEM" /tmp/backend.tar.gz "${EC2_USER}@${EC2_HOST}:/home/${EC2_USER}/"

# 2) EC2 접속 후 배치/빌드/실행
ssh -i "$PEM" "${EC2_USER}@${EC2_HOST}" <<'EOF'
set -euo pipefail
sudo mkdir -p /opt/backend
sudo tar xzf /home/ec2-user/backend.tar.gz -C /opt/backend
sudo chown -R backend:backend /opt/backend

cd /opt/backend
sudo docker build -t gap-backend:latest .
sudo docker rm -f gap-backend >/dev/null 2>&1 || true
sudo docker run -d --name gap-backend \
  --env-file /opt/backend/.env \
  -p 8000:8000 \
  gap-backend:latest
EOF

echo "완료: http://${EC2_HOST}:8000/healthz 로 헬스 체크해보세요."
