# Multi-Symbol Deployment Guide

This guide explains how to deploy the market data collector for **multiple trading symbols** (e.g., BTCUSDT, ETHUSDT, SOLUSDT) using Terraform workspaces, ensuring isolated deployments that don't interfere with each other.

## Overview

The project uses **Terraform workspaces** to manage multiple symbols independently:

- Each symbol = separate workspace
- Each workspace = separate Terraform state
- Each workspace = separate EC2 instances
- All symbols = share same S3 bucket (different paths)

**Key Benefit:** Adding ETHUSDT won't affect your running BTCUSDT instances!

## Architecture

```
S3 Bucket (shared)
├── raw/exchange=binance/stream=trade/
│   ├── symbol=BTCUSDT/      ← 3 instances
│   │   └── dt=2025-01-15/...
│   ├── symbol=ETHUSDT/      ← 3 instances
│   │   └── dt=2025-01-15/...
│   └── symbol=SOLUSDT/      ← 3 instances
│       └── dt=2025-01-15/...

Terraform Workspaces (isolated)
├── btcusdt (3 EC2 instances)
├── ethusdt (3 EC2 instances)
└── solusdt (3 EC2 instances)
```

## Quick Start

### 1. Deploy Your First Symbol (BTCUSDT)

```bash
cd infra/terraform

# First time setup
cp terraform.tfvars.example terraform.tfvars
nano terraform.tfvars  # Set bucket_name

# Initialize
terraform init

# Deploy BTCUSDT
./deploy-symbol.sh BTCUSDT apply
```

**Result:** 3 EC2 instances collecting BTCUSDT data

### 2. Add Another Symbol (ETHUSDT)

```bash
# No need to change terraform.tfvars!
./deploy-symbol.sh ETHUSDT apply
```

**Result:**
- 3 NEW EC2 instances for ETHUSDT
- Original BTCUSDT instances **still running**
- Both write to same S3 bucket (different paths)

### 3. Add More Symbols

```bash
./deploy-symbol.sh SOLUSDT apply
./deploy-symbol.sh BNBUSDT apply
./deploy-symbol.sh ADAUSDT apply
```

Each deployment is **completely isolated**!

## Using the Deployment Script

### Command Format

```bash
./deploy-symbol.sh <SYMBOL> <action>
```

### Actions

| Action | Description | Example |
|--------|-------------|---------|
| `plan` | Preview changes (safe, no modifications) | `./deploy-symbol.sh ETHUSDT plan` |
| `apply` | Deploy infrastructure | `./deploy-symbol.sh ETHUSDT apply` |
| `destroy` | Remove infrastructure | `./deploy-symbol.sh BTCUSDT destroy` |
| `output` | Show deployment info | `./deploy-symbol.sh BTCUSDT output` |
| `show` | Show full state | `./deploy-symbol.sh BTCUSDT show` |

### Examples

```bash
# Preview what will be created for ETHUSDT
./deploy-symbol.sh ETHUSDT plan

# Deploy ETHUSDT (creates 3 instances)
./deploy-symbol.sh ETHUSDT apply

# Check deployment status
./deploy-symbol.sh ETHUSDT output

# Remove ETHUSDT (keeps BTCUSDT running)
./deploy-symbol.sh ETHUSDT destroy

# List all deployed symbols
./list-deployments.sh
```

## Step-by-Step: Adding a New Symbol

Let's walk through adding ETHUSDT when you already have BTCUSDT running.

### Step 1: Preview Changes

```bash
./deploy-symbol.sh ETHUSDT plan
```

**Output:**
```
ℹ ==========================================
ℹ Symbol: ETHUSDT
ℹ Workspace: ethusdt
ℹ Action: plan
ℹ ==========================================

✓ Workspace created: ethusdt

Plan: 7 to add, 0 to change, 0 to destroy
```

**Key Point:** `0 to change, 0 to destroy` - existing resources safe!

### Step 2: Deploy

```bash
./deploy-symbol.sh ETHUSDT apply
```

**Confirmation Prompt:**
```
⚠ This will deploy 3 EC2 instances for ETHUSDT
ℹ Estimated cost: ~$22.50/month for EC2 instances

Currently deployed symbols:
  default
* btcusdt
  ethusdt (new)

Continue with deployment? (yes/no):
```

Type `yes` to proceed.

### Step 3: Verify

```bash
# Check ETHUSDT deployment
./deploy-symbol.sh ETHUSDT output

# Verify data collection (wait 2-3 minutes)
aws s3 ls s3://your-bucket/raw/exchange=binance/stream=trade/symbol=ETHUSDT/ --recursive | tail -20

# Confirm BTCUSDT still running
./deploy-symbol.sh BTCUSDT output
```

## Managing Multiple Symbols

### List All Deployments

```bash
./list-deployments.sh
```

**Output:**
```
╔═══════════════════════════════════════════════════════════════════════╗
║              MARKET DATA COLLECTOR - DEPLOYED SYMBOLS                ║
╚═══════════════════════════════════════════════════════════════════════╝

Current workspace: btcusdt

═══════════════════════════════════════════════════════════════════════
DEPLOYED SYMBOLS
═══════════════════════════════════════════════════════════════════════

✓  BTCUSDT (workspace: btcusdt)
     Instances: 3
       - i-0abc12345
       - i-0def67890
       - i-0ghi11111
     S3 Bucket: my-market-data-bucket
     S3 Path: s3://my-market-data-bucket/raw/.../symbol=BTCUSDT/

✓  ETHUSDT (workspace: ethusdt)
     Instances: 3
       - i-0jkl22222
       - i-0mno33333
       - i-0pqr44444
     S3 Bucket: my-market-data-bucket
     S3 Path: s3://my-market-data-bucket/raw/.../symbol=ETHUSDT/
```

### Check Instance Status

```bash
# Get instance IDs for ETHUSDT
terraform workspace select ethusdt
terraform output collector_instance_ids

# Check instance health in AWS
aws ec2 describe-instance-status --instance-ids i-xxxxx i-yyyyy i-zzzzz
```

### Monitor Data Collection

```bash
# Check recent uploads for ETHUSDT
aws s3 ls s3://your-bucket/raw/exchange=binance/stream=trade/symbol=ETHUSDT/ \
  --recursive --human-readable | tail -20

# Count files for ETHUSDT
aws s3 ls s3://your-bucket/raw/exchange=binance/stream=trade/symbol=ETHUSDT/ \
  --recursive | wc -l

# Download sample data
aws s3 cp s3://your-bucket/raw/.../symbol=ETHUSDT/part-xxx.jsonl.gz - | gunzip | head -10
```

## Removing a Symbol

### Safely Remove One Symbol

```bash
# This ONLY removes ETHUSDT instances
# BTCUSDT instances continue running
./deploy-symbol.sh ETHUSDT destroy
```

**Confirmation:**
```
⚠ This will DESTROY all resources for ETHUSDT
⚠ This includes:
  - 3 EC2 instances
  - Security group (if not used by other symbols)
  - VPC resources (if not used by other symbols)

ℹ S3 bucket and data will NOT be deleted

Are you sure? (yes/no):
```

### What Gets Deleted vs. Kept

| Resource | Status | Note |
|----------|--------|------|
| EC2 instances for ETHUSDT | ❌ Deleted | Only ETHUSDT instances |
| EC2 instances for BTCUSDT | ✅ Kept | Other symbols unaffected |
| S3 bucket | ✅ Kept | Shared resource |
| S3 data (ETHUSDT) | ✅ Kept | Data preserved |
| VPC/Subnets | ✅ Kept | Shared by all symbols |
| Security group | ✅ Kept | Shared by all symbols |

## Cost Management

### Per-Symbol Cost

Each symbol costs approximately:
- **EC2 (3x t3.micro):** $22.50/month
- **S3 Storage:** ~$23/month per TB
- **S3 Requests:** ~$0.25/month

### Total Cost Examples

| Symbols | EC2 Cost | S3 (shared) | Total |
|---------|----------|-------------|-------|
| 1 (BTCUSDT) | $22.50 | $23.25 | $45.75 |
| 2 (BTC + ETH) | $45.00 | $23.25 | $68.25 |
| 3 (BTC + ETH + SOL) | $67.50 | $23.25 | $90.75 |
| 5 symbols | $112.50 | $23.25 | $135.75 |

**S3 Note:** Storage cost depends on total data across all symbols.

### Cost Optimization Tips

1. **Share VPC:** All symbols use same VPC (already implemented)
2. **S3 VPC Endpoint:** Reduces data transfer costs (already enabled)
3. **Glacier Transitions:** Move old data to cheaper storage
4. **Right-size instances:** Use t4g.micro (ARM, 20% cheaper)
5. **Reduce instance count:** Use 2 instead of 3 per symbol

## Advanced: Workspace Management

### Manual Workspace Commands

If you prefer manual control:

```bash
# List workspaces
terraform workspace list

# Create workspace
terraform workspace new ethusdt

# Switch workspace
terraform workspace select ethusdt

# Deploy in current workspace
terraform apply -var="symbol=ETHUSDT"

# Delete workspace (must be empty first)
terraform workspace select default
terraform workspace delete ethusdt
```

### Workspace State Files

Each workspace maintains separate state:
```
.terraform/
└── terraform.tfstate.d/
    ├── btcusdt/
    │   └── terraform.tfstate
    ├── ethusdt/
    │   └── terraform.tfstate
    └── solusdt/
        └── terraform.tfstate
```

**Never edit these files manually!**

## Troubleshooting

### Issue: "Workspace already exists"

```bash
./deploy-symbol.sh BTCUSDT apply
# Error: Workspace btcusdt already exists
```

**Solution:** Workspace exists. Use `plan` to check current state:
```bash
./deploy-symbol.sh BTCUSDT plan
```

### Issue: "Can't destroy - resources in use"

```bash
./deploy-symbol.sh BTCUSDT destroy
# Error: Security group in use by other instances
```

**Cause:** VPC resources shared across symbols.

**Solution:** Destroy all symbols first, or use targeted destroy:
```bash
terraform workspace select btcusdt
terraform destroy -target=aws_instance.collector
```

### Issue: Wrong symbol collecting data

```bash
# Deployed ETHUSDT but collecting BTCUSDT data
```

**Cause:** User data script cached or workspace mismatch.

**Solution:**
```bash
# Ensure in correct workspace
terraform workspace select ethusdt

# Force instance replacement
terraform taint aws_instance.collector[0]
terraform taint aws_instance.collector[1]
terraform taint aws_instance.collector[2]
terraform apply -var="symbol=ETHUSDT"
```

### Issue: Can't find deployed symbols

```bash
./list-deployments.sh
# Shows no symbols
```

**Cause:** Not in correct directory or Terraform not initialized.

**Solution:**
```bash
cd infra/terraform
terraform init
./list-deployments.sh
```

## Best Practices

### 1. Always Use the Script

✅ **Do:**
```bash
./deploy-symbol.sh ETHUSDT apply
```

❌ **Don't:**
```bash
terraform apply  # No symbol specified!
```

### 2. Check Before Deploying

```bash
# Always plan first
./deploy-symbol.sh NEWCOIN plan

# Review what will change
# Then apply
./deploy-symbol.sh NEWCOIN apply
```

### 3. Use Consistent Naming

✅ **Do:** `BTCUSDT`, `ETHUSDT`, `SOLUSDT` (uppercase)
❌ **Don't:** `btcusdt`, `BtcUsdt`, `btc-usdt`

### 4. Document Your Symbols

Keep a list of active symbols:
```bash
# Create symbols.txt
cat > symbols.txt << EOF
BTCUSDT - Deployed 2025-01-15
ETHUSDT - Deployed 2025-01-16
SOLUSDT - Deployed 2025-01-17
EOF
```

### 5. Monitor All Symbols

```bash
# Check all symbols daily
./list-deployments.sh

# Verify data flow for each
for symbol in BTCUSDT ETHUSDT SOLUSDT; do
  echo "Checking $symbol..."
  aws s3 ls s3://bucket/raw/.../symbol=$symbol/ --recursive | tail -5
done
```

## CI/CD Integration

For automated deployment of multiple symbols, see the GitHub Actions workflow:

```yaml
# .github/workflows/deploy-symbols.yml
name: Deploy Multiple Symbols

on:
  workflow_dispatch:
    inputs:
      symbol:
        description: 'Symbol to deploy (e.g., ETHUSDT)'
        required: true

jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - name: Deploy Symbol
        run: |
          cd infra/terraform
          ./deploy-symbol.sh ${{ github.event.inputs.symbol }} apply
```

## Summary

### Key Takeaways

1. ✅ **Use workspaces** - Each symbol is isolated
2. ✅ **Use deployment script** - Safe and convenient
3. ✅ **Symbols don't interfere** - Add/remove independently
4. ✅ **Share S3 bucket** - All symbols use same bucket
5. ✅ **Cost scales linearly** - ~$22.50/month per symbol

### Quick Reference

```bash
# Deploy new symbol
./deploy-symbol.sh ETHUSDT apply

# List all symbols
./list-deployments.sh

# Remove symbol
./deploy-symbol.sh ETHUSDT destroy

# Check symbol status
./deploy-symbol.sh ETHUSDT output
```

**You can now safely add as many symbols as you need!** 🚀
