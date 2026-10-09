# retail_banking_agent

A retail banking agent built with the Strands SDK, deployed on AWS Bedrock AgentCore, and connected to watsonx Orchestrate for tracing.

---

## Collaborator Setup

Each collaborator runs entirely within their own AWS account and IBM Cloud instance — no credentials need to be shared.

### Step 1: AWS Account & CLI

1. Create a free AWS account at [aws.amazon.com](https://aws.amazon.com) if you don't have one.
2. Install and configure the AWS CLI:
   ```bash
   aws configure
   ```
   Enter your **Access Key ID**, **Secret Access Key**, and set the default region to `us-east-2`.

---

### Step 2: Create Secrets in AWS Secrets Manager

Create these two secrets in your account's Secrets Manager (region `us-east-2`):

**Secret 1 — IBM Cloud API key** (used to authenticate with watsonx Orchestrate):
```bash
aws secretsmanager create-secret \
  --region us-east-2 \
  --name "retailbank/wxo-api-key" \
  --secret-string "your-ibm-cloud-api-key-here"
```

**Secret 2 — JWT signing key** (used by the A2A endpoint):
```bash
aws secretsmanager create-secret \
  --region us-east-2 \
  --name "retailbank/a2a-jwt-signing-key" \
  --secret-string '{"issuer":"retail-banking-agent","kid":"key-1","private_key_pem":"-----BEGIN RSA PRIVATE KEY-----\n..."}'
```

---

### Step 3: Update the IAM Policy

In [`wxo-secret-policy.json`](wxo-secret-policy.json), replace `<YOUR_AWS_ACCOUNT_ID>` with your own 12-digit AWS account ID (found under **AWS Console → top-right account menu**), then attach the policy to your IAM role.

---

### Step 4: Configure Local Dev Script

Copy the dev script to a local file (gitignored — will not be committed):
```bash
cp scripts/dev_with_orchestrate.sh scripts/dev_with_orchestrate.local.sh
```

Open `scripts/dev_with_orchestrate.local.sh` and fill in these three values:

| Variable | Where to find it |
|---|---|
| `WXO_AGENT_ID` | watsonx Orchestrate UI → your registered external agent's ID |
| `WXO_TENANT_ID` | IBM Cloud → your Orchestrate instance → format: `<account-id>_<instance-id>` |
| `WXO_OTEL_URL` | IBM Cloud → your Orchestrate instance → the OTLP traces endpoint URL |

`WXO_API_KEY_SECRET_ID` defaults to `retailbank/wxo-api-key` — keep it as-is if you used that name in Step 2.

---

### Step 5: Run the Agent

```bash
# Install dependencies
pip install -e .

# Run locally with Orchestrate telemetry
bash scripts/dev_with_orchestrate.local.sh

# Or run without telemetry
agentcore dev
```

---

## Notes

- The agent has **no required `.env` file** — all secrets are fetched from AWS Secrets Manager at runtime via the standard AWS credential chain.
- Local runs are tagged `draft` in the Orchestrate Analyze view; deployed runtimes use `live`.
- The A2A entrypoint (`a2a_main.py`) allows registering the agent as an external agent in watsonx Orchestrate.
