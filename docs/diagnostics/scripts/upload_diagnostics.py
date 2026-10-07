#!/usr/bin/env python3
"""Upload a diagnostics file to ProjectSend with a customer token.

This is the "customer upload" half of the diagnostics flow. It takes the
short-lived, folder-bound, upload-only token (created by ``create_access.py``)
and uploads a file via ``POST /api/v1/files``.

Where the file lands
====================
There is **no** ``--folder-id`` option on purpose. The destination folder is
fixed by the token itself: when the token was minted it was bound to a folder,
and the server forces every upload into that folder, ignoring any folder the
caller might try to specify. So the customer (or support, on their behalf)
simply uploads the file — it lands in the customer's assigned folder and
nowhere else. That is the security property of the folder-bound design.

What the token can do
=====================
The token carries ``upload_only``: it can push this file in, but it cannot
list, view, download, or comment on anything in the library. So this script
only ever uploads; it does not (and cannot) fetch files back.

Zero dependencies (Python 3.8+ stdlib only).

Token resolution (first match wins):
  1. --token TOKEN
  2. --customer KEY   (looks the token up in the local registry)
  3. PROJECTSEND_TOKEN environment variable

Environment:
  PROJECTSEND_TOKEN    the customer upload token (if not passed as --token)
  PROJECTSEND_BASE_URL (optional) default https://fs.sharonai.cloud

Examples:
  # Customer uploads with their token:
  PROJECTSEND_TOKEN='7|psend_…' python3 upload_diagnostics.py --file diagnostics.txt

  # Support uploads on a customer's behalf, pulling the token from the registry:
  python3 upload_diagnostics.py --customer acme@example.com --file diagnostics.txt

  # With a descriptive name and machine-readable output:
  python3 upload_diagnostics.py --file diag.txt --name "Acme host123 2026-10-07" --json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from typing import Optional

# Allow running as a plain script from this directory.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import projectsend_client as ps  # noqa: E402

# Where the token registry lives by default. Support uses this to look a
# customer's token up by email instead of asking them to paste it.
DEFAULT_REGISTRY = os.path.join(os.path.expanduser("~"), ".projectsend", "registry.json")


def resolve_token(args: argparse.Namespace) -> Optional[str]:
    """Find the customer token to use, in priority order.

    1. ``--token`` — an explicit token (highest priority, for one-off use).
    2. ``--customer`` — a registry key (email); the token is read from the
       local registry. This is how support uploads on a customer's behalf
       without the customer pasting their secret.
    3. ``PROJECTSEND_TOKEN`` — the environment variable (for the customer's
       own scripted uploads).

    Returns the token string, or ``None`` if no source yielded one.
    """
    if args.token:
        return args.token.strip()
    if args.customer:
        key = args.customer.strip().lower()
        if os.path.isfile(args.registry):
            try:
                with open(args.registry, "r", encoding="utf-8") as handle:
                    registry = json.load(handle)
                entry = registry.get(key)
                if entry and entry.get("plain_text"):
                    return entry["plain_text"]
            except (json.JSONDecodeError, OSError):
                pass
        print(f"error: no token found for customer '{key}' in {args.registry}", file=sys.stderr)
        return None
    return os.environ.get("PROJECTSEND_TOKEN", "").strip() or None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Upload a diagnostics file to ProjectSend with a customer token.",
    )
    parser.add_argument("--file", required=True, help="Path to the diagnostics file to upload")
    parser.add_argument("--name", help="Display name (default: <file-stem> <date>)")
    parser.add_argument("--description", help="Optional description (max 2000 chars)")
    # Note: there is no --folder-id. The folder is fixed by the token (see the
    # module docstring); the server forces the upload into the bound folder.
    parser.add_argument("--token", help="Customer upload token (overrides env/registry)")
    parser.add_argument("--customer", help="Registry key (email) to look the token up")
    parser.add_argument("--registry", default=DEFAULT_REGISTRY, help=f"Registry path (default {DEFAULT_REGISTRY})")
    parser.add_argument("--json", action="store_true", help="Print the response data as JSON")
    return parser


def main(argv: Optional[list] = None) -> int:
    args = build_parser().parse_args(argv)

    token = resolve_token(args)
    if not token:
        print(
            "error: no token provided — pass --token, --customer, "
            "or set PROJECTSEND_TOKEN",
            file=sys.stderr,
        )
        return 2

    if not os.path.isfile(args.file):
        print(f"error: file not found: {args.file}", file=sys.stderr)
        return 2

    # A sensible default name: the file stem plus today's date, so repeated
    # uploads from the same host do not collide in the folder.
    stem = os.path.splitext(os.path.basename(args.file))[0]
    name = args.name or f"{stem} {datetime.now(timezone.utc):%Y-%m-%d}"

    try:
        # No folder_id is passed: the server places the file in the folder the
        # token is bound to.
        data = ps.upload_file(
            token,
            args.file,
            name=name,
            description=args.description,
        )
    except ps.ProjectSendError as exc:
        # Branch on the stable RFC 7807 type slug for an actionable message.
        # These hints are written for the folder-bound, upload-only token.
        hints = {
            "unauthenticated": "the token is missing, invalid, or expired — re-run create_access.py",
            "forbidden": "the token is valid but can no longer upload (owner demoted, or its bound folder was removed)",
            "validation_failed": "the request was invalid — see field errors above",
            "payload_too_large": "the file exceeds the server's upload limit",
            "too_many_requests": "rate limited — wait a moment and retry",
            "not_found": "the endpoint was not found — is the server patched and restarted?",
        }
        hint = hints.get(exc.type)
        print(f"error: upload failed — {exc}", file=sys.stderr)
        if hint:
            print(f"hint: {hint}", file=sys.stderr)
        return 1
    except ps.ProjectSendConnectionError as exc:
        print(f"error: could not reach ProjectSend — {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(data, indent=2, sort_keys=True))
        return 0

    file_id = data.get("id")
    # The token is upload-only, so it cannot produce a working download link;
    # report the identifiers the issuer needs to find the file in the UI.
    print(f'✓ Uploaded "{data.get("name", name)}" ({data.get("size", 0)} bytes)')
    print(f"  ID:       {file_id}")
    print(f"  Slug:     {data.get('slug')}")
    print(f"  Checksum: {data.get('checksum')}")
    print("  Folder:   the folder this token is bound to (set at mint time)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
