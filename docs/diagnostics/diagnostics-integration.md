# ProjectSend — Diagnostics Integration (Customer Upload Tokens)

**Last updated:** 2026-10-07
**Target:** https://fs.sharonai.cloud (ProjectSend, forked)
**Status:** Implemented, deployed, merged to the fork's `main`, tested end-to-end.

---

## Summary (read this first)

**The problem.** Our diagnostics flow produces files (logs, reports, tarballs) that need to be
sent into a ProjectSend library. But the person running the upload — often on-site, often a
customer — should **not** be able to read the rest of the library. They should only be able to
push their file in. ProjectSend's stock API tokens are all-or-nothing per ability: the `upload`
ability also unlocks the read routes (list, view, download, comment), so a stock "upload" token
lets the holder browse the whole library. There was no way to hand someone a token that could
*only* upload, and *only* into one specific folder.

**What we built.** A small, self-contained set of changes to a **fork** of ProjectSend that adds:

1. A new, narrower permission — **`upload_only`** — accepted by the upload endpoints *alone*.
2. A **folder binding** on tokens — a `folder_id` column the upload path enforces.
3. A **web GUI** ("Customer tokens", in the API menu) so an issuer can mint and revoke these
   tokens from the browser.
4. A pair of **zero-dependency Python scripts** that demonstrate the full flow (mint on the
   issuer side, upload on the customer side).

Together these let an **issuer** (a staff account) mint a **customer token** that:

- can **only upload** — no list, view, download, or comment; and
- can **only write into one folder** — the one chosen at mint time. The server forces every
  upload into that folder and ignores any other folder the caller tries to specify.

**How the security model works.**

- **Upload-only.** The new `upload_only` permission is accepted by the upload routes
  (`POST /api/v1/files`, the chunked-upload routes) and by *no* read route. The read routes
  require `upload` or an `edit_*` ability, which a customer token does not carry. So a customer
  token can push files in but cannot read, list, or download anything.
- **Folder-bound.** A token's `folder_id` is fixed at mint time. The API upload path
  (`FilesController::store`) forces every file into the bound folder and ignores any `folder_id`
  the caller sends. If the bound folder has been deleted, the upload **fails closed** (it does
  not fall back to root or to the caller's choice).
- **No chaining.** A minted token can never mint further tokens: the API strips
  `create_api_tokens` from the set of grantable abilities (`TokenAbilities::grantableFor`), so a
  customer token cannot escalate.
- **Re-proven.** Minting and revoking in the GUI sit behind `password.confirm`, so a stale
  issuer session cannot be used to mint tokens without re-entering the password.
- **Live revocation.** Tokens are checked against the owning account on every request; if the
  issuer is demoted or the token is revoked, it stops working immediately.

**How to use it.**

- **GUI:** Sign in as an issuer → **API** menu → **Customer tokens** → create a token
  (name + folder + expiry). The token is shown **once**; hand it to the customer.
- **Scripts:** `create_access.py` (issuer side — mints the token) and `upload_diagnostics.py`
  (customer side — uploads the file). See [The scripts folder](#the-scripts-folder-sample-integration).

**How it's maintained.** The changes live in a fork (`clintonsharonai/projectsend`) on a feature
branch (`customer-tokens-gui`), now merged into the fork's `main`. When upstream ProjectSend
ships a new version, we merge upstream into our branch, resolve the small, well-understood
conflicts, re-run the update, and re-deploy. See
[Maintaining through an upstream update](#maintaining-through-an-upstream-update).

**Where things live.**

| Thing | Location |
|---|---|
| Fork (source of truth) | `https://github.com/clintonsharonai/projectsend` |
| This document | `docs/diagnostics/diagnostics-integration.md` in the fork |
| Sample scripts | `docs/diagnostics/scripts/` in the fork |
| Server clone | `/root/projectsend-src` on `fs.sharonai.cloud` |
| Deploy key | `/root/.ssh/projectsend-repo` (on the server) |
| Live app | Docker container `projectsend-app-1`, app at `/var/www/html` |
| Working copy | `scratch/projectsend/` (this OneDrive folder — mirrors the fork's `docs/diagnostics/`) |
| Credentials | `scratch/projectsend/details` (SSH + fork + deploy key) |

---

## How the branch is forked

We do **not** patch the live container by hand. The live code is always built from the fork, so
every change is reviewable, diffable, and re-deployable.

**The fork.** `clintonsharonai/projectsend` is a GitHub fork of `projectsend/projectsend`. The
server holds a deploy key (`/root/.ssh/projectsend-repo`) that can push to the fork, and a clone
of the fork at `/root/projectsend-src`.

**The branches.**

| Branch | Purpose | Head (as of 2026-10-07) |
|---|---|---|
| `main` | Upstream + our feature, merged. What the live site runs. | `a913c41` (merge commit) |
| `customer-tokens-gui` | The feature branch. Where the work was done. | `001f54f` |

The feature branch was cut from upstream commit `b8050b3` (ProjectSend 2.4.1). It carries four
commits, in order:

| Commit | Message |
|---|---|
| `f18d2b5` | Add customer tokens GUI for issuers |
| `3f25c34` | Move Customer tokens nav entry to the API menu |
| `356284e` | Bind customer tokens to a folder and make them upload-only |
| `001f54f` | Allow folder_id on the API token-mint endpoint |

`main` was then fast-forwarded to a merge of `customer-tokens-gui` (commit `a913c41`,
"Merge customer-tokens-gui into main"). The merge was clean (0 conflicts) because the files both
sides touched auto-merged; this is verified each time — see the maintenance section.

**Pushing to the fork** (from the server, using the deploy key):

```bash
cd /root/projectsend-src
GIT_SSH_COMMAND="ssh -i /root/.ssh/projectsend-repo -o StrictHostKeyChecking=no -o IdentitiesOnly=yes" \
  git push origin customer-tokens-gui   # or: git push origin main
```

**Why a fork + branch (not a patch file).** The feature touches ~12 files across the API, the
identity module, the routes, and the frontend. A patch file would be fragile across upstream
releases; a branch merges cleanly and the diff (`git diff b8050b3..customer-tokens-gui`) is the
single source of truth for "what is ours".

---

## What was changed

The full diff is `git diff b8050b3..customer-tokens-gui` (12 files, ~790 insertions). File by file:

### Backend

| File | Change |
|---|---|
| `database/migrations/2026_10_06_000000_add_folder_id_to_personal_access_tokens_table.php` | **New.** Adds `folder_id` (`unsignedBigInteger`, nullable, indexed) to `personal_access_tokens`, after `abilities`. Reversible (`down()` drops the index + column). |
| `app/Modules/Identity/Permissions/Permission.php` | Adds the `UploadOnly = 'upload_only'` case, its label ("Upload files (dedicated token)"), and places it in the Files permission category. |
| `routes/api.php` | The upload routes (`POST files`, the chunked-upload routes) now use `token-can:upload,upload_only` — i.e. a token with *either* `upload` or `upload_only` may upload. The read routes are unchanged (they still require `upload` / `edit_*`). |
| `app/Modules/Files/Http/Controllers/Api/FilesController.php` | `store()` now checks `$user->currentAccessToken()?->folder_id`. If the token is folder-bound, the request's `folder_id` is **ignored** and the bound folder is used; if the bound folder no longer exists the upload fails closed. Unbound tokens behave exactly as before. |
| `app/Modules/Api/Http/Controllers/TokensController.php` | The API mint endpoint (`POST /api/v1/tokens`) accepts an optional `folder_id` (validated to exist and be uploadable by the issuer), stores it on the token, and echoes it back in the response. This is what the scripts use. |
| `app/Modules/Api/Auth/TokenAbilities.php` | `grantableFor()` / `grantableCasesFor()` = `availableFor` minus `create_api_tokens`. This is the **no-chaining ceiling**: a minted token can never be granted the ability to mint further tokens. |
| `app/Modules/Identity/Http/Controllers/CustomerTokensController.php` | **New.** The GUI backend. `create()` passes the issuer's uploadable folders (via `StaffLibraryScope::folders`) to the form; `store()` validates `folder_id`, mints a token with `abilities=[upload_only]` and the bound `folder_id`, and returns the plain token once; `index()` lists the issuer's tokens with their folder names; `destroy()` revokes. Minting/revoking are behind `password.confirm`. |
| `app/Modules/Identity/Http/Controllers/ApiTokensController.php` | The existing "API tokens" GUI page made folder-aware (shows the bound folder per token). |

### Frontend

| File | Change |
|---|---|
| `resources/js/pages/settings/customer-tokens/index.tsx` | **New.** The token list. Uses `AppLayout` (the normal app shell — **not** the settings sidebar), matching the other API-menu pages. Shows each token's name, folder, abilities, and expiry. |
| `resources/js/pages/settings/customer-tokens/create.tsx` | **New.** The mint form: a **folder picker** (a `Select` of the issuer's uploadable folders), a name, and an expiry. No ability checkboxes — the token is always `upload_only`. |
| `resources/js/components/app-sidebar.tsx` | Adds the "Customer tokens" nav entry under the **API** menu. |

### Routes

| File | Change |
|---|---|
| `routes/settings.php` | Adds the `settings/customer-tokens` (index) and `settings/customer-tokens/create` routes, gated to users with `create_api_tokens`. |

### Data (not a file — seeded in the DB)

- **Role 5 — "Diagnostics Issuer."** A staff role with exactly three permissions:
  `create_api_tokens`, `upload`, `upload_only`. This is the role an issuer account is given.
  (It lives in the `roles` / `role_permission` tables, not in a seeder — create it via the
  Roles UI or a tinker snippet if rebuilding a fresh install.)
- **Issuer account.** A staff user (e.g. `diagnostics-bot@sharonai.local`) assigned Role 5.

**Net effect on a stock install:** nothing changes for ordinary users or ordinary tokens. The
only new behaviour is (a) a new permission that only the upload routes honour, and (b) an
optional folder binding that only kicks in when a token actually has a `folder_id`.

---

## Finding folder IDs

A customer token is bound to a folder by its **numeric ID** — `--folder-id` in the scripts, the
selected value in the GUI picker. The GUI shows folder *names*, not IDs, so here is how to find
the ID for a folder.

**1. The web UI URL (easiest).** Open the folder in the ProjectSend interface — the ID is in the
URL: `https://fs.sharonai.cloud/folders/1` is folder `1`. The number after `/folders/` is the ID.

**2. The API — `GET /api/v1/folders`.**

```bash
curl -s -H "Authorization: Bearer $TOKEN" -H 'Accept: application/json' \
  https://fs.sharonai.cloud/api/v1/folders | jq -r '.data[] | "\(.id)  \(.name)"'
```

The token needs the `upload` ability (or `edit_files` / `edit_others_files`). An `upload_only`
token — and the `create_api_tokens`-only issuer token — **cannot** list folders. Use a token with
`upload`, or run this while logged in as the issuer (their role has `upload`).

**3. The customer-tokens create page.** The folder picker lists folders by name; the ID is the
underlying value (visible in page source / browser dev tools as the selected option's value).

**4. The database (server access).**

```bash
docker exec projectsend-app-1 sh -c "cd /var/www/html && php artisan tinker --execute=\
\"foreach(App\\\\Modules\\\\Files\\\\Models\\\\Folder::orderBy('id')->get() as \\\$f){echo \\\$f->id.'  '.\\\$f->name.PHP_EOL;}\""
```

**Current folders (as of 2026-10-07):**

| ID | Name |
|---|---|
| 1 | Diagnostics Uploads |
| 4 | Customer-GMI |
| 5 | Customer-Canva |
| 6 | Customer-FH |

---

## Maintaining through an upstream update

This is the recurring cost of forking, and it is small because our footprint is small and
well-isolated. The steps below are what was actually done to move the fork from 2.4.1 to 2.6.0.

### The shape of an update

1. **Upstream moves.** `projectsend/projectsend` ships a new release (new migrations, new
   features, refactors). Our fork's `main` is behind.
2. **We merge upstream in.** Bring upstream's new `main` into our fork, on top of our feature.
3. **Resolve conflicts.** Our feature touches a handful of files that upstream also touches.
   The recurring conflict surface is:
   - `app/Modules/Files/Http/Controllers/Api/FilesController.php` (we patch `store()`)
   - `app/Modules/Identity/Permissions/Permission.php` (we add a case)
   - `routes/api.php` (we change the upload middleware)
   - `resources/js/components/app-sidebar.tsx` (we add a nav entry)
   - `routes/settings.php` (we add routes)
   Most of these are additive (a new case, a new route, a new nav item) and auto-merge; the one to
   read carefully is `FilesController::store()`, because upstream may refactor the upload path.
4. **Re-deploy and update.** See the deployment steps below.
5. **Re-test end to end.** See the testing section.

### Concrete steps (as performed for 2.4.1 → 2.6.0)

```bash
# On the server, in the fork clone:
cd /root/projectsend-src

# 1. Fetch upstream (add it as a remote once if you haven't):
git remote add upstream https://github.com/projectsend/projectsend.git 2>/dev/null || true
git fetch upstream

# 2. Merge upstream's main into our feature branch (or into main directly):
git checkout customer-tokens-gui
git merge upstream/main            # resolve conflicts if any

# 3. Sanity-check the merge kept our feature:
git grep -n "upload_only" app/Modules/Identity/Permissions/Permission.php
git grep -n "folder_id"  app/Modules/Files/Http/Controllers/Api/FilesController.php
git grep -n "upload_only" routes/api.php

# 4. Lint the changed PHP:
for f in $(git diff --name-only upstream/main -- '*.php'); do php -l "$f"; done

# 5. Push the branch, then merge into main and push main:
GIT_SSH_COMMAND="ssh -i /root/.ssh/projectsend-repo -o StrictHostKeyChecking=no -o IdentitiesOnly=yes" \
  git push origin customer-tokens-gui
git checkout main && git merge customer-tokens-gui
GIT_SSH_COMMAND="ssh -i /root/.ssh/projectsend-repo -o StrictHostKeyChecking=no -o IdentitiesOnly=yes" \
  git push origin main
```

### Deploying the merged code to the live container

The container runs the code from `/var/www/html`. To deploy a new build:

```bash
# 1. Back up the .env (it is a symlink to storage/.env — do not overwrite it):
docker exec projectsend-app-1 sh -lc "cp /var/www/html/.env /tmp/env-backup-$(date +%s)"

# 2. Package the new code (exclude runtime state) and ship it in:
cd /root/projectsend-src
tar czf /root/ps-main.tar.gz --exclude=.git --exclude=vendor --exclude=node_modules \
  --exclude=public/build --exclude=storage --exclude=.env .
docker cp /root/ps-main.tar.gz projectsend-app-1:/tmp/
docker exec projectsend-app-1 sh -lc "cd /var/www/html && tar xzf /tmp/ps-main.tar.gz"

# 3. Rebuild the frontend (Node is NOT in the container — build on a host with Node 24,
#    then ship public/build in the same way). See "Building the frontend" below.

# 4. Run the update. This runs any new migrations, syncs system roles, clears/rebuilds
#    caches, and — critically — records the applied version so the "update has not been run"
#    banner does not appear:
docker exec projectsend-app-1 sh -lc "cd /var/www/html && php artisan projectsend:update"

# 5. Restart PHP-FPM (the image ships opcache.validate_timestamps=Off, so a running
#    process keeps serving old code until it is restarted):
docker exec projectsend-app-1 sh -lc "pkill -TERM -f '[p]hp-fpm: master'"
```

> **The version banner.** ProjectSend compares the code version (`config/projectsend.version`)
> against a `applied_version` setting in the DB. If they differ, signed-in users with
> `view_system_info` see "Version X is installed, but the update has not been run." Running
> `php artisan projectsend:update` records the applied version and clears it. (Recreating the
> container with `docker compose up -d --force-recreate` triggers the same update via the
> entrypoint; running the command directly is the equivalent and is what we use.)

### Building the frontend

The container has no Node. The frontend is built on a host with **Node 24** and shipped in:

```bash
# On the build host (e.g. a Mac), in a checkout of the fork at the target commit:
npm install          # or: npm ci
npm run build        # emits public/build
# Ship public/build into the container the same way as the code tar:
tar czf build.tar.gz -C public build
docker cp build.tar.gz projectsend-app-1:/tmp/
docker exec projectsend-app-1 sh -lc "rm -rf /var/www/html/public/build && tar xzf /tmp/build.tar.gz -C /var/www/html/public"
```

Watch for **new frontend dependencies** in upstream releases (e.g. 2.6.0 added
`react-image-crop` for the logo-crop feature) — `npm install` picks them up, but a stale
`node_modules` will fail the build with "Rollup failed to resolve import …".

---

## Testing the feature end to end

The test is a shell script that runs **inside the container** (so it talks to the app over
`127.0.0.1:80`, the same way a real client would). It exercises the whole flow and asserts the
security properties. The version used for the 2.6.0 deploy is `ct_main_final.sh`.

**What it checks:**

| # | Step | Expect |
|---|---|---|
| 0 | Create a throwaway issuer user (Role 5) | user created |
| 1 | Log in as the issuer | `302 → /dashboard` |
| 2 | `GET /settings/customer-tokens` | `200`, **no** settings sidebar |
| 3 | `GET /settings/customer-tokens/create` | `200`, folder picker shows the target folder |
| 4 | `POST /confirm-password` | `302` |
| 5 | `POST /settings/customer-tokens` (mint, `folder_id=1`) | `302 → index` |
| 6 | Read the plain token from the index flash | `psend_…` (57 chars) |
| 7 | `POST /api/v1/files` with the token (a `.txt`) | `201`, file lands in **folder 1** |
| 8 | `GET /api/v1/files` and `GET /api/v1/files/{id}` with the token | **`403`** (upload-only: no read) |
| 9 | `GET /dashboard`, `GET /settings/api-tokens` | `200` (upstream features intact) |
| 10 | Delete the uploaded file | `0` leftover files |

**How to run it:**

```bash
# Copy the script into the container and run it with sh (the image has no bash):
docker cp ct_main_final.sh projectsend-app-1:/tmp/
docker exec projectsend-app-1 sh /tmp/ct_main_final.sh
```

**Notes on the test:**

- It creates its own issuer user (Role 5, password `TestPass123!`) and deletes it at the end, so
  it is safe to run repeatedly and leaves no trace.
- The upload uses a **`.txt`** file. A `.bin` (or other non-allowlisted) extension is rejected
  with `422 validation_failed` ("This file type is not allowed for upload") — that is the
  install's file-type policy, not a token problem.
- The plain token is extracted from the index flash (`plain_text&quot;:&quot;…`). The token format
  is `psend_…`; a `12|`-style prefix in the flash is a different field and must be stripped.
- The XSRF cookie is `projectsend_xsrf`; extract it from the cookie jar (it is **not**
  `HttpOnly`, so its jar line has no `#HttpOnly_` prefix — take the last tab-field).

**Result of the 2.6.0 deploy test:** all steps passed — index/create 200 with no sidebar and the
folder picker present, mint 302, upload 201 into folder 1, list/download 403, dashboard and
api-tokens 200, 0 leftover files.

---

## The scripts folder (sample integration)

The scripts live in the fork at **`docs/diagnostics/scripts/`** (mirrored in this OneDrive
folder's `scripts/`). They are a **reference integration** — a minimal, dependency-free
demonstration of the whole flow, written so it can be dropped into (or read alongside) a real
diagnostics script. They are not part of the ProjectSend app itself; they talk to the live API
over HTTPS.

Three files, all **Python 3.8+ stdlib only** (no `requests`, no `pip install`):

| File | Role | Who runs it |
|---|---|---|
| `projectsend_client.py` | The shared client library. Wraps the two API calls (`mint_token`, `upload_file`), builds the multipart body, and maps RFC 7807 error documents onto exceptions. | both |
| `create_access.py` | **Issuer side.** Mints (or reuses) a folder-bound, upload-only token for a customer and records it in a local registry. | support / the diagnostics pipeline |
| `upload_diagnostics.py` | **Customer side.** Uploads a file with a customer token. | the customer / on-site runner |

**Bash ports.** `create_access.sh` and `upload_diagnostics.sh` do the same two jobs in
bash, for hosts where Python is inconvenient. They require `curl` + `jq` (no Python), use the
**same registry format** (`~/.projectsend/registry.json`), so the Python and bash versions are
interchangeable — a token minted by one can be used by the other. Same flags, same idempotent
reuse, same error hints. (Verified end-to-end against the live API: mint → reuse → upload into
the bound folder → upload-only 403s on read.)

### The two credentials

The scripts model the two-token design exactly:

- **Issuer token** (`PROJECTSEND_ISSUER_TOKEN`) — a long-lived credential for a staff account
  whose role grants `create_api_tokens`. It is the *only* credential that may mint. It is never
  given to a customer.
- **Customer token** (`PROJECTSEND_TOKEN`) — the short-lived, `upload_only`, folder-bound token
  minted from the issuer token and handed to the customer.

### `create_access.py` (mint)

```bash
PROJECTSEND_ISSUER_TOKEN='1|psend_…' \
  python3 create_access.py --customer "Acme Corp" \
    --email acme@example.com --folder-id 1 [--days 7] [--force] [--json]
```

- `--folder-id` is **required** — a customer token without a folder is not a customer token.
- **Idempotent:** if a still-valid token for the *same* customer *and* the same folder exists in
  the registry, it is reused (not re-minted), unless `--force`. The folder must match because a
  token is bound to exactly one folder.
- The minted token is written to a local **registry** (`~/.projectsend/registry.json` by
  default), keyed by customer email (or normalized name). This lets support look a customer's
  token up without re-minting (which would orphan the old one). The registry is a convenience
  cache — the server is the source of truth.
- Output: the plain token (shown once), the folder, the expiry, and a ready-to-paste
  `upload_diagnostics.py` command.

### `upload_diagnostics.py` (upload)

```bash
# Customer uploads with their token:
PROJECTSEND_TOKEN='7|psend_…' python3 upload_diagnostics.py --file diagnostics.txt

# Support uploads on a customer's behalf (token pulled from the registry):
python3 upload_diagnostics.py --customer acme@example.com --file diagnostics.txt
```

- **No `--folder-id` on purpose.** The destination folder is fixed by the token; the server
  forces the upload into the bound folder. The customer cannot steer the file anywhere else —
  that is the security property.
- Token resolution (first match wins): `--token` → `--customer` (registry lookup) →
  `PROJECTSEND_TOKEN` env var.
- The token is upload-only, so the script only ever uploads; it does not (and cannot) fetch
  files back. It reports the file `id` / `slug` / `checksum` so the issuer can find it in the UI.
- Errors branch on the stable RFC 7807 `type` slug (`unauthenticated`, `forbidden`,
  `validation_failed`, `payload_too_large`, `too_many_requests`, `not_found`) with an actionable
  hint for each.

### The client library (`projectsend_client.py`)

- `mint_token(issuer, name, folder_id, abilities=("upload_only",), expires_in_days=7)` →
  `POST /api/v1/tokens`. Returns the `data` object including `plain_text` (shown once),
  `abilities`, `folder_id`, `expires_at`.
- `upload_file(token, file_path, name=None, description=None)` → `POST /api/v1/files`. **No
  `folder_id` argument** — the destination is the token's bound folder.
- `ProjectSendError` carries the RFC 7807 `type` slug and per-field `errors`;
  `ProjectSendConnectionError` is a network-level failure (distinct from an API "no").
- Base URL from `PROJECTSEND_BASE_URL` (default `https://fs.sharonai.cloud`).

### Why this is a good sample

- **Zero dependencies** — runs anywhere Python 3.8+ does, including a locked-down on-site
  machine with no `pip`.
- **Mirrors the security model** — the scripts *cannot* do the wrong thing: the mint is always
  `upload_only` + a folder, and the upload has no folder argument.
- **Idempotent and auditable** — the registry prevents orphaned tokens and gives support a
  lookup; the token name is `<customer>-<date>` so it is revocable by name.

---

## Deployment & operations notes

- **Container:** `projectsend-app-1`; app at `/var/www/html`; **internal port 80**; nginx +
  PHP-FPM under supervisord.
- **OPcache:** the image ships `opcache.validate_timestamps=Off`, so after any PHP change you
  **must** restart the PHP-FPM master (`pkill -TERM -f '[p]hp-fpm: master'`) or the running
  process keeps serving the old code.
- **`.env`:** a symlink to `storage/.env`. Never overwrite it when shipping code; back it up
  first.
- **The update command:** `php artisan projectsend:update` is idempotent and is the single
  definition of "an update" (migrations + system roles + caches + applied-version record). Run it
  after every deploy.
- **Rate limits** are per token; a diagnostics integration cannot starve anything else.
- **`POST /files` is not idempotent** — a retried POST that already succeeded creates a second
  file. The scripts' registry is the guard against accidental double-mints.

---

## Appendix — Original investigation (2026-10-06)

The research that preceded the implementation. The conclusion (a short-lived, upload-only token)
is what the fork implements; the folder binding and the GUI are the additions made during
implementation.

**Bottom line (as first investigated):**

| Access model considered | Supported? | Notes |
|---|---|---|
| Anonymous send | **No** | API is Bearer-token only. No anonymous upload endpoint, no password login, no public dropbox. |
| IP whitelist | **Not natively** | No per-token IP allowlist. Only doable at the reverse proxy/firewall level. |
| **Temporary code / access** | **Yes — the designed mechanism** | Scoped, expiring API tokens. |

**What the service is (verified live):**

- `fs.sharonai.cloud` runs ProjectSend (Laravel + Inertia).
- REST API v1 is live at `/api/v1`; `GET /api/v1/openapi.json` is public by design.
- `GET /api/v1/me` without a token → `401` with an RFC 7807 problem document.
- Upload endpoints: `POST /files` (single-request multipart) and the resumable chunked flow
  (`POST /uploads` → signed part URLs → `POST /uploads/{id}/complete`).

**How stock tokens work:** created in Settings → API tokens by a staff account; abilities ticked
per token; expiry by default (90 days default, max 365); token shown once (only a hash stored);
live permission checks (a demoted owner loses abilities immediately); per-token rate limits; a
token can revoke itself (`DELETE /api/v1/tokens/current`); v1 is staff-only (no client tokens, no
password login on the API).

**Error handling (branch on the stable `type` slug, not prose):**

| Status | `type` | Meaning / action |
|---|---|---|
| 401 | `unauthenticated` | Token missing/malformed/expired/revoked → get a fresh token |
| 403 | `forbidden` | Token lacks the ability, or the account was demoted |
| 413 | `payload_too_large` | Over the upload limit → use the resumable `/uploads` flow |
| 422 | `validation_failed` | See the `errors` object |
| 429 | `too_many_requests` | Honour `Retry-After` |

**Gotchas:** `POST /files` is not idempotent; use the resumable flow for large/flaky uploads;
content type is detected from the bytes; rate limits are per token; set `TRUSTED_PROXIES` behind a
proxy so IP logging/rate limits see real client IPs.

**Sources:** live spec `https://fs.sharonai.cloud/api/v1/openapi.json`; official API page
https://projectsend.org/api; official API guide https://projectsend.org/docs/api-guide; GitHub
https://github.com/projectsend/projectsend.
