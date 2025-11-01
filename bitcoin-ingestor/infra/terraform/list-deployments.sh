#!/bin/bash
set -e

# List all deployed symbols and their status

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Colors
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo ""
echo "╔═══════════════════════════════════════════════════════════════════════╗"
echo "║              MARKET DATA COLLECTOR - DEPLOYED SYMBOLS                ║"
echo "╚═══════════════════════════════════════════════════════════════════════╝"
echo ""

# Check if Terraform is initialized
if [ ! -d ".terraform" ]; then
    echo "Terraform not initialized. Run 'terraform init' first."
    exit 1
fi

# Get current workspace
CURRENT=$(terraform workspace show)

echo -e "${BLUE}Current workspace:${NC} $CURRENT"
echo ""

# List all workspaces
echo "═══════════════════════════════════════════════════════════════════════"
echo "DEPLOYED SYMBOLS"
echo "═══════════════════════════════════════════════════════════════════════"
echo ""

# Iterate through workspaces (skip default)
terraform workspace list | grep -v "default" | sed 's/*//g' | while read workspace; do
    workspace=$(echo $workspace | xargs) # trim whitespace

    if [ -z "$workspace" ]; then
        continue
    fi

    # Switch to workspace quietly
    terraform workspace select "$workspace" > /dev/null 2>&1

    # Get symbol (uppercase workspace name)
    SYMBOL=$(echo "$workspace" | tr '[:lower:]' '[:upper:]')

    # Get instance count
    INSTANCE_COUNT=$(terraform output -json collector_instance_ids 2>/dev/null | jq -r 'length' 2>/dev/null || echo "0")

    if [ "$INSTANCE_COUNT" = "0" ]; then
        echo -e "${YELLOW}⚠${NC}  $SYMBOL (workspace: $workspace)"
        echo "     Status: No resources deployed"
    else
        echo -e "${GREEN}✓${NC}  $SYMBOL (workspace: $workspace)"
        echo "     Instances: $INSTANCE_COUNT"

        # Get instance IDs
        terraform output -json collector_instance_ids 2>/dev/null | jq -r '.[]' 2>/dev/null | while read id; do
            echo "       - $id"
        done

        # Get bucket name
        BUCKET=$(terraform output -raw raw_bucket_name 2>/dev/null || echo "unknown")
        echo "     S3 Bucket: $BUCKET"
        echo "     S3 Path: s3://$BUCKET/raw/exchange=binance/stream=trade/symbol=$SYMBOL/"
    fi

    echo ""
done

# Switch back to original workspace
terraform workspace select "$CURRENT" > /dev/null 2>&1

echo "═══════════════════════════════════════════════════════════════════════"
echo ""
echo "Commands:"
echo "  Deploy new:    ./deploy-symbol.sh ETHUSDT apply"
echo "  Check status:  ./deploy-symbol.sh BTCUSDT output"
echo "  Remove:        ./deploy-symbol.sh BTCUSDT destroy"
echo ""
