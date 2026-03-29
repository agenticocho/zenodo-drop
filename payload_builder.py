"""
payload_builder.py — Discover files in a paper folder and bundle them
for upload alongside the metadata.

Auto-detects: PDF, LaTeX, CSV, Python scripts, images (PNG/JPG/SVG).
"""

from pathlib import Path
from typing import List, Dict, Optional


# ---------------------------------------------------------------------------
# File discovery
# ---------------------------------------------------------------------------

# Extensions we auto-attach (case-insensitive)
AUTO_EXTENSIONS = {
    # Primary documents
    ".pdf",
    # LaTeX source
    ".tex", ".bib", ".sty", ".cls",
    # Data
    ".csv", ".tsv", ".json", ".yml", ".yaml",
    # Code
    ".py", ".jl", ".m", ".nb", ".ipynb",
    # Images / figures
    ".png", ".jpg", ".jpeg", ".svg", ".eps", ".tiff",
    # Archives
    ".zip", ".tar.gz", ".gz",
    # Supplementary
    ".txt", ".md",
}

# Files we skip (even if extension matches)
SKIP_NAMES = {
    "metadata.yml", "metadata.yaml", "series.json",
    "submitted.json", ".zenodo_token",
    ".gitignore", ".DS_Store", "Thumbs.db",
}

SKIP_PREFIXES = (".", "__")


def discover_files(
    folder: str,
    include_figures: bool = True,
    extra_extensions: Optional[List[str]] = None,
) -> List[Path]:
    """
    Walk `folder` and return a list of file paths to attach.

    By default includes all AUTO_EXTENSIONS.  Set include_figures=False
    to skip image files.
    """
    folder_path = Path(folder)
    if not folder_path.is_dir():
        raise FileNotFoundError(f"Not a directory: {folder}")

    allowed = set(AUTO_EXTENSIONS)
    if extra_extensions:
        allowed.update(ext.lower() for ext in extra_extensions)

    if not include_figures:
        allowed -= {".png", ".jpg", ".jpeg", ".svg", ".eps", ".tiff"}

    files = []
    for p in sorted(folder_path.rglob("*")):
        if not p.is_file():
            continue
        if p.name in SKIP_NAMES:
            continue
        if any(p.name.startswith(px) for px in SKIP_PREFIXES):
            continue
        if p.suffix.lower() in allowed:
            files.append(p)

    return files


def summarize_payload(files: List[Path]) -> str:
    """
    Return a human-readable summary of what will be uploaded.
    """
    if not files:
        return "  (no files found)"

    lines = []
    total_bytes = 0
    for f in files:
        size = f.stat().st_size
        total_bytes += size
        if size < 1024:
            size_str = f"{size} B"
        elif size < 1024 * 1024:
            size_str = f"{size / 1024:.1f} KB"
        else:
            size_str = f"{size / (1024 * 1024):.1f} MB"
        lines.append(f"    {f.name:<45} {size_str:>10}")

    if total_bytes < 1024 * 1024:
        total_str = f"{total_bytes / 1024:.1f} KB"
    else:
        total_str = f"{total_bytes / (1024 * 1024):.1f} MB"

    header = f"  📦  {len(files)} file(s), {total_str} total:\n"
    return header + "\n".join(lines)


def find_metadata_file(folder: str) -> Optional[Path]:
    """
    Look for metadata.yml or metadata.yaml in the folder.
    Returns None if not found.
    """
    folder_path = Path(folder)
    for name in ("metadata.yml", "metadata.yaml"):
        p = folder_path / name
        if p.exists():
            return p
    return None
