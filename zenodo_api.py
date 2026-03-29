"""
zenodo_api.py — Low-level Zenodo REST API wrapper.

Uses the NEW bucket-based file upload (PUT), not the deprecated POST endpoint.
Supports sandbox/live toggle, exponential backoff on 429s, and Bearer auth.
"""

import os
import time
import json
import requests
from pathlib import Path
from typing import Optional, Dict, Any, List

# ---------------------------------------------------------------------------
# Token resolution: env var > .zenodo_token file
# ---------------------------------------------------------------------------

def _resolve_token(sandbox: bool = True) -> str:
    """Resolve the API token from env or dotfile."""
    env_key = "ZENODO_SANDBOX_TOKEN" if sandbox else "ZENODO_TOKEN"
    token = os.getenv(env_key)
    if token:
        return token.strip()

    # Fallback: try the generic env var
    token = os.getenv("ZENODO_TOKEN")
    if token:
        return token.strip()

    # Fallback: dotfile
    dotfile = Path(".zenodo_token")
    if dotfile.exists():
        return dotfile.read_text().strip()

    raise RuntimeError(
        f"No Zenodo token found.  Set ${env_key} or ${dotfile} in the working directory."
    )


# ---------------------------------------------------------------------------
# Base URL
# ---------------------------------------------------------------------------

def _base_url(sandbox: bool = True) -> str:
    if sandbox:
        return "https://sandbox.zenodo.org/api"
    return "https://zenodo.org/api"


# ---------------------------------------------------------------------------
# Retry wrapper with exponential backoff (429 / 5xx)
# ---------------------------------------------------------------------------

def _retry_request(fn, retries: int = 4, backoff_base: float = 2.0, idempotent: bool = True):
    """
    Execute `fn()` with retry on HTTP 429 and 5xx.
    Uses exponential backoff: 2s, 4s, 8s, 16s.

    CRITICAL: Set idempotent=False for POST requests that create resources
    (e.g. create_deposit).  Non-idempotent requests are NOT retried on 5xx
    because the server may have created the resource despite the error response.
    They ARE still retried on 429 (rate limit — the request was never processed).
    """
    last_exc = None
    for attempt in range(retries):
        try:
            resp = fn()
            resp.raise_for_status()
            return resp
        except requests.HTTPError as exc:
            last_exc = exc
            status = exc.response.status_code if exc.response is not None else 0
            if status == 429:
                wait = backoff_base ** attempt
                print(f"  ⏳  HTTP 429 rate-limited — retrying in {wait:.0f}s (attempt {attempt + 1}/{retries})")
                time.sleep(wait)
            elif status >= 500:
                if not idempotent:
                    # Non-idempotent request (POST create) — do NOT retry,
                    # the resource may already exist on the server.
                    print(f"  ⚠️  HTTP {status} on non-idempotent request — not retrying (resource may exist)")
                    raise
                wait = backoff_base ** attempt
                print(f"  ⏳  HTTP {status} — retrying in {wait:.0f}s (attempt {attempt + 1}/{retries})")
                time.sleep(wait)
            elif status == 403:
                url = exc.response.url if exc.response is not None else "unknown"
                is_sandbox = "sandbox" in url
                target = "SANDBOX" if is_sandbox else "LIVE"
                token_var = "ZENODO_SANDBOX_TOKEN" if is_sandbox else "ZENODO_TOKEN"
                print(f"\n  ❌  403 FORBIDDEN from {target} Zenodo.")
                print(f"     Possible causes:")
                print(f"       1. Token is invalid or expired")
                print(f"       2. Token lacks 'deposit:write' scope")
                print(f"       3. Token is for {'live' if is_sandbox else 'sandbox'} Zenodo, not {target.lower()}")
                print(f"     Fix: Generate a new token at:")
                if is_sandbox:
                    print(f"       https://sandbox.zenodo.org/account/settings/applications/")
                else:
                    print(f"       https://zenodo.org/account/settings/applications/")
                print(f"     Then: export {token_var}=\"your-new-token\"")
                raise
            elif status == 401:
                print(f"\n  ❌  401 UNAUTHORIZED — no valid token found or token rejected.")
                print(f"     Set ZENODO_TOKEN or ZENODO_SANDBOX_TOKEN as an environment variable.")
                raise
            else:
                raise
    raise last_exc  # all retries exhausted


# ---------------------------------------------------------------------------
# Auth headers
# ---------------------------------------------------------------------------

def _headers(token: str, content_type: Optional[str] = None) -> Dict[str, str]:
    h = {"Authorization": f"Bearer {token}"}
    if content_type:
        h["Content-Type"] = content_type
    return h


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def create_deposit(
    metadata: Optional[Dict[str, Any]] = None,
    sandbox: bool = True,
    token: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Create a new empty deposit (draft).

    Returns the full deposition JSON including:
      - id            (deposition ID)
      - links.bucket  (bucket URL for file uploads)
      - metadata.prereserve_doi.doi
    """
    token = token or _resolve_token(sandbox)
    url = f"{_base_url(sandbox)}/deposit/depositions"
    payload = {"metadata": metadata} if metadata else {}

    resp = _retry_request(
        lambda: requests.post(
            url,
            json=payload,
            headers=_headers(token, "application/json"),
        ),
        idempotent=False,  # POST creates a resource — do NOT retry on 5xx
    )
    return resp.json()


def update_metadata(
    deposition_id: int,
    metadata: Dict[str, Any],
    sandbox: bool = True,
    token: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Update the metadata on a draft deposition.

    `metadata` should be the inner metadata dict (title, description, creators, etc.).
    """
    token = token or _resolve_token(sandbox)
    url = f"{_base_url(sandbox)}/deposit/depositions/{deposition_id}"

    resp = _retry_request(
        lambda: requests.put(
            url,
            json={"metadata": metadata},
            headers=_headers(token, "application/json"),
        )
    )
    return resp.json()


def upload_file(
    bucket_url: str,
    file_path: str,
    sandbox: bool = True,
    token: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Upload a file to the deposition bucket using the NEW API (PUT).

    `bucket_url` comes from create_deposit()["links"]["bucket"].
    """
    token = token or _resolve_token(sandbox)
    filename = Path(file_path).name
    target = f"{bucket_url}/{filename}"

    def _do_upload():
        with open(file_path, "rb") as fp:
            return requests.put(
                target,
                data=fp,
                headers=_headers(token),
            )

    resp = _retry_request(_do_upload)
    return resp.json()


def publish(
    deposition_id: int,
    sandbox: bool = True,
    token: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Publish a draft deposition.  Returns the full record (including DOI).
    WARNING: Once published, a record CANNOT be deleted.
    """
    token = token or _resolve_token(sandbox)
    url = f"{_base_url(sandbox)}/deposit/depositions/{deposition_id}/actions/publish"

    resp = _retry_request(
        lambda: requests.post(url, headers=_headers(token))
    )
    return resp.json()


def create_new_version(
    deposition_id: int,
    sandbox: bool = True,
    token: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Create a new version of an ALREADY-PUBLISHED deposition.

    Returns the full response.  The new draft deposition URL is at:
        resp["links"]["latest_draft"]
    and its numeric ID is the last path segment of that URL.
    """
    token = token or _resolve_token(sandbox)
    url = f"{_base_url(sandbox)}/deposit/depositions/{deposition_id}/actions/newversion"

    resp = _retry_request(
        lambda: requests.post(url, headers=_headers(token)),
        idempotent=False,  # POST creates a new draft — do NOT retry on 5xx
    )
    data = resp.json()
    return data


def get_new_version_draft(
    deposition_id: int,
    sandbox: bool = True,
    token: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Create a new version AND fetch the draft deposition in one call.
    Returns the draft deposition JSON (with links.bucket, id, etc.).
    """
    token = token or _resolve_token(sandbox)
    nv = create_new_version(deposition_id, sandbox=sandbox, token=token)
    draft_url = nv["links"]["latest_draft"]

    resp = _retry_request(
        lambda: requests.get(draft_url, headers=_headers(token))
    )
    return resp.json()


def delete_all_files(
    deposition_id: int,
    sandbox: bool = True,
    token: Optional[str] = None,
) -> None:
    """
    Delete ALL files on a draft deposition (useful before re-uploading
    on a new version).
    """
    token = token or _resolve_token(sandbox)
    url = f"{_base_url(sandbox)}/deposit/depositions/{deposition_id}/files"
    resp = _retry_request(
        lambda: requests.get(url, headers=_headers(token))
    )
    for f in resp.json():
        file_id = f["id"]
        del_url = f"{url}/{file_id}"
        _retry_request(
            lambda u=del_url: requests.delete(u, headers=_headers(token))
        )


def get_deposit(
    deposition_id: int,
    sandbox: bool = True,
    token: Optional[str] = None,
) -> Dict[str, Any]:
    """Fetch metadata for an existing deposition."""
    token = token or _resolve_token(sandbox)
    url = f"{_base_url(sandbox)}/deposit/depositions/{deposition_id}"
    resp = _retry_request(
        lambda: requests.get(url, headers=_headers(token))
    )
    return resp.json()
