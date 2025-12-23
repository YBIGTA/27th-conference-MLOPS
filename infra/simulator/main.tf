terraform {
  required_version = ">= 1.5.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.0"
    }
  }
}

variable "region" {
  description = "AWS region"
  type        = string
}

variable "vpc_id" {
  description = "VPC ID for the instance"
  type        = string
}

variable "subnet_id" {
  description = "Subnet ID for the instance"
  type        = string
}

variable "key_name" {
  description = "Optional SSH key name"
  type        = string
  default     = null
}

variable "allowed_ssh_cidrs" {
  description = "List of CIDRs allowed for SSH"
  type        = list(string)
  default     = ["0.0.0.0/0"]
}

variable "supabase_url" {
  description = "Supabase URL"
  type        = string
  sensitive   = true
  default     = ""
}

variable "supabase_service_role_key" {
  description = "Supabase service role key"
  type        = string
  sensitive   = true
  default     = ""
}

variable "openai_api_key" {
  description = "OpenAI API key"
  type        = string
  sensitive   = true
  default     = ""
}

variable "instance_profile" {
  description = "Optional IAM instance profile name"
  type        = string
  default     = null
}

provider "aws" {
  region = var.region
}

data "aws_ami" "amazon_linux" {
  most_recent = true
  owners      = ["amazon"]

  filter {
    name   = "name"
    values = ["al2023-ami-*-x86_64"] # Amazon Linux 2023
  }

  filter {
    name   = "architecture"
    values = ["x86_64"]
  }
}

resource "aws_security_group" "simulator" {
  name        = "simulator-sg"
  description = "SG for simulator t3.small"
  vpc_id      = var.vpc_id

  ingress {
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = var.allowed_ssh_cidrs
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "simulator-sg"
  }
}

locals {
  user_data = <<-EOT
    #!/bin/bash
    set -euo pipefail

    dnf update -y || yum update -y
    dnf install -y docker docker-compose-plugin cronie || yum install -y docker docker-compose-plugin cronie
    systemctl enable docker
    systemctl start docker
    systemctl enable crond
    systemctl start crond

    SUPABASE_URL="${var.supabase_url}"
    SUPABASE_SERVICE_ROLE_KEY="${var.supabase_service_role_key}"
    OPENAI_API_KEY="${var.openai_api_key}"

    mkdir -p /opt/simulator

    cat >/opt/simulator/run.sh <<'EOF'
    #!/bin/bash
    set -eo pipefail
    APP_DIR="/opt/simulator/app"
    if [ ! -d "$APP_DIR" ]; then
      echo "App directory $APP_DIR not present; upload code via scp/rsync."
      exit 0
    fi

    cd "$APP_DIR"
    if [ ! -f "simulation/docker-compose.yml" ]; then
      echo "docker-compose.yml not found under $APP_DIR/simulation; skipping."
      exit 0
    fi

    docker compose -f simulation/docker-compose.yml up -d --build
    EOF
    chmod +x /opt/simulator/run.sh

    cat >/etc/cron.d/simulator <<'EOF'
    */10 * * * * root /opt/simulator/run.sh >> /var/log/simulator.log 2>&1
    EOF

    systemctl restart crond
  EOT
}

resource "aws_instance" "simulator" {
  ami                         = data.aws_ami.amazon_linux.id
  instance_type               = "t3.small"
  subnet_id                   = var.subnet_id
  vpc_security_group_ids      = [aws_security_group.simulator.id]
  key_name                    = var.key_name
  iam_instance_profile        = var.instance_profile
  user_data                   = local.user_data
  associate_public_ip_address = true

  tags = {
    Name = "simulator"
  }
}
