## Gap-Fill Backend (EC2 + Terraform)

Single EC2(t3.medium) that runs the FastAPI/SSE gap-fill service. Creates a minimal VPC, public subnet, security group (SSH + backend port + optional 443), IAM role for SSM + optional S3 artifact, and a systemd service that launches `uvicorn main:app`.

### Files
- `main.tf` – SG + IAM + EC2 with user-data bootstrap (installs Docker). Uses an existing VPC/subnet (set in tfvars).
- `terraform.tfvars.example` – fill in `existing_vpc_id`, `existing_subnet_id`, `ssh_key_name`, CIDRs, etc., then copy to `terraform.tfvars`.
- (Optional) Put any helper scripts/docker-compose in `backend/` and zip for S3 artifact or scp after provision. The container image itself is pulled manually by you.

### Required inputs
- `ssh_key_name` – existing EC2 key pair.
- `ssh_ingress_cidrs` – lock SSH to your IP.
- `backend_http_cidrs` – who can reach the API/SSE port (`backend_port`).

### Optional inputs
- `backend_artifact_bucket` / `backend_artifact_key` – S3 zip that contains `main.py` and `requirements.txt` for the backend. If provided, user-data pulls it into `/opt/backend/`.
- `env_parameter_name` – SSM parameter path containing `.env` (defaults to `/mlops/backend/env`).

### Apply
```bash
cd infra/backend
cp terraform.tfvars.example terraform.tfvars
# edit terraform.tfvars to set key pair, CIDRs, and optional artifact info
terraform init
terraform apply
```

### User-data bootstrap (summary)
- Install python3/pip/git/awscli, create `backend` user, pull optional SSM `.env`, optional S3 artifact zip to `/opt/backend`.
- Create venv at `/opt/backend/venv`, `pip install -r requirements.txt` if present (fallback to `fastapi uvicorn httpx`).
- systemd unit `backend.service` runs `uvicorn main:app --host 0.0.0.0 --port <backend_port>`.

### After provision
- SSH (`ssh -i <key.pem> ec2-user@<public_ip>`) and use Docker directly: e.g., `docker pull <image>` then `docker run ... -p 8000:8000`.
- `/opt/backend/.env` is populated from SSM (if present) for you to mount into the container.
- If you provided an artifact, it is extracted to `/opt/backend` (ownership: backend user).
