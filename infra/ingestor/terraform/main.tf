terraform {
  required_version = ">= 1.5.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.45"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.4"
    }
  }
}

provider "aws" {
  region = var.aws_region
}

variable "aws_region" {
  type        = string
  default     = "ap-northeast-2"
  description = "AWS region for all resources."
}

variable "project" {
  type        = string
  default     = "mlops-ingestor-ybigta"
  description = "Prefix used when naming shared resources."
}

variable "landing_bucket_name" {
  type        = string
  default     = "ybigta-mlops-landing-zone"
  description = "Base name for the landing-zone bucket (uniqueness enforced with account id)."
}

variable "artifact_bucket_name" {
  type        = string
  default     = "ybigta-mlops-artifacts"
  description = "Base name for the CodePipeline artifact bucket."
}

variable "vpc_cidr" {
  type        = string
  default     = "10.42.0.0/16"
}

variable "public_subnet_cidr" {
  type        = string
  default     = "10.42.1.0/24"
}

variable "instance_type_ingestor" {
  type        = string
  default     = "t3.micro"
}

variable "ssh_key_name" {
  type        = string
  description = "Name of the EC2 key pair used for SSH troubleshooting."
}

variable "ingestor_ssh_cidrs" {
  type        = list(string)
  default     = ["0.0.0.0/0"]
}

variable "lambda_schedule_expression" {
  type        = string
  default     = "rate(5 minutes)"
  description = "EventBridge schedule expression driving the news lambda."
}

variable "news_data_source" {
  type        = string
  default     = "TEST"
}

variable "env_parameter_name" {
  type        = string
  default     = "/mlops/collector/env"
  description = "SSM Parameter Store name containing the collector .env payload."
}

variable "tags" {
  type        = map(string)
  default     = {}
}

locals {
  tags           = merge(var.tags, { Project = var.project })
  landing_bucket = "${var.landing_bucket_name}-${data.aws_caller_identity.current.account_id}"
  artifact_bucket = "${var.artifact_bucket_name}-${data.aws_caller_identity.current.account_id}"
}

data "aws_caller_identity" "current" {}

# ------------------------
# Networking
# ------------------------

resource "aws_vpc" "main" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true
  tags                 = merge(local.tags, { Name = "${var.project}-vpc" })
}

resource "aws_internet_gateway" "igw" {
  vpc_id = aws_vpc.main.id
  tags   = merge(local.tags, { Name = "${var.project}-igw" })
}

resource "aws_subnet" "public" {
  vpc_id                  = aws_vpc.main.id
  cidr_block              = var.public_subnet_cidr
  map_public_ip_on_launch = true
  availability_zone       = "${var.aws_region}a"
  tags                    = merge(local.tags, { Name = "${var.project}-public" })
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id
  tags   = merge(local.tags, { Name = "${var.project}-rt-public" })
}

resource "aws_route" "public_internet" {
  route_table_id         = aws_route_table.public.id
  destination_cidr_block = "0.0.0.0/0"
  gateway_id             = aws_internet_gateway.igw.id
}

resource "aws_route_table_association" "public" {
  route_table_id = aws_route_table.public.id
  subnet_id      = aws_subnet.public.id
}

resource "aws_security_group" "ingestor" {
  name        = "${var.project}-ingestor-sg"
  description = "Restricts ingress to SSH and egress to HTTPS/DNS"
  vpc_id      = aws_vpc.main.id

  ingress {
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = var.ingestor_ssh_cidrs
    description = "SSH administration"
  }

  egress {
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
    description = "HTTPS egress"
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

  tags = merge(local.tags, { Name = "${var.project}-ingestor-sg" })
}

# ------------------------
# Landing + artifact buckets
# ------------------------

resource "aws_s3_bucket" "landing" {
  bucket        = local.landing_bucket
  force_destroy = false
  tags          = merge(local.tags, { Name = "${var.project}-landing" })
}

resource "aws_s3_bucket_versioning" "landing" {
  bucket = aws_s3_bucket.landing.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "landing" {
  bucket = aws_s3_bucket.landing.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "aws:kms"
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "landing" {
  bucket = aws_s3_bucket.landing.id
  rule {
    id     = "transition-to-ia"
    status = "Enabled"
    filter {
      prefix = ""
    }
    transition {
      days          = 30
      storage_class = "STANDARD_IA"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "landing" {
  bucket                  = aws_s3_bucket.landing.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket" "artifacts" {
  bucket        = local.artifact_bucket
  force_destroy = false
  tags          = merge(local.tags, { Name = "${var.project}-artifacts" })
}

resource "aws_s3_bucket_versioning" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id
  versioning_configuration { status = "Enabled" }
}

resource "aws_s3_bucket_public_access_block" "artifacts" {
  bucket                  = aws_s3_bucket.artifacts.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# ------------------------
# Queue
# ------------------------


# ------------------------
# IAM for EC2 collector
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

resource "aws_iam_role" "ingestor" {
  name               = "${var.project}-ingestor-role"
  assume_role_policy = data.aws_iam_policy_document.assume_ec2.json
  tags               = local.tags
}

resource "aws_iam_role_policy" "ingestor" {
  name = "${var.project}-ingestor-policy"
  role = aws_iam_role.ingestor.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["s3:PutObject", "s3:AbortMultipartUpload", "s3:ListBucket"]
        Resource = [
          aws_s3_bucket.landing.arn,
          "${aws_s3_bucket.landing.arn}/*"
        ]
      },
      {
        Effect   = "Allow"
        Action   = [
          "logs:CreateLogStream",
          "logs:CreateLogGroup",
          "logs:PutLogEvents"
        ]
        Resource = "*"
      },
      {
        Effect   = "Allow"
        Action   = [
          "ssm:GetParameter"
        ]
        Resource = "arn:aws:ssm:${var.aws_region}:${data.aws_caller_identity.current.account_id}:parameter${var.env_parameter_name}"
      },
      {
        Effect   = "Allow"
        Action   = [
          "codedeploy:*",
          "ec2:Describe*",
          "autoscaling:Describe*"
        ]
        Resource = "*"
      }
    ]
  })
}

resource "aws_iam_instance_profile" "ingestor" {
  name = "${var.project}-ingestor-profile"
  role = aws_iam_role.ingestor.name
}

# ------------------------
# Launch template + ASG
# ------------------------

locals {
  user_data = <<-EOT
              #!/bin/bash
              set -xeuo pipefail
              dnf update -y
              dnf install -y python3 python3-pip ruby wget codedeploy-agent
              systemctl enable --now codedeploy-agent
              mkdir -p /opt/collector
              aws ssm get-parameter --name ${var.env_parameter_name} --with-decryption --region ${var.aws_region} \
                --query 'Parameter.Value' --output text > /opt/collector/.env
              chown ec2-user:ec2-user /opt/collector/.env
              chmod 600 /opt/collector/.env
          EOT
}

data "aws_ssm_parameter" "al2023" {
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"
}

resource "aws_launch_template" "ingestor" {
  name_prefix   = "${var.project}-ingestor-"
  image_id      = data.aws_ssm_parameter.al2023.value
  instance_type = var.instance_type_ingestor
  key_name      = var.ssh_key_name

  iam_instance_profile { name = aws_iam_instance_profile.ingestor.name }

  network_interfaces {
    associate_public_ip_address = true
    subnet_id                   = aws_subnet.public.id
    security_groups             = [aws_security_group.ingestor.id]
  }

  user_data = base64encode(local.user_data)

  tag_specifications {
    resource_type = "instance"
    tags          = merge(local.tags, { Name = "${var.project}-ingestor" })
  }

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_autoscaling_group" "ingestor" {
  name                      = "${var.project}-ingestor-asg"
  desired_capacity          = 1
  max_size                  = 1
  min_size                  = 1
  vpc_zone_identifier       = [aws_subnet.public.id]
  health_check_type         = "EC2"
  health_check_grace_period = 60

  launch_template {
    id      = aws_launch_template.ingestor.id
    version = "$Latest"
  }

  tag {
    key                 = "Name"
    value               = "${var.project}-ingestor"
    propagate_at_launch = true
  }
}

# ------------------------
# Lambda to mock news ingestion
# ------------------------

data "archive_file" "news_lambda" {
  type        = "zip"
  source_dir  = "${path.module}/../lambda/news_ingestor"
  output_path = "${path.module}/../lambda/news_ingestor.zip"
}

data "aws_iam_policy_document" "assume_lambda" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "news_lambda" {
  name               = "${var.project}-news-lambda-role"
  assume_role_policy = data.aws_iam_policy_document.assume_lambda.json
  tags               = local.tags
}

resource "aws_iam_role_policy" "news_lambda" {
  name = "${var.project}-news-lambda-policy"
  role = aws_iam_role.news_lambda.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["s3:PutObject"]
        Resource = "${aws_s3_bucket.landing.arn}/*"
      },
      {
        Effect   = "Allow"
        Action   = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents"
        ]
        Resource = "*"
      }
    ]
  })
}

resource "aws_lambda_function" "news_ingestor" {
  function_name = "${var.project}-news"
  role          = aws_iam_role.news_lambda.arn
  handler       = "main.handler"
  runtime       = "python3.11"
  filename      = data.archive_file.news_lambda.output_path
  source_code_hash = data.archive_file.news_lambda.output_base64sha256
  environment {
    variables = {
      BUCKET_NAME      = aws_s3_bucket.landing.id
      NEWS_DATA_SOURCE = var.news_data_source
    }
  }
  tags = local.tags
}

resource "aws_cloudwatch_event_rule" "news_schedule" {
  name                = "${var.project}-news-schedule"
  schedule_expression = var.lambda_schedule_expression
  tags                = local.tags
}

resource "aws_cloudwatch_event_target" "news_target" {
  rule      = aws_cloudwatch_event_rule.news_schedule.name
  target_id = "news-lambda"
  arn       = aws_lambda_function.news_ingestor.arn
}

resource "aws_lambda_permission" "allow_events" {
  statement_id  = "AllowExecutionFromEvents"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.news_ingestor.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.news_schedule.arn
}

# ------------------------
# CodeDeploy + CodePipeline
# ------------------------

data "aws_iam_policy_document" "assume_codedeploy" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["codedeploy.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "codedeploy" {
  name               = "${var.project}-codedeploy-role"
  assume_role_policy = data.aws_iam_policy_document.assume_codedeploy.json
  tags               = local.tags
}

resource "aws_iam_role_policy" "codedeploy" {
  name = "${var.project}-codedeploy-policy"
  role = aws_iam_role.codedeploy.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = [
          "autoscaling:*",
          "ec2:*",
          "elasticloadbalancing:*",
          "s3:*",
          "cloudwatch:*"
        ]
        Resource = "*"
      }
    ]
  })
}

resource "aws_codedeploy_app" "collector" {
  name             = "${var.project}-app"
  compute_platform = "Server"
  tags             = local.tags
}

resource "aws_codedeploy_deployment_group" "collector" {
  app_name              = aws_codedeploy_app.collector.name
  deployment_group_name = "${var.project}-dg"
  service_role_arn      = aws_iam_role.codedeploy.arn
  autoscaling_groups    = [aws_autoscaling_group.ingestor.name]

  deployment_style {
    deployment_option = "WITHOUT_TRAFFIC_CONTROL"
    deployment_type   = "IN_PLACE"
  }

  auto_rollback_configuration {
    enabled = true
    events  = ["DEPLOYMENT_FAILURE"]
  }

  tags = local.tags
}


# ------------------------
# Outputs
# ------------------------

output "s3_bucket_name" {
  value = aws_s3_bucket.landing.id
}

output "artifact_bucket_name" {
  value = aws_s3_bucket.artifacts.id
}
