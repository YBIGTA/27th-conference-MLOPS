terraform {
  required_version = ">= 1.5.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.45"
    }
  }
}

provider "aws" {
  region = var.aws_region
}

data "aws_caller_identity" "current" {}

variable "project" {
  type        = string
  default     = "mlops-gap-backend"
  description = "Name prefix for backend resources."
}

variable "aws_region" {
  type        = string
  default     = "ap-northeast-2"
}

variable "existing_vpc_id" {
  type        = string
  default     = ""
  description = "If set, reuse this VPC instead of creating a new one."
}

variable "existing_subnet_id" {
  type        = string
  default     = ""
  description = "If set, reuse this subnet for the backend instance."
}

variable "ssh_key_name" {
  type        = string
  description = "Existing EC2 key pair for SSH."
}

variable "ssh_ingress_cidrs" {
  type        = list(string)
  default     = ["0.0.0.0/0"]
  description = "CIDRs allowed to SSH (restrict to your IP)."
}

variable "backend_http_cidrs" {
  type        = list(string)
  default     = ["0.0.0.0/0"]
  description = "CIDRs allowed to reach the backend port (SSE/API)."
}

variable "backend_port" {
  type        = number
  default     = 8000
  description = "Port exposed by the FastAPI backend (uvicorn)."
}

variable "instance_type_backend" {
  type        = string
  default     = "t3.medium"
  description = "EC2 instance type for the backend."
}

variable "env_parameter_name" {
  type        = string
  default     = "/mlops/backend/env"
  description = "SSM Parameter Store path containing the backend .env payload."
}

variable "backend_artifact_bucket" {
  type        = string
  default     = ""
  description = "Optional S3 bucket containing a zip of the backend app."
}

variable "backend_artifact_key" {
  type        = string
  default     = ""
  description = "S3 key for the backend artifact zip (required if bucket set)."
}

variable "landing_bucket_name" {
  type        = string
  default     = "ybigta-mlops-landing-zone-324037321745"
  description = "Landing bucket for train/model data."
}

variable "sagemaker_training_role_arn" {
  type        = string
  default     = ""
  description = "IAM role ARN used by SageMaker training jobs (for iam:PassRole)."
}

variable "tags" {
  type    = map(string)
  default = {}
}

locals {
  tags                 = merge(var.tags, { Project = var.project })
  artifact_bucket_arn    = var.backend_artifact_bucket != "" ? "arn:aws:s3:::${var.backend_artifact_bucket}" : ""
  artifact_objects_arn   = var.backend_artifact_bucket != "" ? "arn:aws:s3:::${var.backend_artifact_bucket}/*" : ""
  ssm_param_arn          = "arn:aws:ssm:${var.aws_region}:${data.aws_caller_identity.current.account_id}:parameter${var.env_parameter_name}"
  landing_bucket_arn     = "arn:aws:s3:::${var.landing_bucket_name}"
  landing_bucket_objects = "arn:aws:s3:::${var.landing_bucket_name}/*"
}

# ------------------------
# Networking
# ------------------------

data "aws_vpc" "selected" {
  count = var.existing_vpc_id != "" ? 1 : 0
  id    = var.existing_vpc_id
}

data "aws_subnet" "selected" {
  count = var.existing_subnet_id != "" ? 1 : 0
  id    = var.existing_subnet_id
}

resource "aws_security_group" "backend" {
  name        = "${var.project}-sg"
  description = "Ingress for backend SSE/API + SSH; egress to HTTPS/DNS."
  vpc_id      = coalesce(try(data.aws_vpc.selected[0].id, null))

  ingress {
    description = "SSH"
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = var.ssh_ingress_cidrs
  }

  ingress {
    description = "Backend port"
    from_port   = var.backend_port
    to_port     = var.backend_port
    protocol    = "tcp"
    cidr_blocks = var.backend_http_cidrs
  }

  ingress {
    description = "ML inference API (ml-api, default 9000)"
    from_port   = 9000
    to_port     = 9000
    protocol    = "tcp"
    cidr_blocks = var.backend_http_cidrs
  }

  ingress {
    description = "HTTPS (optional TLS terminator)"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = var.backend_http_cidrs
  }

  egress {
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
    description = "HTTPS egress"
  }

  egress {
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
    description = "HTTP egress (Binance REST)"
  }

  egress {
    from_port   = 53
    to_port     = 53
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
    description = "DNS TCP"
  }

  egress {
    from_port   = 53
    to_port     = 53
    protocol    = "udp"
    cidr_blocks = ["0.0.0.0/0"]
    description = "DNS UDP"
  }

  tags = merge(local.tags, { Name = "${var.project}-sg" })
}

# ------------------------
# IAM
# ------------------------

data "aws_iam_policy_document" "assume_ec2" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "backend" {
  name               = "${var.project}-role"
  assume_role_policy = data.aws_iam_policy_document.assume_ec2.json
  tags               = local.tags
}

data "aws_iam_policy_document" "backend" {
  statement {
    effect = "Allow"
    actions = [
      "logs:CreateLogGroup",
      "logs:CreateLogStream",
      "logs:PutLogEvents",
      "logs:DescribeLogStreams",
      "logs:GetLogEvents",
    ]
    resources = ["*"]
  }

  statement {
    effect    = "Allow"
    actions   = ["ssm:GetParameter"]
    resources = [local.ssm_param_arn]
  }

  dynamic "statement" {
    for_each = var.backend_artifact_bucket != "" ? [1] : []
    content {
      effect = "Allow"
      actions = [
        "s3:GetObject",
        "s3:ListBucket",
      ]
      resources = [
        local.artifact_bucket_arn,
        local.artifact_objects_arn,
      ]
    }
  }

  # ML 파이프라인: Landing bucket 접근
  statement {
    effect = "Allow"
    actions = [
      "s3:GetObject",
      "s3:PutObject",
      "s3:ListBucket",
    ]
    resources = [
      local.landing_bucket_arn,
      local.landing_bucket_objects,
    ]
  }

  # ML 파이프라인: SageMaker 트레이닝 잡 호출 + PassRole
  dynamic "statement" {
    for_each = var.sagemaker_training_role_arn != "" ? [1] : []
    content {
      effect = "Allow"
      actions = [
        "iam:PassRole",
      ]
      resources = [var.sagemaker_training_role_arn]
      condition {
        test     = "StringEquals"
        variable = "iam:PassedToService"
        values   = ["sagemaker.amazonaws.com"]
      }
    }
  }

  statement {
    effect = "Allow"
    actions = [
      "sagemaker:CreateTrainingJob",
      "sagemaker:DescribeTrainingJob",
      "sagemaker:StopTrainingJob",
      "sagemaker:ListTrainingJobs",
    ]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "backend" {
  name   = "${var.project}-policy"
  role   = aws_iam_role.backend.id
  policy = data.aws_iam_policy_document.backend.json
}

resource "aws_iam_instance_profile" "backend" {
  name = "${var.project}-profile"
  role = aws_iam_role.backend.name
}

# ------------------------
# Compute
# ------------------------

data "aws_ssm_parameter" "al2023" {
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"
}

locals {
  user_data = <<-EOT
              #!/bin/bash
              set -xeuo pipefail
              dnf update -y
              dnf install -y docker docker-compose-plugin git unzip awscli
              systemctl enable --now docker
              usermod -aG docker ec2-user || true
              useradd -m backend || true
              usermod -aG docker backend || true

              install -d -o backend -g backend /opt/backend

              # optional: pull .env from SSM for container env use
              aws ssm get-parameter --name ${var.env_parameter_name} --with-decryption --region ${var.aws_region} --query 'Parameter.Value' --output text > /opt/backend/.env || true
              chown backend:backend /opt/backend/.env || true
              chmod 600 /opt/backend/.env || true

              # optional: download artifact (e.g., docker-compose or scripts) from S3
              if [ -n "${var.backend_artifact_bucket}" ] && [ -n "${var.backend_artifact_key}" ]; then
                aws s3 cp s3://${var.backend_artifact_bucket}/${var.backend_artifact_key} /tmp/backend.zip || true
                unzip -o /tmp/backend.zip -d /opt/backend || true
                chown -R backend:backend /opt/backend
              fi

              # Docker is installed and running; you can SSH and docker pull/run your image.
          EOT
}

resource "aws_instance" "backend" {
  ami                         = data.aws_ssm_parameter.al2023.value
  instance_type               = var.instance_type_backend
  subnet_id                   = coalesce(try(data.aws_subnet.selected[0].id, null))
  vpc_security_group_ids      = [aws_security_group.backend.id]
  key_name                    = var.ssh_key_name
  associate_public_ip_address = true
  iam_instance_profile        = aws_iam_instance_profile.backend.name

  user_data = base64encode(local.user_data)

  tags = merge(local.tags, { Name = "${var.project}-ec2" })
}

# ------------------------
# Outputs
# ------------------------

output "backend_public_ip" {
  value       = aws_instance.backend.public_ip
  description = "Public IP of the backend instance."
}

output "backend_public_dns" {
  value       = aws_instance.backend.public_dns
  description = "Public DNS of the backend instance."
}

output "backend_url" {
  value       = "http://${aws_instance.backend.public_dns}:${var.backend_port}"
  description = "HTTP endpoint for the backend (adjust if behind TLS/ALB)."
}
