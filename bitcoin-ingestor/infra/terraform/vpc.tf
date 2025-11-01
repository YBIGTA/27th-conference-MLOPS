# VPC and networking resources
# Note: This creates a simple VPC setup. For production, you may want to use an existing VPC.

# Get available AZs
data "aws_availability_zones" "available" {
  state = "available"
}

# Create VPC if not provided
resource "aws_vpc" "collector" {
  count = var.create_vpc ? 1 : 0

  cidr_block           = var.vpc_cidr
  enable_dns_hostnames = true
  enable_dns_support   = true

  tags = {
    Name        = "${var.environment}-collector-vpc"
    Environment = var.environment
  }
}

# Internet Gateway
resource "aws_internet_gateway" "collector" {
  count = var.create_vpc ? 1 : 0

  vpc_id = aws_vpc.collector[0].id

  tags = {
    Name        = "${var.environment}-collector-igw"
    Environment = var.environment
  }
}

# Public subnets
resource "aws_subnet" "collector_public" {
  count = var.create_vpc ? var.availability_zone_count : 0

  vpc_id                  = aws_vpc.collector[0].id
  cidr_block              = cidrsubnet(var.vpc_cidr, 8, count.index)
  availability_zone       = data.aws_availability_zones.available.names[count.index]
  map_public_ip_on_launch = true

  tags = {
    Name        = "${var.environment}-collector-public-${count.index + 1}"
    Environment = var.environment
    Type        = "public"
  }
}

# Route table for public subnets
resource "aws_route_table" "collector_public" {
  count = var.create_vpc ? 1 : 0

  vpc_id = aws_vpc.collector[0].id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.collector[0].id
  }

  tags = {
    Name        = "${var.environment}-collector-public-rt"
    Environment = var.environment
  }
}

# Associate route table with subnets
resource "aws_route_table_association" "collector_public" {
  count = var.create_vpc ? var.availability_zone_count : 0

  subnet_id      = aws_subnet.collector_public[count.index].id
  route_table_id = aws_route_table.collector_public[0].id
}

# VPC Endpoint for S3 (cost optimization - no data transfer charges)
resource "aws_vpc_endpoint" "s3" {
  count = var.create_vpc && var.enable_s3_endpoint ? 1 : 0

  vpc_id       = aws_vpc.collector[0].id
  service_name = "com.amazonaws.${var.aws_region}.s3"

  route_table_ids = [aws_route_table.collector_public[0].id]

  tags = {
    Name        = "${var.environment}-s3-endpoint"
    Environment = var.environment
  }
}
