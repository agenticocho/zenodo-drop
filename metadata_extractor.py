"""
metadata_extractor.py — Auto-extract metadata from LaTeX or PDF papers.

Reads the source file and pulls out:
  - title
  - abstract
  - keywords
  - authors (name, affiliation)
  - related identifiers (from bibliography DOIs)

Eliminates the need to manually write metadata.yml.
"""

import os
import re
import subprocess
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple
from datetime import date


# ═══════════════════════════════════════════════════════════════════════════
#  LaTeX extraction
# ═══════════════════════════════════════════════════════════════════════════

def _strip_latex_commands(text: str) -> str:
    """Strip common LaTeX commands from text, preserving math."""
    # Remove \textbf{...}, \textit{...}, \emph{...} — keep inner text
    text = re.sub(r"\\(?:textbf|textit|emph|textrm|textsc)\{([^}]*)\}", r"\1", text)
    # Remove \label{...}, \ref{...}, \cite{...}
    text = re.sub(r"\\(?:label|ref|cite|eqref)\{[^}]*\}", "", text)
    # Remove \\, \noindent, \maketitle, etc.
    text = re.sub(r"\\(?:noindent|maketitle|bigskip|medskip|smallskip|vspace\{[^}]*\}|hspace\{[^}]*\})", "", text)
    # Collapse multiple spaces/newlines
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _extract_braced(tex: str, command: str) -> Optional[str]:
    """Extract content from \\command{...}, handling nested braces."""
    pattern = re.compile(r"\\" + re.escape(command) + r"\s*\{")
    m = pattern.search(tex)
    if not m:
        return None

    start = m.end()
    depth = 1
    i = start
    while i < len(tex) and depth > 0:
        if tex[i] == "{":
            depth += 1
        elif tex[i] == "}":
            depth -= 1
        i += 1

    return tex[start : i - 1].strip()


def _extract_environment(tex: str, env_name: str) -> Optional[str]:
    """Extract content between \\begin{env} and \\end{env}."""
    pattern = re.compile(
        r"\\begin\{" + re.escape(env_name) + r"\}(.*?)\\end\{" + re.escape(env_name) + r"\}",
        re.DOTALL,
    )
    m = pattern.search(tex)
    if m:
        return m.group(1).strip()
    return None


def extract_from_latex(tex_path: str) -> Dict[str, Any]:
    """
    Extract metadata from a .tex file.

    Returns a dict with keys: title, description, creators, keywords, etc.
    """
    tex = Path(tex_path).read_text(encoding="utf-8", errors="replace")

    meta = {}

    # --- Title ---
    title = _extract_braced(tex, "title")
    if title:
        meta["title"] = _strip_latex_commands(title)

    # --- Abstract ---
    abstract = _extract_environment(tex, "abstract")
    if abstract:
        meta["description"] = _strip_latex_commands(abstract)

    # --- Authors ---
    # Handle common patterns: \author{Name}, \author{Name \\ Affiliation},
    # \author[affil]{Name}, and multiple \author commands
    creators = []

    # Pattern 1: \author{Name \\ Affiliation} or \author{Name}
    author_block = _extract_braced(tex, "author")
    if author_block:
        # Check for \and separator (common in multi-author papers)
        author_parts = re.split(r"\\and\b", author_block)

        for part in author_parts:
            part = part.strip()
            if not part:
                continue

            # Split on \\ for name/affiliation
            lines = [_strip_latex_commands(l.strip()) for l in re.split(r"\\\\", part)]
            lines = [l for l in lines if l]

            if lines:
                name = lines[0]
                # Clean up footnote markers, superscripts, etc.
                name = re.sub(r"\\footnote\{[^}]*\}", "", name)
                name = re.sub(r"\\\w+\{[^}]*\}", "", name).strip()
                name = re.sub(r"[~\^]", "", name).strip()

                affiliation = ""
                if len(lines) > 1:
                    affiliation = lines[1]

                if name:
                    creator = {"name": name}
                    if affiliation:
                        creator["affiliation"] = affiliation
                    creators.append(creator)

    # Pattern 2: Look for \affiliation{...} commands (REVTeX style)
    if creators and not any(c.get("affiliation") for c in creators):
        affil = _extract_braced(tex, "affiliation")
        if affil:
            affil_clean = _strip_latex_commands(affil)
            for c in creators:
                c["affiliation"] = affil_clean

    # If no authors found, try \name{...} (some templates)
    if not creators:
        name = _extract_braced(tex, "name")
        if name:
            creators.append({"name": _strip_latex_commands(name)})

    if creators:
        meta["creators"] = creators

    # --- Keywords ---
    # Try \keywords{...} command
    kw = _extract_braced(tex, "keywords")
    if not kw:
        # Try the keywords environment
        kw = _extract_environment(tex, "keywords")
    if not kw:
        # Try \begin{keyword}...\end{keyword} (Elsevier style)
        kw = _extract_environment(tex, "keyword")

    if kw:
        kw_clean = _strip_latex_commands(kw)
        # Split on comma, semicolon, or \sep
        keywords = re.split(r"[;,]|\\sep", kw_clean)
        keywords = [k.strip() for k in keywords if k.strip()]
        if keywords:
            meta["keywords"] = keywords

    # --- Date ---
    date_str = _extract_braced(tex, "date")
    if date_str:
        # Try to parse common formats
        date_clean = _strip_latex_commands(date_str)
        if date_clean.lower() == "\\today" or date_clean.lower() == "today":
            meta["publication_date"] = date.today().isoformat()
        else:
            # Try ISO format first
            iso_match = re.search(r"(\d{4}-\d{2}-\d{2})", date_clean)
            if iso_match:
                meta["publication_date"] = iso_match.group(1)
            else:
                # Store raw — user can fix
                meta["publication_date"] = date.today().isoformat()

    # --- DOIs from bibliography ---
    doi_pattern = re.compile(r"10\.\d{4,9}/[^\s,}\]]+")
    dois = doi_pattern.findall(tex)
    if dois:
        # Deduplicate while preserving order
        seen = set()
        unique_dois = []
        for d in dois:
            # Clean trailing punctuation
            d = d.rstrip(".")
            if d not in seen:
                seen.add(d)
                unique_dois.append(d)

        related = []
        for d in unique_dois:
            related.append({
                "identifier": d,
                "relation": "references",
                "resource_type": "publication-preprint",
            })
        if related:
            meta["related_identifiers"] = related

    return meta


# ═══════════════════════════════════════════════════════════════════════════
#  PDF extraction (via pdftotext or PyPDF2 fallback)
# ═══════════════════════════════════════════════════════════════════════════

def _pdf_to_text(pdf_path: str) -> str:
    """Extract text from PDF. Tries pdftotext first, falls back to PyPDF2."""
    # Try pdftotext (poppler-utils) — better quality
    try:
        result = subprocess.run(
            ["pdftotext", "-layout", pdf_path, "-"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    # Fallback: PyPDF2
    try:
        from PyPDF2 import PdfReader
        reader = PdfReader(pdf_path)
        text = ""
        for page in reader.pages[:5]:  # First 5 pages should have all metadata
            text += page.extract_text() or ""
        return text
    except ImportError:
        pass

    # Last resort: raw read (won't work well but won't crash)
    return ""


def extract_from_pdf(pdf_path: str) -> Dict[str, Any]:
    """
    Extract metadata from a PDF file.

    Uses text extraction to find title (first large text block),
    abstract, and keywords.
    """
    text = _pdf_to_text(pdf_path)
    if not text.strip():
        return {}

    meta = {}
    lines = text.split("\n")

    # --- Title: usually the first non-blank, non-header line(s) ---
    title_lines = []
    started = False
    for line in lines:
        stripped = line.strip()
        if not stripped:
            if started:
                break
            continue
        # Skip headers like "arXiv:...", page numbers, dates at top
        if re.match(r"^(arXiv:|Page\s+\d|Volume|Issue|\d{4}-\d{2}-\d{2})", stripped):
            continue
        started = True
        title_lines.append(stripped)
        # Title usually 1-3 lines before author block
        if len(title_lines) >= 3:
            break

    if title_lines:
        meta["title"] = " ".join(title_lines)

    # --- Abstract ---
    abstract = None

    # Pattern 1: "Abstract" heading on its own line, body follows
    _abs_m = re.search(
        r"(?:^|\n)\s*(?:Abstract|ABSTRACT)[:\s.\-]*\n(.*?)(?=\n\s*(?:Keywords|KEYWORDS|Key\s*words|1[.\s]+|I{1,3}\.\s)|\n\s*\n\s*\n)",
        text, re.DOTALL | re.IGNORECASE,
    )
    if _abs_m and len(_abs_m.group(1).strip()) > 30:
        abstract = _abs_m.group(1).strip()

    # Pattern 2: "Abstract" with body on the SAME line (single-column PDFs)
    if not abstract:
        _abs_m = re.search(
            r"(?:^|\n)\s*(?:Abstract|ABSTRACT)[:\s.\-]+(\S.{30,})",
            text, re.IGNORECASE,
        )
        if _abs_m:
            rest = text[_abs_m.start(1):]
            end = re.search(r"\n\s*\n|\n\s*(?:Keywords|KEYWORDS|Key\s*words|1[.\s]+|I{1,3}\.\s)", rest, re.IGNORECASE)
            if end:
                abstract = rest[:end.start()].strip()
            else:
                abstract = rest[:2000].strip()

    # Pattern 3: Generous grab after "Abstract" until double-blank
    if not abstract:
        _abs_m = re.search(
            r"(?:Abstract|ABSTRACT)\s*\n?(.{50,2000}?)(?:\n\s*\n|$)",
            text, re.DOTALL | re.IGNORECASE,
        )
        if _abs_m:
            abstract = _abs_m.group(1).strip()

    if abstract:
        abstract = re.sub(r"\s*\n\s*", " ", abstract)
        abstract = re.sub(r"\s+\d{1,3}\s*$", "", abstract)
        meta["description"] = abstract

    # --- Keywords ---
    kw_match = re.search(
        r"(?:^|\n)\s*(?:Keywords|Key\s*words|KEYWORDS)[:\s.\-—]*\n?(.*?)(?:\n\s*(?:\d+[\.\s]+|I{1,3}\.|\§)|\n\n)",
        text, re.DOTALL | re.IGNORECASE,
    )
    if kw_match:
        kw_text = kw_match.group(1).strip()
        keywords = re.split(r"[;,·•]|\s{3,}", kw_text)
        keywords = [k.strip().rstrip(".") for k in keywords if k.strip() and len(k.strip()) > 2]
        if keywords:
            meta["keywords"] = keywords

    # --- DOIs ---
    doi_pattern = re.compile(r"10\.\d{4,9}/[^\s,)\]]+")
    dois = doi_pattern.findall(text)
    if dois:
        seen = set()
        related = []
        for d in dois:
            d = d.rstrip(".")
            if d not in seen:
                seen.add(d)
                related.append({
                    "identifier": d,
                    "relation": "references",
                    "resource_type": "publication-preprint",
                })
        if related:
            meta["related_identifiers"] = related

    return meta


# ═══════════════════════════════════════════════════════════════════════════
#  Unified extractor
# ═══════════════════════════════════════════════════════════════════════════

# ---------------------------------------------------------------------------
# Default creator — override via ZENODO_AUTHOR_NAME, ZENODO_AUTHOR_AFFILIATION,
# ZENODO_AUTHOR_ORCID env vars, or edit directly here.
# ---------------------------------------------------------------------------

DEFAULT_CREATOR = {
    "name": os.environ.get("ZENODO_AUTHOR_NAME", "Last, First"),
    "affiliation": os.environ.get("ZENODO_AUTHOR_AFFILIATION", ""),
    "orcid": os.environ.get("ZENODO_AUTHOR_ORCID", ""),
}


def find_source_file(folder: str) -> Optional[Tuple[str, str]]:
    """
    Find the primary source file in a folder.
    Returns (path, type) where type is 'latex' or 'pdf'.
    Prefers .tex over .pdf (more metadata available).
    """
    folder_path = Path(folder)

    # Prefer LaTeX
    tex_files = sorted(folder_path.glob("*.tex"))
    if tex_files:
        # If multiple .tex files, prefer one with 'paper' or 'main' in the name
        for tf in tex_files:
            if any(kw in tf.stem.lower() for kw in ("paper", "main", "manuscript")):
                return str(tf), "latex"
        return str(tex_files[0]), "latex"

    # Fall back to PDF
    pdf_files = sorted(folder_path.glob("*.pdf"))
    if pdf_files:
        for pf in pdf_files:
            if any(kw in pf.stem.lower() for kw in ("paper", "main", "manuscript")):
                return str(pf), "pdf"
        return str(pdf_files[0]), "pdf"

    return None


def extract_metadata(folder: str) -> Dict[str, Any]:
    """
    Auto-extract metadata from the primary source file in a folder.

    Returns a metadata dict ready for metadata.yml generation.
    Falls back to sensible defaults for any field that can't be extracted.
    """
    source = find_source_file(folder)
    if not source:
        return _default_metadata(folder)

    file_path, file_type = source
    print(f"  📄  Extracting metadata from {Path(file_path).name} ({file_type})...")

    if file_type == "latex":
        meta = extract_from_latex(file_path)
    else:
        meta = extract_from_pdf(file_path)

    # Apply defaults for anything not extracted
    meta.setdefault("title", Path(folder).name)
    # If no abstract extracted, build a minimal one from the title
    if not meta.get("description"):
        title = meta.get("title", "")
        meta["description"] = f"{title}. (Abstract auto-generated — edit metadata.yml to refine.)"
    meta.setdefault("upload_type", "publication")
    meta.setdefault("publication_type", "preprint")
    meta.setdefault("publication_date", date.today().isoformat())
    meta.setdefault("version", "v1")
    meta.setdefault("license", "CC-BY-4.0")
    meta.setdefault("keywords", [])
    meta.setdefault("related_identifiers", [])

    # Ensure creator is present and has ORCID
    if not meta.get("creators"):
        meta["creators"] = [DEFAULT_CREATOR.copy()]
    else:
        for c in meta["creators"]:
            c.setdefault("orcid", DEFAULT_CREATOR["orcid"])
            c.setdefault("affiliation", DEFAULT_CREATOR["affiliation"])
            # Normalize name format to "Last, First" if not already
            name = c.get("name", "")
            if name and "," not in name:
                parts = name.split()
                if len(parts) >= 2:
                    c["name"] = f"{parts[-1]}, {' '.join(parts[:-1])}"

    return meta


def _default_metadata(folder: str) -> Dict[str, Any]:
    """Return minimal default metadata when no source file is found."""
    return {
        "title": Path(folder).name,
        "description": "",
        "upload_type": "publication",
        "publication_type": "preprint",
        "publication_date": date.today().isoformat(),
        "version": "v1",
        "creators": [DEFAULT_CREATOR.copy()],
        "keywords": [],
        "license": "CC-BY-4.0",
        "related_identifiers": [],
    }


def generate_metadata_yml(meta: Dict[str, Any], output_path: str) -> str:
    """
    Write a metadata.yml file from extracted metadata.
    Returns the path written.
    """
    import yaml

    # Clean up for YAML output — yaml.dump handles unicode natively
    out_path = Path(output_path)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("# Auto-generated by zenodo-drop metadata extractor\n")
        f.write(f"# Source: {meta.get('_source_file', 'unknown')}\n")
        f.write(f"# Generated: {date.today().isoformat()}\n\n")

        # Remove internal keys
        clean = {k: v for k, v in meta.items() if not k.startswith("_")}
        yaml.dump(clean, f, default_flow_style=False, allow_unicode=True, sort_keys=False)

    print(f"  ✅  metadata.yml generated → {out_path}")
    return str(out_path)
