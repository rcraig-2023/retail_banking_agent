"""Issue a self-signed RS256 JWT for the retail banking A2A runtime.

Reads the private signing key from AWS Secrets Manager (never from disk) and prints a
bearer token to stdout. Usage: python scripts/issue_token.py [--days 30]
"""
import argparse
import json
import subprocess
import time
import uuid

import jwt

SECRET_ID = "retailbank/a2a-jwt-signing-key"
REGION = "us-east-2"
AUDIENCE = "retail-banking-a2a"
CLIENT_ID = "watsonx-orchestrate"
MAX_DAYS = 90


def load_signing_secret() -> dict:
    out = subprocess.run(
        ["aws", "secretsmanager", "get-secret-value", "--region", REGION,
         "--secret-id", SECRET_ID, "--query", "SecretString", "--output", "text"],
        capture_output=True, text=True, check=True,
    ).stdout
    return json.loads(out)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=30)
    days = parser.parse_args().days
    if not 1 <= days <= MAX_DAYS:
        raise SystemExit(f"--days must be between 1 and {MAX_DAYS}")

    secret = load_signing_secret()
    now = int(time.time())
    claims = {
        "iss": secret["issuer"],
        "sub": CLIENT_ID,
        "aud": AUDIENCE,
        "client_id": CLIENT_ID,
        "iat": now,
        "nbf": now,
        "exp": now + days * 86400,
        "jti": uuid.uuid4().hex,
    }
    print(jwt.encode(claims, secret["private_key_pem"], algorithm="RS256", headers={"kid": secret["kid"]}))


if __name__ == "__main__":
    main()
