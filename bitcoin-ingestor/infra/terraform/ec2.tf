# EC2 instances for market data collection

# Get latest Amazon Linux 2023 AMI
data "aws_ami" "amazon_linux_2023" {
  most_recent = true
  owners      = ["amazon"]

  filter {
    name   = "name"
    values = ["al2023-ami-*-x86_64"]
  }

  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
}

# Security group for collector instances
resource "aws_security_group" "collector" {
  name        = "${var.environment}-collector-sg"
  description = "Security group for market data collector instances"
  vpc_id      = var.create_vpc ? aws_vpc.collector[0].id : var.vpc_id

  # Outbound - Allow all (needed for Binance WebSocket and S3 access)
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
    description = "Allow all outbound traffic"
  }

  # Inbound - SSH (optional, for debugging)
  dynamic "ingress" {
    for_each = var.enable_ssh_access ? [1] : []
    content {
      from_port   = 22
      to_port     = 22
      protocol    = "tcp"
      cidr_blocks = var.ssh_cidr_blocks
      description = "SSH access"
    }
  }

  tags = {
    Name = "${var.environment}-collector-sg"
  }
}

# User data template for instance initialization
locals {
  user_data = templatefile("${path.module}/user_data.sh", {
    raw_bucket  = aws_s3_bucket.raw_data.id
    symbol      = var.symbol
    local_dir   = var.local_dir
    rot_bytes   = var.rot_bytes
    rot_secs    = var.rot_secs
    aws_region  = var.aws_region
    log_level   = var.log_level
    repo_url    = var.github_repo_url
    repo_branch = var.github_repo_branch
  })
}

# Launch 3 EC2 instances
resource "aws_instance" "collector" {
  count = var.instance_count

  ami           = data.aws_ami.amazon_linux_2023.id
  instance_type = var.instance_type

  # IAM instance profile for S3 access
  iam_instance_profile = aws_iam_instance_profile.collector_profile.name

  # Networking
  subnet_id = var.create_vpc ? (
    aws_subnet.collector_public[count.index % var.availability_zone_count].id
    ) : (
    var.subnet_ids[count.index % length(var.subnet_ids)]
  )
  vpc_security_group_ids      = [aws_security_group.collector.id]
  associate_public_ip_address = var.associate_public_ip

  # User data for automatic setup
  user_data                   = local.user_data
  user_data_replace_on_change = true

  # Storage
  root_block_device {
    volume_type = "gp3"
    volume_size = 30
    encrypted   = true
  }

  # Metadata options (IMDSv2 required for security)
  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 1
  }

  # Tags
  tags = {
    Name        = "${var.environment}-collector-${count.index + 1}"
    Environment = var.environment
    Purpose     = "market-data-collection"
    InstanceNum = count.index + 1
  }

  # Lifecycle
  lifecycle {
    create_before_destroy = true
  }
}

# CloudWatch Log Group for application logs (optional)
resource "aws_cloudwatch_log_group" "collector" {
  count = var.enable_cloudwatch_logs ? 1 : 0

  name              = "/aws/ec2/${var.environment}-collector"
  retention_in_days = var.log_retention_days

  tags = {
    Environment = var.environment
  }
}
