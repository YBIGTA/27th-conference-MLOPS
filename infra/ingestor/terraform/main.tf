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

variable "lambda_schedule_expression_cryptopanic" {
  type        = string
  default     = "rate(8 hours)"
  description = "EventBridge schedule expression for CryptoPanic news (default keeps under ~100 req/month)."
}

variable "price_1s_schedule_expression" {
  type        = string
  default     = "rate(5 minutes)"
  description = "EventBridge schedule for 1s OHLCV loader."
}

variable "price_1s_prefix" {
  type        = string
  default     = "Binance/BTCUSDT/"
  description = "S3 prefix containing Parquet ticks."
}

variable "price_1s_start" {
  type        = string
  default     = ""
  description = "ISO start timestamp for the loader. Leave blank to start from (now - overlap)."
}

variable "price_1s_overlap_seconds" {
  type        = number
  default     = 180
  description = "Seconds to rewind from the last ts to avoid gaps."
}

variable "price_1s_postgres_url" {
  type        = string
  default     = ""
  description = "Postgres DSN for price_1s loader (e.g., postgresql://user:pass@host:5432/db)."
}

variable "price_1s_env_secret" {
  type        = string
  default     = ""
  description = "Optional password/secret (mapped to ENV_SECRET/PGPASSWORD)."
}

variable "price_1s_pgsslmode" {
  type        = string
  default     = "require"
  description = "SSL mode for Postgres connection."
}

variable "price_1s_table" {
  type        = string
  default     = "price_15s"
  description = "Target table for price loader."
}

variable "price_1s_supabase_url" {
  type        = string
  default     = ""
  description = "Supabase REST URL for price loader (fallback to SUPABASE_URL env if blank)."
}

variable "price_1s_supabase_service_role_key" {
  type        = string
  default     = ""
  description = "Supabase service role key for REST upsert (fallback to ENV_SECRET)."
}

variable "price_1s_layer_arns" {
  type        = list(string)
  default     = []
  description = "External Lambda layer ARNs providing pandas/pyarrow/requests for the price loader."
}

variable "price_1s_bucket_override" {
  type        = string
  default     = ""
  description = "Optional override for landing bucket (default uses created landing bucket)."
}

variable "news_data_source" {
  type        = string
  default     = "RSS"
  description = "Prefix used by the RSS news Lambda (e.g., RSS)."
}

variable "cryptopanic_api_key" {
  type        = string
  default     = ""
  description = "API key for CryptoPanic (leave blank to use RSS)."
}

variable "news_fetch_content" {
  type        = string
  default     = "false"
  description = "Whether to fetch article HTML content (true/false)."
}

variable "news_max_article_bytes" {
  type        = number
  default     = 524288
  description = "Maximum bytes to download per article when fetching content."
}

variable "news_max_article_chars" {
  type        = number
  default     = 4000
  description = "Maximum characters of extracted text to keep per article."
}

variable "news_content_prefix" {
  type        = string
  default     = "ExtContent"
  description = "S3 prefix for enriched articles."
}

variable "news_data_prefix" {
  type        = string
  default     = "ExtContent/news_data/"
  description = "S3 prefix where raw news JSON is stored."
}

variable "news_min_crawl_date" {
  type        = string
  default     = "2025-12-11T00:00:00Z"
  description = "Minimum crawlDate to ingest (ISO8601, UTC)."
}

variable "news_table" {
  type        = string
  default     = "news"
  description = "Supabase table name for news ingestion."
}

variable "news_supabase_url" {
  type        = string
  default     = ""
  description = "Supabase REST URL for news ingestion."
}

variable "news_supabase_service_role_key" {
  type        = string
  default     = ""
  description = "Supabase service role key for news ingestion."
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
    from_port   = 9443
    to_port     = 9443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
    description = "Binance WebSocket"
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
          "s3:GetObject",
          "s3:ListBucket"
        ]
        Resource = [
          aws_s3_bucket.artifacts.arn,
          "${aws_s3_bucket.artifacts.arn}/*"
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
              dnf install -y python3 python3-pip ruby wget
              cd /tmp
              wget https://aws-codedeploy-${var.aws_region}.s3.${var.aws_region}.amazonaws.com/latest/install
              chmod +x install
              ./install auto
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
# price_1s loader Lambda (BTCUSDT 15s OHLCV)
# ------------------------
resource "aws_iam_role" "price_1s_lambda" {
  name               = "${var.project}-price-1s-role"
  assume_role_policy = data.aws_iam_policy_document.assume_lambda.json
  tags               = local.tags
}

resource "aws_iam_role_policy" "price_1s_lambda" {
  name = "${var.project}-price-1s-policy"
  role = aws_iam_role.price_1s_lambda.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:ListBucket"]
        Resource = [
          aws_s3_bucket.landing.arn,
          "${aws_s3_bucket.landing.arn}/*"
        ]
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

resource "aws_lambda_function" "price_1s_loader" {
  function_name = "${var.project}-price-1s-loader"
  role          = aws_iam_role.price_1s_lambda.arn
  handler       = "main.handler"
  runtime       = "python3.10"
  timeout       = 120
  memory_size   = 512
  filename      = "${path.module}/../lambda/price_1s/price_1s_code.zip"
  source_code_hash = filebase64sha256("${path.module}/../lambda/price_1s/price_1s_code.zip")
  layers        = var.price_1s_layer_arns
  environment {
    variables = {
      PRICE_1S_BUCKET          = var.price_1s_bucket_override != "" ? var.price_1s_bucket_override : aws_s3_bucket.landing.id
      PRICE_1S_PREFIX          = var.price_1s_prefix
      PRICE_1S_START           = var.price_1s_start
      PRICE_1S_OVERLAP_SECONDS = var.price_1s_overlap_seconds
      PRICE_TABLE              = var.price_1s_table
      PRICE_1S_SUPABASE_URL            = var.price_1s_supabase_url
      PRICE_1S_SUPABASE_SERVICE_ROLE_KEY = var.price_1s_supabase_service_role_key
      ENV_SECRET               = var.price_1s_env_secret
    }
  }
  tags = local.tags
}

resource "aws_cloudwatch_event_rule" "price_1s_schedule" {
  name                = "${var.project}-price-1s-schedule"
  schedule_expression = var.price_1s_schedule_expression
  tags                = local.tags
}

resource "aws_cloudwatch_event_target" "price_1s_target" {
  rule      = aws_cloudwatch_event_rule.price_1s_schedule.name
  target_id = "price-1s-loader"
  arn       = aws_lambda_function.price_1s_loader.arn
}

resource "aws_lambda_permission" "allow_events_price_1s" {
  statement_id  = "AllowExecutionFromEventsPrice1s"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.price_1s_loader.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.price_1s_schedule.arn
}

# ------------------------
# News ingestion Lambdas (RSS + CryptoPanic)
# ------------------------

data "archive_file" "news_lambda" {
  type        = "zip"
  source_dir  = "${path.module}/../lambda/news_ingestor"
  output_path = "${path.module}/../lambda/news_ingestor.zip"
}

data "archive_file" "news_content_lambda" {
  type        = "zip"
  source_dir  = "${path.module}/../lambda/news_content_fetcher"
  output_path = "${path.module}/../lambda/news_content_fetcher.zip"
}

data "archive_file" "news_data_ingestor_lambda" {
  type        = "zip"
  source_dir  = "${path.module}/../lambda/news_data_ingestor"
  output_path = "${path.module}/../lambda/news_data_ingestor.zip"
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
        Action   = ["s3:PutObject", "s3:GetObject", "s3:ListBucket"]
        Resource = [
          aws_s3_bucket.landing.arn,
          "${aws_s3_bucket.landing.arn}/*"
        ]
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
  handler       = "main.lambda_handler"
  runtime       = "python3.11"
  filename      = data.archive_file.news_lambda.output_path
  source_code_hash = data.archive_file.news_lambda.output_base64sha256
  environment {
    variables = {
      LANDING_BUCKET_NAME = aws_s3_bucket.landing.id
      NEWS_SOURCE         = var.news_data_source
      NEWS_FETCH_CONTENT  = var.news_fetch_content
      NEWS_MAX_ARTICLE_BYTES = var.news_max_article_bytes
      NEWS_MAX_ARTICLE_CHARS = var.news_max_article_chars
    }
  }
  tags = local.tags
}

resource "aws_lambda_function" "news_ingestor_cryptopanic" {
  function_name = "${var.project}-news-cryptopanic"
  role          = aws_iam_role.news_lambda.arn
  handler       = "main.lambda_handler"
  runtime       = "python3.11"
  filename      = data.archive_file.news_lambda.output_path
  source_code_hash = data.archive_file.news_lambda.output_base64sha256
  environment {
    variables = {
      LANDING_BUCKET_NAME = aws_s3_bucket.landing.id
      NEWS_SOURCE         = "CRYPTOPANIC"
      CRYPTOPANIC_API_KEY = var.cryptopanic_api_key
      NEWS_FETCH_CONTENT  = var.news_fetch_content
      NEWS_MAX_ARTICLE_BYTES = var.news_max_article_bytes
      NEWS_MAX_ARTICLE_CHARS = var.news_max_article_chars
    }
  }
  tags = local.tags
}

resource "aws_lambda_function" "news_content_fetcher" {
  function_name = "${var.project}-news-content"
  role          = aws_iam_role.news_lambda.arn
  handler       = "main.lambda_handler"
  runtime       = "python3.11"
  timeout       = 60
  filename      = data.archive_file.news_content_lambda.output_path
  source_code_hash = data.archive_file.news_content_lambda.output_base64sha256
  environment {
    variables = {
      LANDING_BUCKET_NAME      = aws_s3_bucket.landing.id
      DEST_PREFIX              = var.news_content_prefix
      SOURCE_PREFIX            = "Ext/${var.news_data_source}/"
      NEWS_MAX_ARTICLE_BYTES   = var.news_max_article_bytes
      NEWS_MAX_ARTICLE_CHARS   = var.news_max_article_chars
    }
  }
  tags = local.tags
}

resource "aws_lambda_function" "news_data_ingestor" {
  function_name = "${var.project}-news-data-ingestor"
  role          = aws_iam_role.news_lambda.arn
  handler       = "main.lambda_handler"
  runtime       = "python3.10"
  timeout       = 120
  memory_size   = 512
  filename      = data.archive_file.news_data_ingestor_lambda.output_path
  source_code_hash = data.archive_file.news_data_ingestor_lambda.output_base64sha256
  environment {
    variables = {
      NEWS_DATA_PREFIX                 = var.news_data_prefix
      NEWS_TABLE                       = var.news_table
      NEWS_MIN_CRAWL_DATE              = var.news_min_crawl_date
      NEWS_SUPABASE_URL                = var.news_supabase_url
      NEWS_SUPABASE_SERVICE_ROLE_KEY   = var.news_supabase_service_role_key
      NEWS_MAX_ARTICLE_BYTES           = var.news_max_article_bytes
      NEWS_MAX_ARTICLE_CHARS           = var.news_max_article_chars
    }
  }
  tags = local.tags
}

resource "aws_cloudwatch_event_rule" "news_schedule" {
  name                = "${var.project}-news-schedule-rss"
  schedule_expression = var.lambda_schedule_expression
  tags                = local.tags
}

resource "aws_cloudwatch_event_target" "news_target" {
  rule      = aws_cloudwatch_event_rule.news_schedule.name
  target_id = "news-lambda-rss"
  arn       = aws_lambda_function.news_ingestor.arn
}

resource "aws_cloudwatch_event_rule" "news_schedule_cryptopanic" {
  name                = "${var.project}-news-schedule-cryptopanic"
  schedule_expression = var.lambda_schedule_expression_cryptopanic
  tags                = local.tags
}

resource "aws_cloudwatch_event_target" "news_target_cryptopanic" {
  rule      = aws_cloudwatch_event_rule.news_schedule_cryptopanic.name
  target_id = "news-lambda-cryptopanic"
  arn       = aws_lambda_function.news_ingestor_cryptopanic.arn
}

resource "aws_lambda_permission" "allow_events" {
  statement_id  = "AllowExecutionFromEvents"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.news_ingestor.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.news_schedule.arn
}

resource "aws_lambda_permission" "allow_events_cryptopanic" {
  statement_id  = "AllowExecutionFromEventsCrypto"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.news_ingestor_cryptopanic.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.news_schedule_cryptopanic.arn
}

resource "aws_lambda_permission" "allow_s3_news_content" {
  statement_id  = "AllowExecutionFromS3Content"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.news_content_fetcher.function_name
  principal     = "s3.amazonaws.com"
  source_arn    = aws_s3_bucket.landing.arn
}

resource "aws_lambda_permission" "allow_s3_news_data" {
  statement_id  = "AllowExecutionFromS3NewsData"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.news_data_ingestor.function_name
  principal     = "s3.amazonaws.com"
  source_arn    = aws_s3_bucket.landing.arn
}

resource "aws_s3_bucket_notification" "landing_news_content" {
  bucket = aws_s3_bucket.landing.id

  lambda_function {
    lambda_function_arn = aws_lambda_function.news_content_fetcher.arn
    events              = ["s3:ObjectCreated:*"]
    filter_prefix       = "Ext/${var.news_data_source}/"
    filter_suffix       = ".json"
  }

  lambda_function {
    lambda_function_arn = aws_lambda_function.news_data_ingestor.arn
    events              = ["s3:ObjectCreated:*"]
    filter_prefix       = var.news_data_prefix
    filter_suffix       = ".json"
  }

  depends_on = [aws_lambda_permission.allow_s3_news_content, aws_lambda_permission.allow_s3_news_data]
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
