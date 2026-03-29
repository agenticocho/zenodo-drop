"""
series_chainer.py — Auto-generate related_identifiers from the submitted.json ledger.

Reads submitted.json, understands paper ordering, and generates the full
`references` / `isReferencedBy` chain so you never manually type DOIs again.
"""

import json
from pathlib import Path
from typing import Dict, Any, List, Optional


LEDGER_FILE = "submitted.json"


def load_ledger(ledger_path: Optional[str] = None) -> Dict[str, Any]:
    """
    Load the submitted.json ledger.  Returns empty dict if not found.
    """
    p = Path(ledger_path or LEDGER_FILE)
    if not p.exists():
        return {}
    with open(p) as f:
        return json.load(f)


def save_ledger(ledger: Dict[str, Any], ledger_path: Optional[str] = None) -> None:
    """
    Write the submitted.json ledger (pretty-printed).
    """
    p = Path(ledger_path or LEDGER_FILE)
    with open(p, "w") as f:
        json.dump(ledger, f, indent=2, sort_keys=False)
    print(f"  📒  Ledger saved → {p}")


def record_submission(
    paper_key: str,
    doi: str,
    deposition_id: int,
    version: str,
    title: str,
    ledger_path: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Record a successful submission in the ledger.
    Returns the updated ledger.
    """
    from datetime import datetime

    ledger = load_ledger(ledger_path)
    ledger[paper_key] = {
        "doi": doi,
        "deposition_id": deposition_id,
        "version": version,
        "title": title,
        "submitted": datetime.utcnow().isoformat(timespec="seconds") + "Z",
    }
    save_ledger(ledger, ledger_path)
    return ledger


def _extract_paper_number(key: str) -> Optional[int]:
    """
    Try to extract a numeric paper number from a key like 'paper7', 'paper13', etc.
    Returns None if no number found.
    """
    import re
    m = re.search(r"(\d+)", key)
    return int(m.group(1)) if m else None


def build_chain(
    current_paper_key: str,
    ledger_path: Optional[str] = None,
    resource_type: str = "publication-preprint",
) -> List[Dict[str, str]]:
    """
    Build the related_identifiers list for `current_paper_key`.

    Logic:
    - Every PRIOR paper in the series → this paper `references` it.
    - Every LATER paper in the series → this paper `isReferencedBy` it.
    - Papers are ordered by the numeric suffix in their key (paper7, paper8, ...).
    - If no numeric suffix, all OTHER papers are treated as `references`.

    Returns a list of dicts ready for Zenodo metadata:
        [{"identifier": "10.5281/zenodo.xxx", "relation": "references", "resource_type": "..."}]
    """
    ledger = load_ledger(ledger_path)
    current_num = _extract_paper_number(current_paper_key)

    identifiers = []
    for key, entry in ledger.items():
        if key == current_paper_key:
            continue  # skip self

        doi = entry.get("doi")
        if not doi:
            continue

        other_num = _extract_paper_number(key)

        if current_num is not None and other_num is not None:
            if other_num < current_num:
                relation = "references"
            else:
                relation = "isReferencedBy"
        else:
            # Can't determine order — default to references
            relation = "references"

        ri = {
            "identifier": doi,
            "relation": relation,
        }
        if resource_type:
            ri["resource_type"] = resource_type
        identifiers.append(ri)

    # Sort: references first (prior papers), then isReferencedBy (later papers)
    identifiers.sort(key=lambda x: (0 if x["relation"] == "references" else 1, x["identifier"]))

    return identifiers


def merge_chain_into_metadata(
    metadata: Dict[str, Any],
    current_paper_key: str,
    ledger_path: Optional[str] = None,
    resource_type: str = "publication-preprint",
) -> Dict[str, Any]:
    """
    Merge auto-generated series chain into existing metadata,
    preserving any manually-specified related_identifiers.
    """
    auto_chain = build_chain(current_paper_key, ledger_path, resource_type)

    existing = metadata.get("related_identifiers", [])
    existing_ids = {ri["identifier"] for ri in existing}

    # Only add auto-chain entries that aren't already present
    for ri in auto_chain:
        if ri["identifier"] not in existing_ids:
            existing.append(ri)

    metadata["related_identifiers"] = existing
    return metadata
