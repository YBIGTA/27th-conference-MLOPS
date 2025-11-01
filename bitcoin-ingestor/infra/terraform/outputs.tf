output "raw_bucket_name" {
  description = "Name of the S3 raw data bucket"
  value       = aws_s3_bucket.raw_data.id
}

output "raw_bucket_arn" {
  description = "ARN of the S3 raw data bucket"
  value       = aws_s3_bucket.raw_data.arn
}

output "collector_role_arn" {
  description = "ARN of the IAM role for collector instances"
  value       = aws_iam_role.collector_role.arn
}

output "collector_instance_profile_name" {
  description = "Name of the IAM instance profile for collector instances"
  value       = aws_iam_instance_profile.collector_profile.name
}

output "collector_instance_profile_arn" {
  description = "ARN of the IAM instance profile for collector instances"
  value       = aws_iam_instance_profile.collector_profile.arn
}

output "collector_instance_ids" {
  description = "IDs of the collector EC2 instances"
  value       = aws_instance.collector[*].id
}

output "collector_instance_public_ips" {
  description = "Public IP addresses of collector instances (if public IPs are enabled)"
  value       = aws_instance.collector[*].public_ip
}

output "collector_instance_private_ips" {
  description = "Private IP addresses of collector instances"
  value       = aws_instance.collector[*].private_ip
}

output "security_group_id" {
  description = "ID of the security group for collector instances"
  value       = aws_security_group.collector.id
}

output "vpc_id" {
  description = "ID of the VPC"
  value       = var.create_vpc ? aws_vpc.collector[0].id : var.vpc_id
}

output "subnet_ids" {
  description = "IDs of the subnets"
  value       = var.create_vpc ? aws_subnet.collector_public[*].id : var.subnet_ids
}

output "cloudwatch_log_group" {
  description = "Name of the CloudWatch log group (if enabled)"
  value       = var.enable_cloudwatch_logs ? aws_cloudwatch_log_group.collector[0].name : null
}

output "connection_commands" {
  description = "SSH commands to connect to instances (if SSH is enabled)"
  value = var.enable_ssh_access && var.associate_public_ip ? [
    for idx, instance in aws_instance.collector :
    "ssh -i <your-key.pem> ec2-user@${instance.public_ip}  # Instance ${idx + 1}"
  ] : ["SSH access is disabled or no public IPs"]
}

output "service_status_commands" {
  description = "Commands to check service status on instances"
  value = [
    "sudo systemctl status market-collector",
    "sudo journalctl -u market-collector -f",
    "sudo journalctl -u market-collector --since '10 minutes ago'"
  ]
}
