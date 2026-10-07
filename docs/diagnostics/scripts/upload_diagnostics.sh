#!/usr/bin/env bash
#
# upload_diagnostics.sh — Upload a diagnostics file to ProjectSend with a customer token.
#
# Bash port of upload_diagnostics.py. Same behaviour, same registry format, so the
# Python and bash versions are interchangeable.
#
# Where the file lands:
#   There is NO --folder-id option on purpose. The destination folder is fixed by
#   the token itself: when the token was minted it was bound to a folder, and the
#   server forces every upload into that folder, ignoring any folder the caller
#   might try to specify. So the customer (or support, on their behalf) simply
#   uploads the file — it lands in the customer's assigned folder and nowhere
#   else. That is the security property of the folder-bound design.
#
# What the token can do:
#   The token carries `upload_only`: it can push this file in, but it cannot
#   list, view, download, or comment on anything in the library. So this script
#   only ever uploads; it does not (and cannot) fetch files back.
#
# Requires: bash, curl, jq.
#
# Token resolution (first match wins):
#   1. --token TOKEN
#   2. --customer KEY   (looks the token up in the local registry)
#   3. PROJECTSEND_TOKEN environment variable
#
# Environment:
#   PROJECTSEND_TOKEN    the customer upload token (if not passed as --token)
#   PROJECTSEND_BASE_URL (optional) default https://fs.sharonai.cloud
#
# Examples:
#   # Customer uploads with their token:
#   PROJECTSEND_TOKEN='7|psend_…' ./upload_diagnostics.sh --file diagnostics.txt
#
#   # Support uploads on a customer's behalf, pulling the token from the registry:
#   ./upload_diagnostics.sh --customer acme@example.com --file diagnostics.txt
#
#   # With a descriptive name and machine-readable output:
#   ./upload_diagnostics.sh --file diag.txt --name "Acme host123 2026-10-07" --json
#
set -euo pipefail

BASE_URL="${PROJECTSEND_BASE_URL:-https://fs.sharonai.cloud}"
REGISTRY="${HOME}/.projectsend/registry.json"

FILE="" NAME="" DESCRIPTION="" TOKEN="" CUSTOMER="" JSON=0

usage() {
  cat <<'EOF'
Usage: upload_diagnostics.sh --file PATH [options]

  --file PATH         Path to the diagnostics file to upload (required)
  --name NAME         Display name (default: <file-stem> <date>)
  --description TEXT  Optional description (max 2000 chars)
  --token TOKEN       Customer upload token (overrides env/registry)
  --customer KEY      Registry key (email) to look the token up
  --registry PATH     Registry path (default ~/.projectsend/registry.json)
  --json              Print the response data as JSON

Note: there is no --folder-id. The folder is fixed by the token (set at mint
time); the server forces the upload into the bound folder.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --file)        FILE="$2"; shift 2;;
    --name)        NAME="$2"; shift 2;;
    --description) DESCRIPTION="$2"; shift 2;;
    --token)       TOKEN="$2"; shift 2;;
    --customer)    CUSTOMER="$2"; shift 2;;
    --registry)    REGISTRY="$2"; shift 2;;
    --json)        JSON=1; shift;;
    -h|--help)     usage; exit 0;;
    *) echo "error: unknown argument: $1" >&2; usage >&2; exit 2;;
  esac
done

command -v curl >/dev/null 2>&1 || { echo "error: curl is required" >&2; exit 2; }
command -v jq   >/dev/null 2>&1 || { echo "error: jq is required" >&2; exit 2; }

# ---------------------------------------------------------------------------
# Resolve the token to use, in priority order:
#   1. --token  2. --customer (registry)  3. PROJECTSEND_TOKEN
# ---------------------------------------------------------------------------
if [[ -z "$TOKEN" && -n "$CUSTOMER" ]]; then
  KEY=$(printf '%s' "$CUSTOMER" | tr '[:upper:]' '[:lower:]' | xargs)
  if [[ -f "$REGISTRY" ]]; then
    TOKEN=$(jq -r --arg k "$KEY" '.[$k].plain_text // empty' "$REGISTRY" 2>/dev/null || true)
  fi
  if [[ -z "$TOKEN" ]]; then
    echo "error: no token found for customer '$KEY' in $REGISTRY" >&2
    exit 2
  fi
fi
if [[ -z "$TOKEN" ]]; then
  TOKEN="${PROJECTSEND_TOKEN:-}"
fi
if [[ -z "$TOKEN" ]]; then
  echo "error: no token provided — pass --token, --customer, or set PROJECTSEND_TOKEN" >&2
  exit 2
fi

[[ -n "$FILE" ]] || { echo "error: --file is required" >&2; exit 2; }
[[ -f "$FILE" ]] || { echo "error: file not found: $FILE" >&2; exit 2; }

# A sensible default name: the file stem plus today's date, so repeated uploads
# from the same host do not collide in the folder.
STEM=$(basename "$FILE"); STEM="${STEM%.*}"
NAME="${NAME:-$STEM $(date -u +%Y-%m-%d)}"

# ---------------------------------------------------------------------------
# Upload. No folder_id is sent: the server places the file in the folder the
# token is bound to.
# ---------------------------------------------------------------------------
CURL_ARGS=(-sS -w $'\n%{http_code}' -X POST
  -H "Authorization: Bearer $TOKEN"
  -H 'Accept: application/json'
  -F "file=@$FILE"
  -F "name=$NAME")
[[ -n "$DESCRIPTION" ]] && CURL_ARGS+=(-F "description=$DESCRIPTION")

RESPONSE=$(curl "${CURL_ARGS[@]}" "$BASE_URL/api/v1/files")
HTTP_CODE=$(tail -n1 <<<"$RESPONSE")
BODY=$(sed '$d' <<<"$RESPONSE")

if [[ ! "$HTTP_CODE" =~ ^2 ]]; then
  TYPE=$(jq -r '.type // "unknown"' <<<"$BODY" 2>/dev/null || echo unknown)
  # Branch on the stable RFC 7807 type slug for an actionable message. These
  # hints are written for the folder-bound, upload-only token.
  case "$TYPE" in
    unauthenticated)   HINT="the token is missing, invalid, or expired — re-run create_access.sh";;
    forbidden)         HINT="the token is valid but can no longer upload (owner demoted, or its bound folder was removed)";;
    validation_failed) HINT="the request was invalid — see field errors above";;
    payload_too_large) HINT="the file exceeds the server's upload limit";;
    too_many_requests) HINT="rate limited — wait a moment and retry";;
    not_found)         HINT="the endpoint was not found — is the server patched and restarted?";;
    *)                 HINT="";;
  esac
  echo "error: upload failed — $BODY" >&2
  [[ -n "$HINT" ]] && echo "hint: $HINT" >&2
  exit 1
fi

if [[ "$JSON" -eq 1 ]]; then
  jq '.data // .' <<<"$BODY"
  exit 0
fi

FILE_ID=$(jq -r '.data.id // empty' <<<"$BODY")
SLUG=$(jq -r '.data.slug // empty' <<<"$BODY")
CHECKSUM=$(jq -r '.data.checksum // empty' <<<"$BODY")
SIZE=$(jq -r '.data.size // empty' <<<"$BODY")
EX_NAME=$(jq -r '.data.name // empty' <<<"$BODY")

# The token is upload-only, so it cannot produce a working download link; report
# the identifiers the issuer needs to find the file in the UI.
echo "✓ Uploaded \"${EX_NAME:-$NAME}\" (${SIZE:-0} bytes)"
echo "  ID:       $FILE_ID"
echo "  Slug:     $SLUG"
echo "  Checksum: $CHECKSUM"
echo "  Folder:   the folder this token is bound to (set at mint time)"
