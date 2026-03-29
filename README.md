# zenodo-drop

**No PhD. No lab. No permission. No paywall.**

Automate the entire [Zenodo](https://zenodo.org) deposit process so independent researchers never have to manually fill out 17 fields or pay $1,700 APCs again.

---

## Setup

### 1. Install dependencies

```bash
pip install requests pyyaml
```

### 2. Get your Zenodo API token

You need a personal access token from Zenodo with **`deposit:write`** and **`deposit:actions`** scopes.

| Environment | Where to create token | Token env var |
|---|---|---|
| **Sandbox** (testing) | [sandbox.zenodo.org/account/settings/applications](https://sandbox.zenodo.org/account/settings/applications/) | `ZENODO_SANDBOX_TOKEN` |
| **Live** (real DOIs) | [zenodo.org/account/settings/applications](https://zenodo.org/account/settings/applications/) | `ZENODO_TOKEN` |

> **Important:** Sandbox and live are completely separate systems with separate accounts and separate tokens. Start with sandbox to test.

### 3. Set your credentials

**Option A — Environment variables (recommended):**
```bash
# Add to your ~/.bashrc or ~/.zshrc
export ZENODO_TOKEN="your-live-token"
export ZENODO_SANDBOX_TOKEN="your-sandbox-token"

# Optional: default author for auto-extracted metadata
export ZENODO_AUTHOR_NAME="Last, First"
export ZENODO_AUTHOR_AFFILIATION="Your Institution"
export ZENODO_AUTHOR_ORCID="0000-0002-1234-5678"
```

**Option B — Dotfile:**
```bash
echo "your-token" > .zenodo_token   # Used as fallback
```

See `.env.example` for all available variables.

### 4. Create your paper folder

```
my-paper/
├── metadata.yml        ← optional (auto-generated from .tex or .pdf if missing)
├── paper.pdf           ← auto-detected and uploaded
├── paper.tex           ← auto-detected and uploaded
├── figures/
│   └── figure1.png     ← auto-detected and uploaded
└── data.csv            ← auto-detected and uploaded
```

If there's no `metadata.yml`, zenodo-drop reads your `.tex` or `.pdf` and generates one automatically — extracting title, abstract, keywords, authors, and DOIs from the bibliography.

---

## Usage

### Upload a new paper

```bash
# Preview what would be submitted (no network calls)
python cli.py preview my-paper/

# Dry run — validates everything, shows metadata, no actual upload
python cli.py upload my-paper/ --dry-run

# Upload to sandbox (default — safe mode)
python cli.py upload my-paper/

# Upload to LIVE Zenodo (real DOIs, permanent records)
python cli.py --live upload my-paper/
```

### Upload a new version of an existing paper

```bash
python cli.py --live newversion my-paper/
```

This uses the proper Zenodo `newversion` API — creates a linked revision, not a separate deposit.

### Backlink prior papers

After publishing a new paper, update your older papers to point forward to it:

```bash
python cli.py --live backlink 10.5281/zenodo.XXXXXXX \
  --records "19101554,19139178,19166572"
```

This adds `isReferencedBy` links to each record ID listed. If you accidentally include the new paper's own record ID, it's automatically skipped (no ghost drafts).

### Other commands

```bash
# Show the auto-generated series chain for a paper
python cli.py chain paper7

# Print the submitted.json ledger
python cli.py ledger
```

### Global flags

| Flag | Description |
|---|---|
| `--sandbox` | Use Zenodo sandbox (default) |
| `--live` | Use live Zenodo — real DOIs, permanent records |
| `--ledger PATH` | Custom path to submitted.json |

### Upload / newversion flags

| Flag | Description |
|---|---|
| `--dry-run` | Preview everything, no API calls |
| `--preview` | Show metadata and prompt for confirmation |
| `--no-publish` | Create draft only — don't publish |
| `--no-figures` | Skip image files |

---

## Features

- **CLI-first**: `python cli.py upload my-paper/` — one command, one DOI
- **Auto-extract metadata**: reads title, abstract, keywords, authors, and bibliography DOIs from `.tex` or `.pdf` — no manual YAML required
- **Full metadata support**: title, abstract, keywords, authors, license, related identifiers, version, communities
- **Auto-detect files**: PDF, LaTeX, CSV, Python scripts, images — anything in your paper folder gets attached
- **Series chaining**: auto-generates `references` / `isReferencedBy` links from a local ledger
- **Version bumps**: proper `newversion` flow — linked revisions, not duplicate deposits
- **Safe defaults**: sandbox by default, dry-run, preview, draft-only
- **Backlink command**: update all prior papers with forward links to a new paper, with automatic self-skip to prevent ghost drafts
- **Submission ledger**: `submitted.json` tracks DOIs, deposition IDs, versions, timestamps
- **GitHub Actions**: auto-deposit on `git tag paper7-v1`
- **Exponential backoff**: handles Zenodo 429 rate limits gracefully; non-idempotent requests (deposit creation) are never retried on server errors to prevent ghost drafts

---

## How it works

### Metadata auto-extraction

When you point zenodo-drop at a folder with no `metadata.yml`:

1. Finds the `.tex` file (preferred) or `.pdf`
2. Extracts title, abstract, keywords, author/affiliation
3. Scrapes DOIs from the bibliography → `related_identifiers`
4. Fills in defaults (license, upload type, date)
5. Writes `metadata.yml` into the folder
6. Proceeds with upload

### Series chaining

The `submitted.json` ledger tracks every paper you publish. When uploading a new paper, zenodo-drop reads the ledger and auto-generates `references` / `isReferencedBy` links so the whole series is connected — you never manually type prior DOIs.

### No ghost drafts

Creating a Zenodo deposit is a non-idempotent POST. If the server returns a 500 error but actually created the deposit, retrying would create a duplicate. zenodo-drop handles this correctly: deposit creation and new-version creation are **never retried on server errors**, only on rate limits (429). The `backlink` command auto-skips the new paper's own record ID to avoid opening unnecessary drafts.

---

## GitHub Actions

Push a tag → get a DOI.

```bash
git tag paper7-v1
git push origin paper7-v1
```

Set these secrets in your GitHub repo settings:
- `ZENODO_TOKEN` — live personal access token
- `ZENODO_SANDBOX_TOKEN` — sandbox token (for testing)

See `.github/workflows/zenodo-release.yml`.

---

## File structure

```
zenodo-drop/
├── cli.py                  # entry point
├── zenodo_api.py           # Zenodo REST API wrapper
├── metadata.py             # YAML loader + validator
├── metadata_extractor.py   # auto-extract from LaTeX/PDF
├── series_chainer.py       # auto-generate related identifiers
├── payload_builder.py      # discover + bundle files
├── templates/
│   └── metadata.yml.example
├── examples/
│   └── my-paper/
├── .env.example            # credential template
├── .github/
│   └── workflows/
│       └── zenodo-release.yml
├── requirements.txt
├── .gitignore
├── README.md
└── LICENSE
```

---

## License

[CC-BY-4.0](https://creativecommons.org/licenses/by/4.0/)
