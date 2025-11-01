# Main Terraform configuration for Market Data Ingestion
# This file serves as the entry point for the infrastructure

# The actual resources are defined in:
# - s3.tf: S3 bucket and IAM resources
# - providers.tf: Provider configuration
# - variables.tf: Input variables
# - outputs.tf: Output values

# Example usage:
# terraform init
# terraform plan -var="bucket_name=my-market-data-raw"
# terraform apply -var="bucket_name=my-market-data-raw"
