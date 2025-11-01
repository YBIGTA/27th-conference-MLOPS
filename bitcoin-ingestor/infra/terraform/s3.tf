resource "aws_s3_bucket" "raw_data" {
  bucket = var.bucket_name

  tags = {
    Name        = var.bucket_name
    Purpose     = "Raw market data storage"
    Environment = var.environment
  }
}

# Enable versioning
resource "aws_s3_bucket_versioning" "raw_data" {
  bucket = aws_s3_bucket.raw_data.id

  versioning_configuration {
    status = var.enable_versioning ? "Enabled" : "Disabled"
  }
}

# Server-side encryption
resource "aws_s3_bucket_server_side_encryption_configuration" "raw_data" {
  bucket = aws_s3_bucket.raw_data.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# Block public access
resource "aws_s3_bucket_public_access_block" "raw_data" {
  bucket = aws_s3_bucket.raw_data.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Lifecycle policy
resource "aws_s3_bucket_lifecycle_configuration" "raw_data" {
  count  = var.enable_glacier_transition ? 1 : 0
  bucket = aws_s3_bucket.raw_data.id

  rule {
    id     = "archive-old-data"
    status = "Enabled"

    transition {
      days          = var.glacier_transition_days
      storage_class = "GLACIER"
    }

    filter {
      prefix = "raw/"
    }
  }
}

# IAM policy for EC2 instances
resource "aws_iam_role" "collector_role" {
  name = "${var.environment}-market-collector-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "ec2.amazonaws.com"
        }
      }
    ]
  })

  tags = {
    Name = "${var.environment}-market-collector-role"
  }
}

resource "aws_iam_role_policy" "collector_s3_access" {
  name = "${var.environment}-collector-s3-access"
  role = aws_iam_role.collector_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "s3:PutObject",
          "s3:PutObjectAcl"
        ]
        Resource = "${aws_s3_bucket.raw_data.arn}/*"
      },
      {
        Effect = "Allow"
        Action = [
          "s3:ListBucket"
        ]
        Resource = aws_s3_bucket.raw_data.arn
      }
    ]
  })
}

resource "aws_iam_instance_profile" "collector_profile" {
  name = "${var.environment}-market-collector-profile"
  role = aws_iam_role.collector_role.name
}
