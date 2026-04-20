#!/usr/bin/env python3
"""One-off OAuth consent flow for Gmail + Calendar.

Runs a local browser-based OAuth dance to get a refresh token, then
optionally stores it in Secret Manager for the Cloud Run deployment.

Prerequisites:
    1. Create an OAuth 2.0 Client ID in the GCP console:
       https://console.cloud.google.com/apis/credentials
       - Application type: Desktop app
       - Download the JSON → save as `client_secret.json` in this dir
    2. pip install google-auth-oauthlib google-cloud-secret-manager

Usage:
    python scripts/oauth_setup.py

    # With auto-upload to Secret Manager:
    python scripts/oauth_setup.py --store-secret --project=45black-agents

What happens:
    1. Opens a browser window for you to log in and grant Gmail + Calendar access.
    2. Receives the auth code on a local redirect.
    3. Exchanges it for tokens.
    4. Prints the refresh token.
    5. If --store-secret: uploads it to Secret Manager as a new version of
       `gmail-oauth-refresh-token`.
    6. Writes it to `.env` as GMAIL_OAUTH_REFRESH_TOKEN for local dev.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/calendar",
]

ROOT = Path(__file__).resolve().parent.parent
CLIENT_SECRET_PATH = ROOT / "scripts" / "client_secret.json"
ENV_PATH = ROOT / ".env"


def run_flow() -> dict:
    from google_auth_oauthlib.flow import InstalledAppFlow

    if not CLIENT_SECRET_PATH.exists():
        print(
            f"ERROR: {CLIENT_SECRET_PATH} not found.\n"
            "Download your OAuth Desktop Client JSON from:\n"
            "  https://console.cloud.google.com/apis/credentials\n"
            "and save it as scripts/client_secret.json",
            file=sys.stderr,
        )
        sys.exit(1)

    flow = InstalledAppFlow.from_client_secrets_file(
        str(CLIENT_SECRET_PATH), scopes=SCOPES
    )
    creds = flow.run_local_server(port=8888, open_browser=True)

    return {
        "token": creds.token,
        "refresh_token": creds.refresh_token,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
    }


def store_in_secret_manager(project: str, refresh_token: str) -> str:
    from google.cloud import secretmanager

    client = secretmanager.SecretManagerServiceClient()
    secret_name = f"projects/{project}/secrets/gmail-oauth-refresh-token"

    try:
        client.get_secret(request={"name": secret_name})
    except Exception:
        client.create_secret(
            request={
                "parent": f"projects/{project}",
                "secret_id": "gmail-oauth-refresh-token",
                "secret": {"replication": {"automatic": {}}},
            }
        )

    version = client.add_secret_version(
        request={
            "parent": secret_name,
            "payload": {"data": refresh_token.encode("utf-8")},
        }
    )
    return version.name


def append_to_env(key: str, value: str) -> None:
    """Append or update a key in the .env file."""
    lines = ENV_PATH.read_text().splitlines() if ENV_PATH.exists() else []
    found = False
    for i, line in enumerate(lines):
        if line.startswith(f"{key}="):
            lines[i] = f"{key}={value}"
            found = True
            break
    if not found:
        lines.append(f"{key}={value}")
    ENV_PATH.write_text("\n".join(lines) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="OAuth setup for Gmail + Calendar")
    parser.add_argument(
        "--store-secret",
        action="store_true",
        help="Upload refresh token to Secret Manager",
    )
    parser.add_argument(
        "--project",
        default="",
        help="GCP project ID (required with --store-secret)",
    )
    args = parser.parse_args()

    print("Starting OAuth consent flow...")
    print("A browser window will open. Log in with the Google account whose")
    print("Gmail and Calendar the agents should access.\n")

    tokens = run_flow()
    refresh_token = tokens["refresh_token"]

    print(f"\nRefresh token: {refresh_token[:20]}...{refresh_token[-8:]}")
    print(f"Client ID:     {tokens['client_id'][:20]}...")

    # Store locally in .env
    append_to_env("GMAIL_OAUTH_REFRESH_TOKEN", refresh_token)
    append_to_env("GOOGLE_CLIENT_ID", tokens["client_id"])
    append_to_env("GOOGLE_CLIENT_SECRET", tokens["client_secret"])
    print(f"Written to {ENV_PATH}")

    # Optionally store in Secret Manager
    if args.store_secret:
        if not args.project:
            print("ERROR: --project required with --store-secret", file=sys.stderr)
            sys.exit(1)
        version = store_in_secret_manager(args.project, refresh_token)
        print(f"Stored in Secret Manager: {version}")

        # Also store client_id and client_secret
        from google.cloud import secretmanager

        client = secretmanager.SecretManagerServiceClient()
        for secret_id, val in [
            ("google-oauth-client-id", tokens["client_id"]),
            ("google-oauth-client-secret", tokens["client_secret"]),
        ]:
            secret_name = f"projects/{args.project}/secrets/{secret_id}"
            try:
                client.get_secret(request={"name": secret_name})
            except Exception:
                client.create_secret(
                    request={
                        "parent": f"projects/{args.project}",
                        "secret_id": secret_id,
                        "secret": {"replication": {"automatic": {}}},
                    }
                )
            client.add_secret_version(
                request={
                    "parent": secret_name,
                    "payload": {"data": val.encode("utf-8")},
                }
            )
            print(f"Stored {secret_id} in Secret Manager")

    print("\nDone. Your agents can now access Gmail and Calendar.")
    print("Test locally:  uvicorn backend.main:app --port 8787")


if __name__ == "__main__":
    main()
