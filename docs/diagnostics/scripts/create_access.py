#!/usr/bin/env python3
"""Mint a folder-bound, upload-only ProjectSend token for a customer.

This is the "access creation" half of the diagnostics flow. It holds the
long-lived **issuer** token (granted only ``create_api_tokens``) and, per
customer, mints a short-lived customer token via ``POST /api/v1/tokens``.

What the minted token can and cannot do
=======================================
Since the folder-bound redesign, a customer token is fixed at mint time to do
exactly one thing:

* **Upload-only.** It carries the ``upload_only`` ability, which the upload
  endpoints accept and *no read endpoint does*. The customer can push files
  in but cannot list, view, download, or comment on anything in the library.
* **Folder-bound.** It is bound to the folder you pass with ``--folder-id``.
  The server forces every upload into that folder and ignores any other folder
  the customer might try to specify. The customer cannot write anywhere else.

The minted token is recorded in a local JSON registry (one entry per customer)
and handed to the customer, who uses it with ``upload_diagnostics.py``.

Zero dependencies (Python 3.8+ stdlib only).

Environment:
  PROJECTSEND_ISSUER_TOKEN   (required) the issuer token
  PROJECTSEND_BASE_URL       (optional) default https://fs.sharonai.cloud

Examples:
  # Mint (or reuse) a 7-day upload token for a customer, bound to folder 1:
  PROJECTSEND_ISSUER_TOKEN='1|psend_…' \\
    python3 create_access.py --customer "Acme Corp" \\
      --email acme@example.com --folder-id 1

  # Force a fresh token even if a valid one exists:
  python3 create_access.py --customer "Acme Corp" \\
    --email acme@example.com --folder-id 1 --force

  # Machine-readable output (the registry entry as JSON):
  python3 create_access.py --customer "Acme Corp" \\
    --email acme@example.com --folder-id 1 --json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from typing import Any, Dict, Optional

# Allow running as a plain script from this directory.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import projectsend_client as ps  # noqa: E402

# Where the local token registry lives by default. It is a plain JSON object
# keyed by customer (email if given, else normalized name), so support can
# look a customer's token up without asking them for it again.
DEFAULT_REGISTRY = os.path.join(os.path.expanduser("~"), ".projectsend", "registry.json")


# ---------------------------------------------------------------------------
# Registry helpers
# ---------------------------------------------------------------------------

def load_registry(path: str) -> Dict[str, Any]:
    """Read the token registry, tolerating a missing or corrupt file.

    A missing file means "no tokens yet" (return an empty dict). A corrupt
    file is warned about and treated as empty rather than crashing — the
    registry is a convenience cache, not a source of truth (the server is).
    """
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError) as exc:
        print(f"warning: could not read registry {path} ({exc}); starting fresh", file=sys.stderr)
        return {}


def save_registry(path: str, data: Dict[str, Any]) -> None:
    """Write the registry atomically.

    Writes to a temp file then ``os.replace``s it into place, so a crash
    mid-write never leaves a half-written (and thus unreadable) registry.
    """
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, sort_keys=True)
        handle.write("\n")
    os.replace(tmp, path)  # atomic on POSIX


def customer_key(customer: str, email: Optional[str]) -> str:
    """Stable registry key: the email if given, else the normalized name.

    The key is what ties a customer to their token across runs, so it must be
    stable. Email is preferred because it is unique; the name is a fallback
    (whitespace-collapsed, lowercased) for when no email is on hand.
    """
    if email:
        return email.strip().lower()
    return re.sub(r"\s+", " ", customer.strip()).lower()


def slugify(value: str, max_len: int = 120) -> str:
    """Turn a customer name into a safe token-name fragment (``acme-corp``)."""
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug[:max_len].rstrip("-") or "customer"


def is_expired(entry: Dict[str, Any]) -> bool:
    """Whether a registry entry's token has passed its ``expires_at``.

    A missing/empty ``expires_at`` means "never expires" (not expired). A
    malformed timestamp is treated as not-expired so a bad value never blocks
    a still-usable token; the server is the final authority on expiry.
    """
    expires_at = entry.get("expires_at")
    if not expires_at:
        return False  # never expires
    try:
        exp = datetime.fromisoformat(expires_at)
    except ValueError:
        return False
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    return exp <= datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Mint a folder-bound, upload-only ProjectSend token for a customer.",
    )
    parser.add_argument("--customer", required=True, help="Customer / company name")
    parser.add_argument("--email", help="Customer email (used as the registry key)")
    # The folder the token is bound to. Required: a customer token without a
    # folder is not a customer token. Find the id in the ProjectSend UI (the
    # customer-tokens screen lists folders) or via the API.
    parser.add_argument("--folder-id", type=int, required=True,
                        help="Id of the folder this token may upload into (required)")
    parser.add_argument("--days", type=int, default=7, help="Token lifetime in days (default 7)")
    parser.add_argument("--ticket", help="Optional ticket / reference to store with the token")
    parser.add_argument("--name", help="Token name (default: <customer>-<date>)")
    parser.add_argument("--force", action="store_true",
                        help="Mint a new token even if a valid one for the same folder exists")
    parser.add_argument("--registry", default=DEFAULT_REGISTRY, help=f"Registry path (default {DEFAULT_REGISTRY})")
    parser.add_argument("--json", action="store_true", help="Print the registry entry as JSON")
    return parser


def main(argv: Optional[list] = None) -> int:
    args = build_parser().parse_args(argv)

    # The issuer token is the root of trust for this script. It is read from
    # the environment (never the command line) so it does not leak into shell
    # history or process listings.
    issuer = os.environ.get("PROJECTSEND_ISSUER_TOKEN", "").strip()
    if not issuer:
        print("error: PROJECTSEND_ISSUER_TOKEN is not set", file=sys.stderr)
        return 2

    key = customer_key(args.customer, args.email)
    registry = load_registry(args.registry)
    existing = registry.get(key)

    # Idempotent: reuse a still-valid token bound to the SAME folder, unless
    # --force. The folder must match because a token is bound to exactly one
    # folder — a valid token for a different folder is the wrong credential.
    same_folder = existing and existing.get("folder_id") == args.folder_id
    if existing and same_folder and not args.force and not is_expired(existing):
        if args.json:
            print(json.dumps(existing, indent=2, sort_keys=True))
        else:
            print(f'✓ Reusing existing token for "{args.customer}" ({key})')
            print(f"  Token:   {existing['plain_text']}")
            print(f"  Folder:  {existing['folder_id']}")
            print(f"  Expires: {existing.get('expires_at', 'never')}")
        return 0

    # A name the issuer can recognize later when revoking.
    token_name = args.name or f"{slugify(args.customer)}-{datetime.now(timezone.utc):%Y-%m-%d}"

    try:
        # upload_only + folder_id is the customer token shape: it can only
        # push files into args.folder_id and nothing else.
        data = ps.mint_token(
            issuer,
            name=token_name,
            folder_id=args.folder_id,
            abilities=ps.CUSTOMER_TOKEN_ABILITIES,
            expires_in_days=args.days,
        )
    except ps.ProjectSendError as exc:
        # A validation_failed here almost always means the folder is not one
        # the issuer may write to — surface the field errors to say so.
        print(f"error: mint failed — {exc}", file=sys.stderr)
        if exc.type == "validation_failed" and exc.errors:
            print("hint: check that --folder-id is a folder this issuer can upload into", file=sys.stderr)
        return 1
    except ps.ProjectSendConnectionError as exc:
        print(f"error: could not reach ProjectSend — {exc}", file=sys.stderr)
        return 1

    # Record the token locally so support can look it up by customer without
    # re-minting (which would orphan the old token).
    entry: Dict[str, Any] = {
        "customer": args.customer,
        "email": args.email,
        "ticket": args.ticket,
        "token_name": data.get("name", token_name),
        "plain_text": data["plain_text"],
        "abilities": data.get("abilities", list(ps.CUSTOMER_TOKEN_ABILITIES)),
        "folder_id": data.get("folder_id", args.folder_id),
        "expires_at": data.get("expires_at"),
        "created_at": data.get("created_at"),
        "days": args.days,
    }
    registry[key] = entry
    save_registry(args.registry, registry)

    if args.json:
        print(json.dumps(entry, indent=2, sort_keys=True))
        return 0

    print(f'✓ Created upload token for "{args.customer}" ({key})')
    print(f"  Token:     {entry['plain_text']}")
    print(f"  Name:      {entry['token_name']}")
    print(f"  Abilities: {', '.join(entry['abilities'])}")
    print(f"  Folder:    {entry['folder_id']}  (uploads are forced here)")
    print(f"  Expires:   {entry.get('expires_at', 'never')}")
    print(f"  Registry:  {args.registry}")
    print()
    print("Hand the token to the customer, or upload on their behalf with:")
    print(f"  PROJECTSEND_TOKEN='{entry['plain_text']}' python3 upload_diagnostics.py --file <diagnostics-file>")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
