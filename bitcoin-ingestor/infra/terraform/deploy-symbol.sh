#!/bin/bash
set -e

# Workspace-aware deployment script for different symbols
# This prevents accidentally destroying existing instances when adding new symbols

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Function to print colored output
print_info() { echo -e "${BLUE}ℹ ${NC}$1"; }
print_success() { echo -e "${GREEN}✓${NC} $1"; }
print_warning() { echo -e "${YELLOW}⚠${NC} $1"; }
print_error() { echo -e "${RED}✗${NC} $1"; }

# Function to show usage
usage() {
    cat << EOF
Usage: $0 <symbol> [action]

Deploy market data collector for a specific trading symbol using Terraform workspaces.
Each symbol runs in its own isolated workspace with separate EC2 instances.

Arguments:
  symbol    Trading symbol (e.g., BTCUSDT, ETHUSDT, SOLUSDT)
  action    Terraform action: plan, apply, destroy, output (default: plan)

Examples:
  $0 BTCUSDT plan      # Preview changes for BTCUSDT
  $0 BTCUSDT apply     # Deploy 3 instances for BTCUSDT
  $0 ETHUSDT apply     # Deploy 3 instances for ETHUSDT (separate from BTCUSDT)
  $0 BTCUSDT output    # Show outputs for BTCUSDT workspace
  $0 ETHUSDT destroy   # Remove only ETHUSDT instances

Available symbols:
  BTCUSDT, ETHUSDT, BNBUSDT, SOLUSDT, ADAUSDT, XRPUSDT, DOGEUSDT, etc.

Notes:
  - Each symbol uses a separate Terraform workspace
  - Workspaces are isolated - changes to one won't affect others
  - All instances for a symbol write to the same S3 bucket
  - Bucket name is set in terraform.tfvars

EOF
    exit 1
}

# Check arguments
if [ $# -lt 1 ]; then
    usage
fi

SYMBOL=$1
ACTION=${2:-plan}

# Validate symbol format (uppercase letters/numbers)
if [[ ! $SYMBOL =~ ^[A-Z0-9]+$ ]]; then
    print_error "Invalid symbol format: $SYMBOL"
    echo "Symbol must be uppercase letters/numbers (e.g., BTCUSDT, ETHUSDT)"
    exit 1
fi

# Validate action
if [[ ! "$ACTION" =~ ^(plan|apply|destroy|output|show)$ ]]; then
    print_error "Invalid action: $ACTION"
    echo "Valid actions: plan, apply, destroy, output, show"
    exit 1
fi

# Check if Terraform is initialized
if [ ! -d ".terraform" ]; then
    print_warning "Terraform not initialized. Running terraform init..."
    terraform init
fi

# Workspace name (lowercase for Terraform)
WORKSPACE=$(echo "$SYMBOL" | tr '[:upper:]' '[:lower:]')

print_info "=========================================="
print_info "Symbol: $SYMBOL"
print_info "Workspace: $WORKSPACE"
print_info "Action: $ACTION"
print_info "=========================================="
echo ""

# Create workspace if it doesn't exist
if ! terraform workspace list | grep -q "$WORKSPACE"; then
    print_info "Creating new workspace: $WORKSPACE"
    terraform workspace new "$WORKSPACE"
    print_success "Workspace created: $WORKSPACE"
else
    print_info "Switching to workspace: $WORKSPACE"
    terraform workspace select "$WORKSPACE"
    print_success "Workspace selected: $WORKSPACE"
fi

echo ""

# Check if terraform.tfvars exists
if [ ! -f "terraform.tfvars" ]; then
    print_warning "terraform.tfvars not found. Creating from example..."
    if [ -f "terraform.tfvars.example" ]; then
        cp terraform.tfvars.example terraform.tfvars
        print_warning "Please edit terraform.tfvars and set your bucket_name!"
        echo ""
        read -p "Press Enter to continue after editing terraform.tfvars..."
    else
        print_error "terraform.tfvars.example not found!"
        exit 1
    fi
fi

# Execute Terraform action
case $ACTION in
    plan)
        print_info "Running Terraform plan for $SYMBOL..."
        terraform plan -var="symbol=$SYMBOL"
        ;;

    apply)
        print_warning "This will deploy 3 EC2 instances for $SYMBOL"
        print_info "Estimated cost: ~\$22.50/month for EC2 instances"
        echo ""

        # Show current workspaces
        print_info "Currently deployed symbols:"
        terraform workspace list
        echo ""

        read -p "Continue with deployment? (yes/no): " confirm
        if [ "$confirm" != "yes" ]; then
            print_info "Deployment cancelled"
            exit 0
        fi

        print_info "Deploying $SYMBOL..."
        terraform apply -var="symbol=$SYMBOL" -auto-approve

        echo ""
        print_success "=========================================="
        print_success "Deployment complete for $SYMBOL!"
        print_success "=========================================="
        echo ""

        # Show instance information
        print_info "Instance IDs:"
        terraform output -json collector_instance_ids | jq -r '.[]' | while read id; do
            echo "  - $id"
        done

        echo ""
        print_info "Verifying data collection in ~2 minutes:"
        BUCKET=$(terraform output -raw raw_bucket_name)
        echo "  aws s3 ls s3://$BUCKET/raw/exchange=binance/stream=trade/symbol=$SYMBOL/ --recursive | tail -20"
        ;;

    destroy)
        print_warning "This will DESTROY all resources for $SYMBOL"
        print_warning "This includes:"
        echo "  - 3 EC2 instances"
        echo "  - Security group (if not used by other symbols)"
        echo "  - VPC resources (if not used by other symbols)"
        echo ""
        print_info "S3 bucket and data will NOT be deleted"
        echo ""

        read -p "Are you sure you want to destroy $SYMBOL infrastructure? (yes/no): " confirm
        if [ "$confirm" != "yes" ]; then
            print_info "Destroy cancelled"
            exit 0
        fi

        print_info "Destroying $SYMBOL infrastructure..."
        terraform destroy -var="symbol=$SYMBOL" -auto-approve

        print_success "Infrastructure destroyed for $SYMBOL"

        # Ask if user wants to delete workspace
        echo ""
        read -p "Delete workspace '$WORKSPACE'? (yes/no): " delete_ws
        if [ "$delete_ws" = "yes" ]; then
            terraform workspace select default
            terraform workspace delete "$WORKSPACE"
            print_success "Workspace deleted: $WORKSPACE"
        fi
        ;;

    output)
        print_info "Outputs for $SYMBOL:"
        terraform output
        ;;

    show)
        print_info "Current state for $SYMBOL:"
        terraform show
        ;;
esac

echo ""
print_info "=========================================="
print_info "Active workspaces (deployed symbols):"
terraform workspace list
print_info "=========================================="
