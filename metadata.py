"""
metadata.py — Load metadata from YAML, validate required fields,
and build the Zenodo-ready metadata dict.
"""

import yaml
from pathlib import Path
from datetime import date
from typing import Dict, Any, Optional, List


# ---------------------------------------------------------------------------
# Valid Zenodo values (for validation)
# ---------------------------------------------------------------------------

VALID_UPLOAD_TYPES = {
    "publication", "poster", "presentation", "dataset",
    "image", "video", "software", "lesson", "physicalobject", "other",
}

VALID_PUBLICATION_TYPES = {
    "annotationcollection", "book", "section", "conferencepaper",
    "datamanagementplan", "article", "patent", "preprint",
    "deliverable", "milestone", "proposal", "report",
    "softwaredocumentation", "taxonomictreatment", "technicalnote",
    "thesis", "workingpaper", "other",
}

VALID_RELATIONS = {
    "isCitedBy", "cites", "isSupplementTo", "isSupplementedBy",
    "isContinuedBy", "continues", "isDescribedBy", "describes",
    "hasMetadata", "isMetadataFor", "isNewVersionOf",
    "isPreviousVersionOf", "isPartOf", "hasPart",
    "isReferencedBy", "references", "isDocumentedBy", "documents",
    "isCompiledBy", "compiles", "isVariantFormOf", "isOriginalFormOf",
    "isIdenticalTo", "isAlternateIdentifier", "isReviewedBy", "reviews",
    "isDerivedFrom", "isSourceOf", "requires", "isRequiredBy",
    "isObsoletedBy", "obsoletes",
}

VALID_LICENSES = {
    "CC-BY-4.0", "CC-BY-SA-4.0", "CC-BY-NC-4.0", "CC-BY-NC-SA-4.0",
    "CC0-1.0", "MIT", "Apache-2.0", "GPL-3.0-only", "GPL-3.0-or-later",
}


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

def load_metadata(path: str) -> Dict[str, Any]:
    """
    Load metadata from a YAML file.  Returns the raw dict.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Metadata file not found: {p}")
    with open(p) as f:
        data = yaml.safe_load(f)
    if data is None:
        raise ValueError(f"Empty metadata file: {p}")
    return data


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

def apply_defaults(meta: Dict[str, Any]) -> Dict[str, Any]:
    """
    Fill in sensible defaults for optional fields.
    """
    meta.setdefault("upload_type", "publication")
    meta.setdefault("publication_type", "preprint")
    meta.setdefault("publication_date", date.today().isoformat())
    meta.setdefault("access_right", "open")
    meta.setdefault("license", "CC-BY-4.0")
    meta.setdefault("language", "eng")
    meta.setdefault("keywords", [])
    meta.setdefault("related_identifiers", [])
    meta.setdefault("communities", [])
    return meta


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

class MetadataError(Exception):
    """Raised when metadata fails validation."""
    pass


def validate(meta: Dict[str, Any]) -> List[str]:
    """
    Validate metadata.  Returns a list of warning strings.
    Raises MetadataError on hard failures.
    """
    errors = []
    warnings = []

    # Required fields
    if not meta.get("title"):
        errors.append("'title' is required.")
    if not meta.get("description"):
        errors.append("'description' is required.")
    if not meta.get("creators") or len(meta["creators"]) == 0:
        errors.append("At least one creator is required.")

    # Creator structure
    for i, c in enumerate(meta.get("creators", [])):
        if not c.get("name"):
            errors.append(f"Creator {i}: 'name' is required (format: 'Last, First').")
        if not c.get("affiliation"):
            warnings.append(f"Creator {i}: 'affiliation' is empty — consider adding one.")
        orcid = c.get("orcid", "")
        if orcid == "":
            warnings.append(f"Creator {i}: ORCID is blank — TODO: add ORCID when available.")

    # Upload type
    ut = meta.get("upload_type", "")
    if ut and ut not in VALID_UPLOAD_TYPES:
        errors.append(f"Invalid upload_type '{ut}'. Valid: {VALID_UPLOAD_TYPES}")

    # Publication type
    if ut == "publication":
        pt = meta.get("publication_type", "")
        if pt and pt not in VALID_PUBLICATION_TYPES:
            errors.append(f"Invalid publication_type '{pt}'. Valid: {VALID_PUBLICATION_TYPES}")

    # License
    lic = meta.get("license", "")
    if lic and lic not in VALID_LICENSES:
        warnings.append(f"License '{lic}' not in common set — double-check it's valid on Zenodo.")

    # Related identifiers
    for i, ri in enumerate(meta.get("related_identifiers", [])):
        if not ri.get("identifier"):
            errors.append(f"related_identifiers[{i}]: 'identifier' is required.")
        rel = ri.get("relation", "")
        if rel not in VALID_RELATIONS:
            errors.append(
                f"related_identifiers[{i}]: invalid relation '{rel}'. "
                f"Valid: {sorted(VALID_RELATIONS)}"
            )

    if errors:
        raise MetadataError("Metadata validation failed:\n  - " + "\n  - ".join(errors))

    return warnings


# ---------------------------------------------------------------------------
# Build Zenodo payload
# ---------------------------------------------------------------------------

def build_zenodo_metadata(meta: Dict[str, Any]) -> Dict[str, Any]:
    """
    Transform our YAML metadata dict into the exact structure Zenodo expects
    inside {"metadata": {...}}.
    """
    meta = apply_defaults(meta)

    # Clean up creators — remove blank orcid to avoid API rejection
    creators = []
    for c in meta.get("creators", []):
        entry = {"name": c["name"]}
        if c.get("affiliation"):
            entry["affiliation"] = c["affiliation"]
        if c.get("orcid") and c["orcid"].strip():
            entry["orcid"] = c["orcid"].strip()
        creators.append(entry)

    zenodo_meta = {
        "title": meta["title"],
        "description": meta["description"],
        "upload_type": meta["upload_type"],
        "publication_date": meta["publication_date"],
        "creators": creators,
        "access_right": meta.get("access_right", "open"),
        "license": meta.get("license", "CC-BY-4.0"),
        "keywords": meta.get("keywords", []),
    }

    # Optional: publication_type
    if meta.get("publication_type"):
        zenodo_meta["publication_type"] = meta["publication_type"]

    # Optional: language
    if meta.get("language"):
        zenodo_meta["language"] = meta["language"]

    # Optional: notes
    if meta.get("notes"):
        zenodo_meta["notes"] = meta["notes"]

    # Related identifiers
    ri_list = meta.get("related_identifiers", [])
    if ri_list:
        zenodo_ri = []
        for ri in ri_list:
            entry = {
                "identifier": ri["identifier"],
                "relation": ri["relation"],
            }
            if ri.get("resource_type"):
                entry["resource_type"] = ri["resource_type"]
            zenodo_ri.append(entry)
        zenodo_meta["related_identifiers"] = zenodo_ri

    # Communities
    communities = meta.get("communities", [])
    if communities:
        zenodo_meta["communities"] = [
            {"identifier": c} if isinstance(c, str) else c
            for c in communities
        ]

    return zenodo_meta
