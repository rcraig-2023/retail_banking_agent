#!/usr/bin/env bash
# Run `agentcore dev` with watsonx Orchestrate trace export switched on.
#
# `agentcore dev` does not read the WXO_* values in agentcore/agentcore.json (those only
# reach deployed runtimes), so they are exported here instead. Nothing secret is set: the
# IBM Cloud API key is read from AWS Secrets Manager using your own AWS credentials.
# Local chats are tagged "draft"; deployed runtimes use "live". Filter the Analyze view
# in Orchestrate accordingly.
#
# HOW TO CONFIGURE:
#   Copy this file to dev_with_orchestrate.local.sh and fill in your own values.
#   dev_with_orchestrate.local.sh is gitignored and will not be committed.
#   Alternatively, export these variables in your shell before running this script.
set -euo pipefail

cd "$(dirname "$0")/../../.."   # project root (contains agentcore/)

export AWS_REGION="${AWS_REGION:-us-east-2}"
export WXO_AGENT_ID="${WXO_AGENT_ID:-<your-wxo-agent-id>}"
export WXO_TENANT_ID="${WXO_TENANT_ID:-<your-wxo-tenant-id>}"
export WXO_OTEL_URL="${WXO_OTEL_URL:-https://api.<region>.watson-orchestrate.cloud.ibm.com/instances/<instance-id>/v1/orchestrate/inject/traces}"
export WXO_API_KEY_SECRET_ID="${WXO_API_KEY_SECRET_ID:-retailbank/wxo-api-key}"
export WXO_ENVIRONMENT_NAME="${WXO_ENVIRONMENT_NAME:-draft}"
export WXO_AGENT_NAME="${WXO_AGENT_NAME:-retail_banking_support}"

exec agentcore dev "$@"
