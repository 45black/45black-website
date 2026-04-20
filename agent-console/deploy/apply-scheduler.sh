#!/usr/bin/env bash
# Idempotent Cloud Scheduler setup. Reads deploy/scheduler.yaml and creates
# or updates each job in the current GCP project.
#
# Requires: yq (https://github.com/mikefarah/yq), gcloud CLI.

set -euo pipefail

: "${PROJECT_ID:?set PROJECT_ID}"
: "${REGION:=europe-west1}"
SERVICE_URL="${SERVICE_URL:-$(gcloud run services describe console \
    --region "$REGION" --format='value(status.url)')}"

export PROJECT_ID REGION SERVICE_URL

YAML=deploy/scheduler.yaml

for key in $(yq 'keys | .[]' "$YAML"); do
  name=$(yq ".${key}.name" "$YAML")
  schedule=$(yq ".${key}.schedule" "$YAML")
  tz=$(yq ".${key}.time_zone" "$YAML")
  uri=$(yq ".${key}.http_target.uri" "$YAML" | envsubst)
  body=$(yq -o=json ".${key}.http_target.body_json" "$YAML")
  sa=$(yq ".${key}.http_target.oidc_token.service_account_email" "$YAML" | envsubst)
  audience=$(yq ".${key}.http_target.oidc_token.audience" "$YAML" | envsubst)

  args=(
    --location="$REGION"
    --schedule="$schedule"
    --time-zone="$tz"
    --uri="$uri"
    --http-method=POST
    --message-body="$body"
    --headers="Content-Type=application/json"
    --oidc-service-account-email="$sa"
    --oidc-token-audience="$audience"
  )

  if gcloud scheduler jobs describe "$name" --location="$REGION" >/dev/null 2>&1; then
    echo "→ update $name"
    gcloud scheduler jobs update http "$name" "${args[@]}"
  else
    echo "→ create $name"
    gcloud scheduler jobs create http "$name" "${args[@]}"
  fi
done
