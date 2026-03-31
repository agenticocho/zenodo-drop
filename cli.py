#!/usr/bin/env python3
"""
zenodo-drop — Automate Zenodo deposits for independent researchers.

No PhD. No lab. No permission. No paywall.

Usage:
  zenodo-drop upload <folder>           Upload a new deposit from a paper folder
  zenodo-drop newversion <folder>       Upload a new version of an existing deposit
  zenodo-drop preview <folder>          Preview metadata + file list (no upload)
  zenodo-drop chain <paper_key>         Show auto-generated series chain for a paper
  zenodo-drop ledger                    Print the submitted.json ledger
"""

import argparse
import json
import sys
from pathlib import Path

from zenodo_api import (
    create_deposit,
    update_metadata,
    upload_file,
    publish,
    get_new_version_draft,
    delete_all_files,
)
from metadata import (
    load_metadata,
    apply_defaults,
    validate,
    build_zenodo_metadata,
)
from series_chainer import (
    load_ledger,
    record_submission,
    merge_chain_into_metadata,
    build_chain,
)
from payload_builder import (
    discover_files,
    summarize_payload,
    find_metadata_file,
)
from metadata_extractor import (
    extract_metadata,
    generate_metadata_yml,
    find_source_file,
)


# ═══════════════════════════════════════════════════════════════════════════
#  Helpers
# ═══════════════════════════════════════════════════════════════════════════

def _resolve_paper_key(folder: str, meta: dict) -> str:
    """Derive a paper key from the folder name or metadata title."""
    folder_name = Path(folder).resolve().name
    # Try to keep it short: use folder name if it looks like 'paper7', 'paper13', etc.
    import re
    if re.match(r"paper\d+", folder_name, re.IGNORECASE):
        return folder_name.lower()
    # Fallback: slugify folder name
    return re.sub(r"[^a-z0-9]+", "-", folder_name.lower()).strip("-")


def _ensure_metadata(folder: str) -> str:
    """
    Ensure metadata.yml exists in the folder.
    If missing, auto-extract from the paper source (LaTeX/PDF) and generate it.
    Returns the path to metadata.yml.
    """
    meta_file = find_metadata_file(folder)
    if meta_file:
        return str(meta_file)

    # No metadata.yml — try to auto-generate from source
    source = find_source_file(folder)
    if not source:
        print(f"❌  No metadata.yml, .tex, or .pdf found in {folder}")
        sys.exit(1)

    print(f"  🔍  No metadata.yml found — auto-extracting from {Path(source[0]).name}...")
    meta = extract_metadata(folder)
    meta["_source_file"] = Path(source[0]).name

    out_path = str(Path(folder) / "metadata.yml")
    generate_metadata_yml(meta, out_path)
    return out_path


def _get_deposit_ids(sandbox: bool = True) -> set:
    """
    Snapshot all current deposit IDs.  Used before create_deposit()
    so we can spot server-side duplicates afterwards.
    """
    from zenodo_api import _resolve_token, _base_url, _headers
    import requests as _req

    token = _resolve_token(sandbox)
    base = _base_url(sandbox)
    r = _req.get(
        f"{base}/deposit/depositions",
        headers=_headers(token),
        params={"size": 200, "sort": "mostrecent"},
    )
    if r.ok:
        return {d["id"] for d in r.json()}
    return set()


def _kill_server_dupes(keep_id: int, ids_before: set, sandbox: bool = True):
    """
    Zenodo's backend sometimes creates duplicate deposits from a single POST.
    This function runs immediately after create_deposit(), compares the current
    deposit list to the pre-create snapshot, and deletes any new deposits
    that aren't the one we intend to keep.
    """
    import time
    from zenodo_api import _resolve_token, _base_url, _headers
    import requests as _req

    time.sleep(1)  # brief pause for Zenodo to settle

    token = _resolve_token(sandbox)
    base = _base_url(sandbox)
    hdrs = _headers(token)

    r = _req.get(
        f"{base}/deposit/depositions",
        headers=hdrs,
        params={"size": 200, "sort": "mostrecent"},
    )
    if not r.ok:
        return

    ids_after = {d["id"] for d in r.json()}
    new_ids = ids_after - ids_before
    dupes = new_ids - {keep_id}

    if dupes:
        print(f"  ⚠️  Zenodo created {len(dupes)} server-side duplicate(s) — deleting...")
        for dupe_id in sorted(dupes):
            dr = _req.delete(f"{base}/deposit/depositions/{dupe_id}", headers=hdrs)
            if dr.ok or dr.status_code == 204:
                print(f"  🗑️   Deleted ghost {dupe_id}")
            else:
                # May already be published by the time we get here — try discard first
                _req.post(f"{base}/deposit/depositions/{dupe_id}/actions/discard", headers=hdrs)
                dr2 = _req.delete(f"{base}/deposit/depositions/{dupe_id}", headers=hdrs)
                if dr2.ok or dr2.status_code == 204:
                    print(f"  🗑️   Deleted ghost {dupe_id} (after discard)")
                else:
                    print(f"  ⚠️  Could not delete ghost {dupe_id} ({dr2.status_code})")


def _print_metadata_preview(zenodo_meta: dict):
    """Pretty-print the metadata payload."""
    print("\n" + "=" * 70)
    print("  METADATA PREVIEW")
    print("=" * 70)
    print(json.dumps({"metadata": zenodo_meta}, indent=2, ensure_ascii=False))
    print("=" * 70 + "\n")


# ═══════════════════════════════════════════════════════════════════════════
#  Commands
# ═══════════════════════════════════════════════════════════════════════════

def cmd_preview(args):
    """Preview metadata and file list — no network calls."""
    folder = args.folder
    meta_file = _ensure_metadata(folder)
    meta = load_metadata(meta_file)
    meta = apply_defaults(meta)

    # Merge series chain if ledger exists
    paper_key = _resolve_paper_key(folder, meta)
    ledger_path = args.ledger or "submitted.json"
    if Path(ledger_path).exists():
        meta = merge_chain_into_metadata(meta, paper_key, ledger_path)

    # Validate
    warnings = validate(meta)
    for w in warnings:
        print(f"  ⚠️  {w}")

    # Build Zenodo metadata
    zenodo_meta = build_zenodo_metadata(meta)
    _print_metadata_preview(zenodo_meta)

    # File list
    files = discover_files(folder, include_figures=args.attach_figures)
    print(summarize_payload(files))
    print(f"\n  Paper key: {paper_key}")
    print(f"  Ledger:    {ledger_path}")
    print(f"  Target:    {'SANDBOX' if args.sandbox else '🔴 LIVE'}")
    print()


def cmd_upload(args):
    """Create a new deposit, upload files, optionally publish."""
    folder = args.folder
    meta_file = _ensure_metadata(folder)
    meta = load_metadata(meta_file)
    meta = apply_defaults(meta)

    paper_key = _resolve_paper_key(folder, meta)
    ledger_path = args.ledger or "submitted.json"

    # Merge series chain
    if Path(ledger_path).exists():
        meta = merge_chain_into_metadata(meta, paper_key, ledger_path)

    # Validate
    warnings = validate(meta)
    for w in warnings:
        print(f"  ⚠️  {w}")

    zenodo_meta = build_zenodo_metadata(meta)

    # Dry-run gate
    if args.dry_run:
        print("\n  🧪  DRY RUN — would submit the following:\n")
        _print_metadata_preview(zenodo_meta)
        files = discover_files(folder, include_figures=args.attach_figures)
        print(summarize_payload(files))
        print(f"\n  Target: {'SANDBOX' if args.sandbox else '🔴 LIVE'}")
        return

    # Preview
    if args.preview:
        _print_metadata_preview(zenodo_meta)
        resp = input("  Proceed? [y/N] ").strip().lower()
        if resp != "y":
            print("  Aborted.")
            return

    sandbox = args.sandbox

    # 1. Create deposit (with duplicate guard)
    print(f"\n  🚀  Creating deposit on {'SANDBOX' if sandbox else 'LIVE'}...")
    ids_before = _get_deposit_ids(sandbox)
    deposit = create_deposit(sandbox=sandbox)
    dep_id = deposit["id"]
    bucket_url = deposit["links"]["bucket"]
    pre_doi = deposit.get("metadata", {}).get("prereserve_doi", {}).get("doi", "pending")
    print(f"  ✅  Deposit {dep_id} created  (pre-reserved DOI: {pre_doi})")
    _kill_server_dupes(dep_id, ids_before, sandbox)

    # 2. Update metadata
    print("  📝  Setting metadata...")
    update_metadata(dep_id, zenodo_meta, sandbox=sandbox)

    # 3. Upload files
    files = discover_files(folder, include_figures=args.attach_figures)
    print(summarize_payload(files))
    for f in files:
        print(f"  ⬆️   Uploading {f.name}...")
        upload_file(bucket_url, str(f), sandbox=sandbox)

    # 4. Publish (unless --no-publish)
    if args.no_publish:
        print(f"\n  ⏸️   Deposit {dep_id} is a DRAFT (not published).")
        print(f"  Edit at: https://{'sandbox.' if sandbox else ''}zenodo.org/deposit/{dep_id}")
        return

    print("  📢  Publishing...")
    result = publish(dep_id, sandbox=sandbox)
    doi = result.get("doi", "unknown")
    print(f"\n  🎉  PUBLISHED!")
    print(f"  DOI:  {doi}")
    print(f"  URL:  https://doi.org/{doi}")

    # 5. Record in ledger
    record_submission(
        paper_key=paper_key,
        doi=doi,
        deposition_id=dep_id,
        version=meta.get("version", "v1"),
        title=meta.get("title", ""),
        ledger_path=ledger_path,
    )

    # 6. Clean up ghost drafts
    # Zenodo sometimes creates duplicate unsubmitted deposits server-side.
    # Delete any unsubmitted drafts that aren't the one we just published.
    _cleanup_ghost_drafts(dep_id, sandbox=sandbox)


def _cleanup_ghost_drafts(published_id: int, sandbox: bool = True):
    """Delete unsubmitted draft deposits that aren't the published one."""
    from zenodo_api import _resolve_token, _base_url, _headers
    import requests as _req

    token = _resolve_token(sandbox)
    base = _base_url(sandbox)
    hdrs = _headers(token)

    r = _req.get(
        f"{base}/deposit/depositions",
        headers=hdrs,
        params={"size": 50, "sort": "mostrecent"},
    )
    if not r.ok:
        return

    cleaned = 0
    for dep in r.json():
        if dep.get("state") == "unsubmitted" and dep["id"] != published_id:
            del_r = _req.delete(f"{base}/deposit/depositions/{dep['id']}", headers=hdrs)
            if del_r.ok or del_r.status_code == 204:
                cleaned += 1

    if cleaned:
        print(f"  🧹  Cleaned {cleaned} ghost draft(s)")


def cmd_newversion(args):
    """Create a new version of an existing deposit."""
    folder = args.folder
    meta_file = _ensure_metadata(folder)
    meta = load_metadata(meta_file)
    meta = apply_defaults(meta)

    paper_key = _resolve_paper_key(folder, meta)
    ledger_path = args.ledger or "submitted.json"

    # Find the existing deposition ID
    ledger = load_ledger(ledger_path)
    if paper_key not in ledger:
        print(f"❌  '{paper_key}' not found in ledger ({ledger_path}).")
        print(f"    Known papers: {', '.join(ledger.keys()) or '(none)'}")
        print(f"    Use 'upload' for first-time deposits.")
        sys.exit(1)

    existing_id = ledger[paper_key]["deposition_id"]
    old_version = ledger[paper_key].get("version", "v?")
    print(f"  📖  Found {paper_key} in ledger: deposition {existing_id}, {old_version}")

    # Merge series chain
    if Path(ledger_path).exists():
        meta = merge_chain_into_metadata(meta, paper_key, ledger_path)

    # Validate
    warnings = validate(meta)
    for w in warnings:
        print(f"  ⚠️  {w}")

    zenodo_meta = build_zenodo_metadata(meta)

    # Dry-run gate
    if args.dry_run:
        print("\n  🧪  DRY RUN — would create new version:\n")
        _print_metadata_preview(zenodo_meta)
        files = discover_files(folder, include_figures=args.attach_figures)
        print(summarize_payload(files))
        return

    sandbox = args.sandbox

    # 1. Create new version draft
    print(f"  🔄  Creating new version of deposition {existing_id}...")
    draft = get_new_version_draft(existing_id, sandbox=sandbox)
    new_id = draft["id"]
    bucket_url = draft["links"]["bucket"]
    print(f"  ✅  New version draft: {new_id}")

    # 2. Delete old files from the draft
    print("  🗑️   Clearing old files from draft...")
    delete_all_files(new_id, sandbox=sandbox)

    # 3. Update metadata
    print("  📝  Setting metadata...")
    update_metadata(new_id, zenodo_meta, sandbox=sandbox)

    # 4. Upload new files
    files = discover_files(folder, include_figures=args.attach_figures)
    print(summarize_payload(files))
    for f in files:
        print(f"  ⬆️   Uploading {f.name}...")
        upload_file(bucket_url, str(f), sandbox=sandbox)

    # 5. Publish
    if args.no_publish:
        print(f"\n  ⏸️   Draft {new_id} ready (not published).")
        return

    print("  📢  Publishing new version...")
    result = publish(new_id, sandbox=sandbox)
    doi = result.get("doi", "unknown")
    new_ver = meta.get("version", f"v{int(old_version.lstrip('v')) + 1}" if old_version.startswith("v") else "v2")
    print(f"\n  🎉  NEW VERSION PUBLISHED!")
    print(f"  DOI:  {doi}")
    print(f"  URL:  https://doi.org/{doi}")

    # 6. Record in ledger
    record_submission(
        paper_key=paper_key,
        doi=doi,
        deposition_id=new_id,
        version=new_ver,
        title=meta.get("title", ""),
        ledger_path=ledger_path,
    )


def cmd_chain(args):
    """Show the auto-generated series chain for a paper."""
    ledger_path = args.ledger or "submitted.json"
    chain = build_chain(args.paper_key, ledger_path)

    if not chain:
        print(f"  No chain entries for '{args.paper_key}' (ledger: {ledger_path})")
        return

    print(f"\n  Series chain for '{args.paper_key}':\n")
    for ri in chain:
        print(f"    {ri['relation']:<20}  {ri['identifier']}")
    print()


def cmd_ledger(args):
    """Print the submitted.json ledger."""
    ledger_path = args.ledger or "submitted.json"
    ledger = load_ledger(ledger_path)

    if not ledger:
        print(f"  Ledger is empty ({ledger_path}).")
        return

    print(f"\n  📒  Ledger: {ledger_path}\n")
    for key, entry in sorted(ledger.items(), key=lambda x: x[0]):
        print(f"    {key:<15}  DOI: {entry.get('doi', '?'):<35}  v: {entry.get('version', '?'):<5}  {entry.get('submitted', '')}")
    print()


def cmd_backlink(args):
    """Add isReferencedBy to prior papers — skips the new paper's own record."""
    import time
    from zenodo_api import _resolve_token, _base_url, _headers, _retry_request
    import requests as _req

    new_doi = args.doi
    record_ids = [int(x.strip()) for x in args.records.split(",") if x.strip()]
    sandbox = args.sandbox

    # Extract the new paper's record ID from its DOI to skip it
    new_record_id = None
    try:
        new_record_id = int(new_doi.split(".")[-1])
    except (ValueError, IndexError):
        pass

    # Filter out the new paper's own record
    original_count = len(record_ids)
    record_ids = [rid for rid in record_ids if rid != new_record_id]
    if len(record_ids) < original_count:
        print(f"  ℹ️  Skipping record {new_record_id} (the new paper itself)")

    new_link = {
        "identifier": new_doi,
        "relation": "isReferencedBy",
        "resource_type": "publication-preprint",
    }

    if args.dry_run:
        print(f"\n  🧪  DRY RUN — would add isReferencedBy → {new_doi} to {len(record_ids)} records:")
        for rid in record_ids:
            print(f"    {rid}")
        return

    token = _resolve_token(sandbox)
    base = _base_url(sandbox)
    hdrs = _headers(token, "application/json")

    print(f"\n  Adding isReferencedBy → {new_doi}")
    print(f"  Updating {len(record_ids)} records...\n")

    def _clean(meta):
        if "dates" in meta:
            meta["dates"] = [d for d in meta["dates"] if d.get("start") or d.get("date")]
            if not meta["dates"]:
                del meta["dates"]
        for k in ["doi", "prereserve_doi", "relations"]:
            meta.pop(k, None)
        if "related_identifiers" in meta:
            meta["related_identifiers"] = [
                {k: v for k, v in ri.items() if k != "scheme"}
                for ri in meta["related_identifiers"]
                if ri.get("relation") != "isVersionOf"
            ]
        return meta

    ok, fail = 0, 0
    for rid in record_ids:
        # Open for edit
        r = None
        for attempt in range(3):
            r = _req.post(f"{base}/deposit/depositions/{rid}/actions/edit", headers=hdrs)
            if r.ok:
                break
            time.sleep(3 * (attempt + 1))

        if not r or not r.ok:
            print(f"  {rid}: ❌ open failed")
            fail += 1
            continue

        dep = r.json()
        meta = dep["metadata"]

        # Check if already linked
        existing_ids = {ri["identifier"] for ri in meta.get("related_identifiers", [])}
        if new_doi in existing_ids:
            _req.post(f"{base}/deposit/depositions/{rid}/actions/discard", headers=hdrs)
            print(f"  {rid}: already linked ✅")
            ok += 1
            continue

        # Add + clean + update
        meta.setdefault("related_identifiers", []).append(new_link)
        meta = _clean(meta)
        r = _req.put(f"{base}/deposit/depositions/{rid}", headers=hdrs, json={"metadata": meta})
        if not r.ok:
            print(f"  {rid}: ❌ update failed ({r.status_code})")
            _req.post(f"{base}/deposit/depositions/{rid}/actions/discard", headers=hdrs)
            fail += 1
            continue

        # Publish with retry
        for attempt in range(3):
            r = _req.post(f"{base}/deposit/depositions/{rid}/actions/publish", headers=hdrs)
            if r.ok or r.status_code != 500:
                break
            time.sleep(3 * (attempt + 1))

        if r.ok:
            print(f"  {rid}: ✅")
            ok += 1
        else:
            print(f"  {rid}: ⏳ ({r.status_code}, may finalize async)")
            ok += 1  # 500s usually process

        time.sleep(1.5)

    print(f"\n  Done: {ok} updated, {fail} failed")


# ═══════════════════════════════════════════════════════════════════════════
#  Argument parser
# ═══════════════════════════════════════════════════════════════════════════

def build_parser():
    parser = argparse.ArgumentParser(
        prog="zenodo-drop",
        description="Automate Zenodo deposits.  No PhD. No lab. No permission. No paywall.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    # Global flags
    parser.add_argument(
        "--sandbox", action="store_true", default=True,
        help="Use Zenodo sandbox (default: True — safe mode)",
    )
    parser.add_argument(
        "--live", action="store_true", default=False,
        help="Use LIVE Zenodo (override sandbox default)",
    )
    parser.add_argument(
        "--ledger", type=str, default=None,
        help="Path to submitted.json ledger (default: ./submitted.json)",
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # --- upload ---
    p_upload = subparsers.add_parser("upload", help="Upload a new deposit")
    p_upload.add_argument("folder", help="Path to the paper folder")
    p_upload.add_argument("--dry-run", action="store_true", help="Preview only, no API calls")
    p_upload.add_argument("--preview", action="store_true", help="Show metadata before submitting")
    p_upload.add_argument("--no-publish", action="store_true", help="Create draft only, don't publish")
    p_upload.add_argument("--attach-figures", action="store_true", default=True, help="Auto-attach image files (default: True)")
    p_upload.add_argument("--no-figures", dest="attach_figures", action="store_false", help="Skip image files")

    # --- newversion ---
    p_nv = subparsers.add_parser("newversion", help="Upload a new version of an existing deposit")
    p_nv.add_argument("folder", help="Path to the paper folder")
    p_nv.add_argument("--dry-run", action="store_true", help="Preview only, no API calls")
    p_nv.add_argument("--preview", action="store_true", help="Show metadata before submitting")
    p_nv.add_argument("--no-publish", action="store_true", help="Create draft only, don't publish")
    p_nv.add_argument("--attach-figures", action="store_true", default=True, help="Auto-attach image files")
    p_nv.add_argument("--no-figures", dest="attach_figures", action="store_false", help="Skip image files")

    # --- preview ---
    p_preview = subparsers.add_parser("preview", help="Preview metadata + files (no upload)")
    p_preview.add_argument("folder", help="Path to the paper folder")
    p_preview.add_argument("--attach-figures", action="store_true", default=True, help="Include image files in preview")
    p_preview.add_argument("--no-figures", dest="attach_figures", action="store_false", help="Skip image files")

    # --- chain ---
    p_chain = subparsers.add_parser("chain", help="Show series chain for a paper")
    p_chain.add_argument("paper_key", help="Paper key (e.g. paper7, paper13)")

    # --- ledger ---
    subparsers.add_parser("ledger", help="Print the submitted.json ledger")

    # --- backlink ---
    p_bl = subparsers.add_parser("backlink", help="Add isReferencedBy to all prior papers")
    p_bl.add_argument("doi", help="DOI of the new paper (e.g. 10.5281/zenodo.19317500)")
    p_bl.add_argument("--records", type=str, required=True, help="Comma-separated record IDs to update")
    p_bl.add_argument("--dry-run", action="store_true", help="Preview only")

    return parser


# ═══════════════════════════════════════════════════════════════════════════
#  Main
# ═══════════════════════════════════════════════════════════════════════════

def main():
    parser = build_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(0)

    # --live overrides --sandbox
    if args.live:
        args.sandbox = False

    dispatch = {
        "upload": cmd_upload,
        "newversion": cmd_newversion,
        "preview": cmd_preview,
        "chain": cmd_chain,
        "ledger": cmd_ledger,
        "backlink": cmd_backlink,
    }

    cmd_fn = dispatch.get(args.command)
    if cmd_fn:
        cmd_fn(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
