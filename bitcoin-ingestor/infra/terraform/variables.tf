variable "aws_region" {
  description = "AWS region for resources"
  type        = string
  default     = "us-east-1"
}

variable "environment" {
  description = "Environment name (e.g., dev, prod)"
  type        = string
  default     = "dev"
}

variable "bucket_name" {
  description = "Name for the S3 raw data bucket"
  type        = string
}

variable "enable_versioning" {
  description = "Enable S3 bucket versioning"
  type        = bool
  default     = true
}

variable "enable_glacier_transition" {
  description = "Enable transition to Glacier storage class"
  type        = bool
  default     = false
}

variable "glacier_transition_days" {
  description = "Number of days before transitioning to Glacier"
  type        = number
  default     = 90
}

# EC2 Configuration
variable "instance_count" {
  description = "Number of collector instances to launch"
  type        = number
  default     = 3
}

variable "instance_type" {
  description = "EC2 instance type"
  type        = string
  default     = "t3.micro"
}

variable "symbol" {
  description = "Trading symbol to collect (e.g., BTCUSDT)"
  type        = string
  default     = "BTCUSDT"
}

variable "local_dir" {
  description = "Local directory for temporary data files"
  type        = string
  default     = "/tmp/market-data"
}

variable "rot_bytes" {
  description = "File rotation size threshold in bytes"
  type        = number
  default     = 2097152 # 2 MB
}

variable "rot_secs" {
  description = "File rotation time threshold in seconds"
  type        = number
  default     = 5
}

variable "log_level" {
  description = "Application log level"
  type        = string
  default     = "INFO"
}

variable "github_repo_url" {
  description = "GitHub repository URL for application code (leave empty to skip)"
  type        = string
  default     = ""
}

variable "github_repo_branch" {
  description = "GitHub repository branch"
  type        = string
  default     = "main"
}

# Networking Configuration
variable "create_vpc" {
  description = "Create a new VPC (set to false to use existing VPC)"
  type        = bool
  default     = true
}

variable "vpc_id" {
  description = "VPC ID (required if create_vpc is false)"
  type        = string
  default     = ""
}

variable "vpc_cidr" {
  description = "CIDR block for VPC (if creating new VPC)"
  type        = string
  default     = "10.0.0.0/16"
}

variable "subnet_ids" {
  description = "List of subnet IDs (required if create_vpc is false)"
  type        = list(string)
  default     = []
}

variable "availability_zone_count" {
  description = "Number of availability zones to use"
  type        = number
  default     = 3
}

variable "associate_public_ip" {
  description = "Associate public IP address with instances"
  type        = bool
  default     = true
}

variable "enable_s3_endpoint" {
  description = "Create VPC endpoint for S3 (reduces data transfer costs)"
  type        = bool
  default     = true
}

# Security Configuration
variable "enable_ssh_access" {
  description = "Enable SSH access to instances"
  type        = bool
  default     = false
}

variable "ssh_cidr_blocks" {
  description = "CIDR blocks allowed for SSH access"
  type        = list(string)
  default     = []
}

# Monitoring Configuration
variable "enable_cloudwatch_logs" {
  description = "Enable CloudWatch Logs"
  type        = bool
  default     = false
}

variable "log_retention_days" {
  description = "CloudWatch log retention in days"
  type        = number
  default     = 7
}
