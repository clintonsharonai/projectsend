#!/usr/bin/env bash
#
# create_access.sh — Mint a folder-bound, upload-only ProjectSend token for a customer.
#
# Bash port of create_access.py. Same behaviour, same registry format, so the
# Python and bash versions are interchangeable (they share ~/.projectsend/registry.json).
#
# What the minted token can and cannot do:
#   * Upload-only  — it carries the `upload_only` ability, which the upload
#     endpoints accept and *no read endpoint does*. The customer can push files
#     in but cannot list, view, download, or comment on anything.
#   * Folder-bound — bound to --folder-id. The server forces every upload into
#     that folder and ignores any other folder the customer might specify.
#
# The minted token is recorded in a local JSON registry (one entry per customer)
# and handed to the customer, who uses it with upload_diagnostics.sh.
#
# Requires: bash, curl, jq.
#
# Environment:
#   PROJECTSEND_ISSUER_TOKEN   (required) the issuer token
#   PROJECTSEND_BASE_URL       (optional) default https://fs.sharonai.cloud
#
# Examples:
#   # Mint (or reuse) a 7-day upload token for a customer, bound to folder 1:
#   PROJECTSEND_ISSUER_TOKEN='1|psend_…' \
#     ./create_access.sh --customer "Acme Corp" \
#       --email acme@example.com --folder-id 1
#
#   # Force a fresh token even if a valid one exists:
#   ./create_access.sh --customer "Acme Corp" --email acme@example.com --folder-id 1 --force
#
#   # Machine-readable output (the registry entry as JSON):
#   ./create_access.sh --customer "Acme Corp" --email acme@example.com --folder-id 1 --json
#
set -euo pipefail

BASE_URL="${PROJECTSEND_BASE_URL:-https://fs.sharonai.cloud}"
ISSUER="${PROJECTSEND_ISSUER_TOKEN:-}"
REGISTRY="${HOME}/.projectsend/registry.json"

CUSTOMER="" EMAIL="" FOLDER_ID="" DAYS=7 FORCE=0 JSON=0 TICKET="" NAME=""

usage() {
  cat <<'EOF'
Usage: create_access.sh --customer NAME --folder-id N [options]

  --customer NAME   Customer / company name (required)
  --email EMAIL     Customer email (used as the registry key)
  --folder-id N     Id of the folder this token may upload into (required)
  --days N          Token lifetime in days (default 7)
  --ticket REF      Optional ticket / reference to store with the token
  --name NAME       Token name (default: <customer>-<date>)
  --force           Mint a new token even if a valid one for the same folder exists
  --registry PATH   Registry path (default ~/.projectsend/registry.json)
  --json            Print the registry entry as JSON
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --customer)  CUSTOMER="$2"; shift 2;;
    --email)     EMAIL="$2"; shift 2;;
    --folder-id) FOLDER_ID="$2"; shift 2;;
    --days)      DAYS="$2"; shift 2;;
    --ticket)    TICKET="$2"; shift 2;;
    --name)      NAME="$2"; shift 2;;
    --force)     FORCE=1; shift;;
    --registry)  REGISTRY="$2"; shift 2;;
    --json)      JSON=1; shift;;
    -h|--help)   usage; exit 0;;
    *) echo "error: unknown argument: $1" >&2; usage >&2; exit 2;;
  esac
done

command -v curl >/dev/null 2>&1 || { echo "error: curl is required" >&2; exit 2; }
command -v jq   >/dev/null 2>&1 || { echo "error: jq is required" >&2; exit 2; }

# The issuer token is the root of trust for this script. It is read from the
# environment (never the command line) so it does not leak into shell history
# or process listings.
[[ -n "$ISSUER" ]]   || { echo "error: PROJECTSEND_ISSUER_TOKEN is not set" >&2; exit 2; }
[[ -n "$CUSTOMER" ]] || { echo "error: --customer is required" >&2; exit 2; }
[[ -n "$FOLDER_ID" ]] || { echo "error: --folder-id is required" >&2; exit 2; }

# ---------------------------------------------------------------------------
# Registry helpers
# ---------------------------------------------------------------------------

# Stable registry key: the email if given, else the normalized name.
customer_key() { # $1=customer $2=email
  if [[ -n "$2" ]]; then
    printf '%s' "$2" | tr '[:upper:]' '[:lower:]' | xargs
  else
    printf '%s' "$1" | tr '[:upper:]' '[:lower:]' | sed -E 's/[[:space:]]+/ /g' | xargs
  fi
}

# Turn a customer name into a safe token-name fragment (acme-corp).
slugify() { # $1=value
  printf '%s' "$1" | tr '[:upper:]' '[:lower:]' \
    | sed -E 's/[^a-z0-9]+/-/g; s/^-+//; s/-+$//' | cut -c1-120
}

# Read one registry entry (JSON) by key, or nothing if absent/corrupt.
load_entry() { # $1=key
  [[ -f "$REGISTRY" ]] || return 0
  jq -c --arg k "$1" '.[$k] // empty' "$REGISTRY" 2>/dev/null || true
}

# Pull a field out of an entry JSON.
entry_field() { # $1=entry-json $2=field
  jq -r --arg f "$2" '.[$f] // empty' <<<"$1" 2>/dev/null || true
}

# Whether an entry's token has passed its expires_at.
# A missing/empty expires_at means "never expires" (not expired). We compare the
# first 19 chars (YYYY-MM-DDTHH:MM:SS) so a trailing timezone/microsecond
# difference never breaks the check.
is_expired() { # $1=entry-json
  local exp now
  exp=$(entry_field "$1" "expires_at")
  [[ -z "$exp" ]] && return 1
  now=$(date -u +%Y-%m-%dT%H:%M:%S)
  exp="${exp:0:19}"
  [[ "$exp" < "$now" || "$exp" == "$now" ]]
}

# ---------------------------------------------------------------------------
# Idempotent: reuse a still-valid token bound to the SAME folder, unless --force.
# The folder must match because a token is bound to exactly one folder — a valid
# token for a different folder is the wrong credential.
# ---------------------------------------------------------------------------
KEY=$(customer_key "$CUSTOMER" "$EMAIL")
EXISTING=$(load_entry "$KEY")

if [[ -n "$EXISTING" && "$FORCE" -eq 0 ]]; then
  EX_FOLDER=$(entry_field "$EXISTING" "folder_id")
  if [[ "$EX_FOLDER" == "$FOLDER_ID" ]] && ! is_expired "$EXISTING"; then
    if [[ "$JSON" -eq 1 ]]; then
      jq . <<<"$EXISTING"
    else
      echo "✓ Reusing existing token for \"$CUSTOMER\" ($KEY)"
      echo "  Token:   $(entry_field "$EXISTING" "plain_text")"
      echo "  Folder:  $EX_FOLDER"
      echo "  Expires: $(entry_field "$EXISTING" "expires_at")"
    fi
    exit 0
  fi
fi

# ---------------------------------------------------------------------------
# Mint
# ---------------------------------------------------------------------------
TOKEN_NAME="${NAME:-$(slugify "$CUSTOMER")-$(date -u +%Y-%m-%d)}"

# upload_only + folder_id is the customer token shape: it can only push files
# into $FOLDER_ID and nothing else.
PAYLOAD=$(jq -n \
  --arg name "$TOKEN_NAME" \
  --argjson folder_id "$FOLDER_ID" \
  --argjson days "$DAYS" \
  '{name:$name, abilities:["upload_only"], folder_id:$folder_id, expires_in_days:$days}')

# -w '\n%{http_code}' appends the status on its own line so we can split body/code.
RESPONSE=$(curl -sS -w $'\n%{http_code}' -X POST \
  -H "Authorization: Bearer $ISSUER" \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json' \
  -d "$PAYLOAD" \
  "$BASE_URL/api/v1/tokens")

HTTP_CODE=$(tail -n1 <<<"$RESPONSE")
BODY=$(sed '$d' <<<"$RESPONSE")

if [[ ! "$HTTP_CODE" =~ ^2 ]]; then
  echo "error: mint failed — $BODY" >&2
  TYPE=$(jq -r '.type // "unknown"' <<<"$BODY" 2>/dev/null || echo unknown)
  # A validation_failed here almost always means the folder is not one the
  # issuer may write to — say so.
  if [[ "$TYPE" == "validation_failed" ]]; then
    echo "hint: check that --folder-id is a folder this issuer can upload into" >&2
  fi
  exit 1
fi

PLAIN=$(jq -r '.data.plain_text // empty' <<<"$BODY")
EXPIRES=$(jq -r '.data.expires_at // empty' <<<"$BODY")
ABILITIES=$(jq -c '.data.abilities // ["upload_only"]' <<<"$BODY")
EX_NAME=$(jq -r '.data.name // empty' <<<"$BODY")

[[ -n "$PLAIN" ]] || { echo "error: mint succeeded but no plain_text in response" >&2; exit 1; }

# ---------------------------------------------------------------------------
# Record the token locally so support can look it up by customer without
# re-minting (which would orphan the old token). Atomic write (tmp + mv).
# ---------------------------------------------------------------------------
mkdir -p "$(dirname "$REGISTRY")"
ENTRY=$(jq -n \
  --arg customer "$CUSTOMER" \
  --arg email "$EMAIL" \
  --arg ticket "$TICKET" \
  --arg token_name "${EX_NAME:-$TOKEN_NAME}" \
  --arg plain_text "$PLAIN" \
  --argjson abilities "$ABILITIES" \
  --argjson folder_id "$FOLDER_ID" \
  --arg expires_at "$EXPIRES" \
  --arg created_at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  --argjson days "$DAYS" \
  '{customer:$customer, email:$email, ticket:$ticket, token_name:$token_name,
    plain_text:$plain_text, abilities:$abilities, folder_id:$folder_id,
    expires_at:$expires_at, created_at:$created_at, days:$days}')

if [[ -f "$REGISTRY" ]]; then
  jq --arg k "$KEY" --argjson e "$ENTRY" '.[$k] = $e' "$REGISTRY" > "$REGISTRY.tmp"
else
  jq -n --arg k "$KEY" --argjson e "$ENTRY" '{($k): $e}' > "$REGISTRY.tmp"
fi
mv "$REGISTRY.tmp" "$REGISTRY"

if [[ "$JSON" -eq 1 ]]; then
  jq . <<<"$ENTRY"
  exit 0
fi

echo "✓ Created upload token for \"$CUSTOMER\" ($KEY)"
echo "  Token:     $PLAIN"
echo "  Name:      ${EX_NAME:-$TOKEN_NAME}"
echo "  Abilities: $(jq -r 'join(", ")' <<<"$ABILITIES")"
echo "  Folder:    $FOLDER_ID  (uploads are forced here)"
echo "  Expires:   ${EXPIRES:-never}"
echo "  Registry:  $REGISTRY"
echo
echo "Hand the token to the customer, or upload on their behalf with:"
echo "  PROJECTSEND_TOKEN='$PLAIN' ./upload_diagnostics.sh --file <diagnostics-file>"
