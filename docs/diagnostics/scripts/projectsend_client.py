"""Minimal stdlib-only client for the ProjectSend v1 REST API.

Zero dependencies (Python 3.8+ standard library only).

The system this client talks to
================================
ProjectSend hands out two kinds of API token, and this client models both:

* **Issuer token** — a long-lived credential held by the diagnostics
  pipeline (a staff account whose role grants ``create_api_tokens``). It is
  the *only* credential that may mint other tokens. It is never given to a
  customer.

* **Customer token** — a short-lived, *scoped* credential minted from an
  issuer token and handed to a customer. Since the folder-bound redesign a
  customer token has exactly two properties, both fixed at mint time:

  1. **Upload-only.** It carries the ``upload_only`` ability. That ability is
     accepted by the upload endpoints *alone* — the list / show / download /
     comment routes require ``upload`` or an ``edit_*`` ability, which the
     token does not have. So a customer can push files in but cannot read,
     list, or download anything from the library.

  2. **Folder-bound.** It stores a ``folder_id``. The upload endpoint
     *forces* every file it stores into that folder and ignores any
     ``folder_id`` the caller sends at upload time. A customer therefore
     cannot steer a file anywhere other than its assigned folder.

The two operations this module exposes map directly onto that model:

* :func:`mint_token`  — an issuer mints a folder-bound, upload-only customer
  token (``POST /api/v1/tokens`` with ``abilities=["upload_only"]`` and a
  ``folder_id``).
* :func:`upload_file` — a customer uploads a file with their token
  (``POST /api/v1/files``). Note there is **no** ``folder_id`` argument: the
  destination is whatever folder the token was bound to.

Design notes
------------
* Errors raise :class:`ProjectSendError` carrying the RFC 7807 ``type`` slug
  (``unauthenticated``, ``forbidden``, ``validation_failed``,
  ``payload_too_large``, ``too_many_requests``, ...) so callers can branch on
  stable, machine-readable codes instead of scraping status codes.
* The base URL and tokens are read from the environment by default
  (``PROJECTSEND_BASE_URL``) but every function also accepts explicit
  arguments, so this module drops cleanly into an existing script.
* The plaintext token is returned **once** by :func:`mint_token` and never
  stored server-side (the database keeps a SHA-256 hash). Persist it yourself
  (``create_access.py`` writes it to a local registry).
"""

from __future__ import annotations

import json
import mimetypes
import os
import urllib.error
import urllib.request
import uuid
from typing import Any, Dict, Iterable, List, Optional, Tuple

# The public install. Override with PROJECTSEND_BASE_URL for a different host.
DEFAULT_BASE_URL = "https://fs.sharonai.cloud"
# Diagnostics bundles can be large; give the socket a generous ceiling.
DEFAULT_TIMEOUT = 120  # seconds

# The ability a customer token carries. It is deliberately *not* "upload":
# "upload" also unlocks the read routes, whereas "upload_only" unlocks only
# the upload routes. See the module docstring for the full rationale.
CUSTOMER_TOKEN_ABILITIES = ("upload_only",)


class ProjectSendError(Exception):
    """A non-2xx response from the ProjectSend API.

    The API answers errors with an RFC 7807 problem document::

        {
          "type": "forbidden",
          "title": "You are not allowed to do that.",
          "detail": "...",
          "errors": {"folder_id": ["..."]}   # only on validation_failed
        }

    This exception mirrors that document so callers can branch on ``.type``
    (a stable slug) rather than parsing ``.detail`` prose. ``.errors`` is the
    per-field map on ``validation_failed`` and ``None`` otherwise.
    """

    def __init__(
        self,
        status: int,
        type_: str,
        title: str,
        detail: Optional[str] = None,
        errors: Optional[Dict[str, List[str]]] = None,
        body: Optional[str] = None,
    ) -> None:
        self.status = status
        self.type = type_
        self.title = title
        self.detail = detail
        self.errors = errors
        self.body = body
        super().__init__(self._message())

    def _message(self) -> str:
        base = f"[{self.status}] {self.title} (type={self.type})"
        if self.detail:
            base += f" — {self.detail}"
        if self.errors:
            fields = "; ".join(
                f"{k}: {', '.join(v)}" for k, v in self.errors.items()
            )
            base += f" — {fields}"
        return base


class ProjectSendConnectionError(Exception):
    """A network-level failure (DNS, connect, timeout) — not an API error.

    Distinguished from :class:`ProjectSendError` so a caller can tell "the
    server said no" apart from "we never reached the server" and retry the
    latter without treating it as an auth/validation problem.
    """


def base_url() -> str:
    """The API base URL from the environment, or the public default.

    Trailing slashes are stripped so callers can concatenate ``/api/v1/...``
    without producing a double slash.
    """
    return os.environ.get("PROJECTSEND_BASE_URL", DEFAULT_BASE_URL).rstrip("/")


# ---------------------------------------------------------------------------
# Low-level request plumbing
# ---------------------------------------------------------------------------

def _send(
    method: str,
    path: str,
    token: Optional[str],
    body: Optional[bytes],
    content_type: Optional[str],
    url_base: Optional[str] = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> Dict[str, Any]:
    """Send one request and return the parsed JSON body.

    This is the single choke point every public operation funnels through, so
    the auth header, error mapping, and JSON decoding live here exactly once.

    * ``token`` is sent as ``Authorization: Bearer <token>``. Pass ``None``
      only for the (nonexistent-here) unauthenticated routes.
    * Any 2xx status returns the parsed body.
    * Any non-2xx status raises :class:`ProjectSendError` with the RFC 7807
      fields parsed out of the problem document.
    * A network-level failure raises :class:`ProjectSendConnectionError`.
    """
    url = f"{url_base or base_url()}{path}"
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if content_type:
        headers["Content-Type"] = content_type

    request = urllib.request.Request(
        url, data=body, headers=headers, method=method
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            status = response.status
    except urllib.error.HTTPError as exc:  # non-2xx: the API answered
        raw = exc.read()
        status = exc.code
    except urllib.error.URLError as exc:  # DNS / connect / timeout
        raise ProjectSendConnectionError(str(exc.reason)) from exc

    text = raw.decode("utf-8", "replace") if raw else ""
    data: Dict[str, Any]
    if text:
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            data = {}
    else:
        data = {}

    if 200 <= status < 300:
        return data

    # Non-2xx: parse the RFC 7807 problem document into the exception.
    raise ProjectSendError(
        status=status,
        type_=str(data.get("type", "unknown")),
        title=str(data.get("title", "Request failed")),
        detail=data.get("detail"),
        errors=data.get("errors") if isinstance(data.get("errors"), dict) else None,
        body=text,
    )


def _encode_multipart(
    fields: Dict[str, str],
    files: List[Tuple[str, str, bytes, str]],
) -> Tuple[bytes, str]:
    """Build a ``multipart/form-data`` body.

    ``fields`` are plain form values (e.g. ``name``, ``description``);
    ``files`` is a list of ``(field_name, filename, content, content_type)``.
    Returns ``(body_bytes, content_type_header)`` ready to hand to
    :func:`_send`.

    The boundary is a fresh UUID per call so concurrent uploads never collide.
    """
    boundary = uuid.uuid4().hex
    parts: List[bytes] = []
    for name, value in fields.items():
        parts.append(f"--{boundary}".encode())
        parts.append(f'Content-Disposition: form-data; name="{name}"'.encode())
        parts.append(b"")
        parts.append(str(value).encode("utf-8"))
    for name, filename, content, content_type in files:
        parts.append(f"--{boundary}".encode())
        parts.append(
            f'Content-Disposition: form-data; name="{name}"; '
            f'filename="{filename}"'.encode()
        )
        parts.append(f"Content-Type: {content_type}".encode())
        parts.append(b"")
        parts.append(content)
    parts.append(f"--{boundary}--".encode())
    parts.append(b"")
    body = b"\r\n".join(parts)
    return body, f"multipart/form-data; boundary={boundary}"


# ---------------------------------------------------------------------------
# Public operations
# ---------------------------------------------------------------------------

def mint_token(
    token: str,
    name: str,
    folder_id: int,
    abilities: Iterable[str] = CUSTOMER_TOKEN_ABILITIES,
    expires_in_days: Optional[int] = 7,
    never_expires: bool = False,
    url_base: Optional[str] = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> Dict[str, Any]:
    """Mint a folder-bound, upload-only customer token from an issuer token.

    Args:
        token: The **issuer** token — a credential granted
            ``create_api_tokens``. This is the only credential that may mint.
            It is never handed to a customer.
        name: A human-readable name for the token. Name it after the customer
            (e.g. ``acme-corp-2026-10-07``) so the issuer can identify and
            revoke it later.
        folder_id: The id of the folder this token may upload into. The token
            is *bound* to it: every upload it makes is forced into this folder
            and it cannot write anywhere else. The folder must be one the
            issuer's account may put content into, or the API rejects the mint
            with ``validation_failed``.
        abilities: What the token may do. Defaults to ``("upload_only",)`` —
            the customer token shape. You almost never change this; a customer
            token that carries ``upload`` (instead of ``upload_only``) could
            also read the library, which is the whole point of avoiding it.
        expires_in_days: Token lifetime in days. Ignored when
            ``never_expires`` is true. The server caps this at its configured
            maximum (``api.tokens.max_days``).
        never_expires: Mint a token that stays valid until revoked by hand.
            Prefer a finite lifetime for customer tokens.
        url_base: Override the base URL for this call.
        timeout: Socket timeout in seconds.

    Returns:
        The ``data`` object from the 201 response. The keys you care about:

        * ``plain_text`` — the token secret, shown **once**. Persist it now;
          the server stores only a hash and it cannot be recovered.
        * ``abilities`` — the abilities the token was granted.
        * ``folder_id`` — the folder the token is bound to (echoed back).
        * ``expires_at`` / ``created_at`` — ISO-8601 timestamps.

    Raises:
        ProjectSendError: On any non-2xx. ``validation_failed`` here usually
            means the ``folder_id`` is not one the issuer may write to, or an
            ability was not grantable (the API strips ``create_api_tokens`` so
            a minted token can never mint further tokens — no chaining).
        ProjectSendConnectionError: On a network-level failure.
    """
    # The JSON body. ``folder_id`` is what makes this a customer token rather
    # than an ordinary personal token; omit it (pass None) only if you truly
    # want an unbound token, which the diagnostics flow never does.
    payload: Dict[str, Any] = {
        "name": name,
        "abilities": list(abilities),
    }
    if folder_id is not None:
        payload["folder_id"] = folder_id
    if never_expires:
        payload["never_expires"] = True
    else:
        payload["expires_in_days"] = expires_in_days

    body = json.dumps(payload).encode("utf-8")
    data = _send(
        "POST",
        "/api/v1/tokens",
        token,
        body,
        "application/json",
        url_base=url_base,
        timeout=timeout,
    )
    return data.get("data", data)


def upload_file(
    token: str,
    file_path: str,
    name: Optional[str] = None,
    description: Optional[str] = None,
    url_base: Optional[str] = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> Dict[str, Any]:
    """Upload a file with a customer token.

    Args:
        token: The customer's token (the ``plain_text`` from
            :func:`mint_token`). It must carry ``upload_only`` and be bound to
            a folder.
        file_path: Path to the file to upload. It is read in full, so keep it
            within the server's upload limit (the API returns
            ``payload_too_large`` otherwise).
        name: Optional display name for the stored file. Defaults to the
            file's name on the server side.
        description: Optional description (max 2000 chars).

    There is deliberately **no** ``folder_id`` argument. The destination folder
    is fixed by the token: the server forces the upload into the folder the
    token was bound to and ignores any folder the caller might try to specify.
    That is the security property — a customer cannot choose where their file
    lands.

    Returns:
        The ``data`` object from the 201 response: ``id``, ``slug``,
        ``checksum`` (SHA-256), ``size``, ``name``, ...

    Raises:
        FileNotFoundError: If ``file_path`` does not exist.
        ProjectSendError: On any non-2xx. ``unauthenticated`` means the token
            is missing/invalid/expired; ``forbidden`` means the token is valid
            but the owner lost the permission (or the bound folder was removed);
            ``payload_too_large`` means the file exceeds the server limit.
        ProjectSendConnectionError: On a network-level failure.
    """
    if not os.path.isfile(file_path):
        raise FileNotFoundError(f"file not found: {file_path}")

    with open(file_path, "rb") as handle:
        content = handle.read()

    filename = os.path.basename(file_path)
    # Guess the MIME type from the extension; the server re-detects it from the
    # bytes anyway, so this is only a hint for the multipart header.
    content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"

    fields: Dict[str, str] = {}
    if name is not None:
        fields["name"] = name
    if description is not None:
        fields["description"] = description

    body, content_type_header = _encode_multipart(
        fields, [("file", filename, content, content_type)]
    )
    data = _send(
        "POST",
        "/api/v1/files",
        token,
        body,
        content_type_header,
        url_base=url_base,
        timeout=timeout,
    )
    return data.get("data", data)
