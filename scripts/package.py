"""Build the submission zip (Capstone_Project_<name>.zip) from the files git would track.

It takes `git ls-files --cached --others --exclude-standard`, so ignored files (`.env`, `.venv`,
`logs`, caches) are never included. Before writing the zip it refuses to continue if `.env` is in
the list or if a text file contains something that looks like a key. It also writes the listing
to docs/evidence/package-listing.txt.

Run: uv run python -m scripts.package --name "Your Name" [--out ..]
"""

import argparse
import re
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KEY_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),  # pragma: allowlist secret
    re.compile(r"[ps]k-lf-[0-9a-f-]{20,}"),  # pragma: allowlist secret
]
TEXT_SUFFIXES = {
    ".py",
    ".md",
    ".txt",
    ".json",
    ".jsonl",
    ".yaml",
    ".yml",
    ".toml",
    ".csv",
    ".ps1",
    ".example",
    "",
}


def listed_files() -> list[str]:
    """Return the repo-relative paths that belong in the package (tracked plus new, not ignored)."""
    out = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return sorted(p for p in out.splitlines() if p and (ROOT / p).is_file())


def scan(paths: list[str]) -> list[str]:
    """Return problems found: a forbidden file name or a key-like string in a text file."""
    problems = []
    for p in paths:
        name = Path(p).name
        if name == ".env" or (name.startswith(".env.") and name != ".env.example"):
            problems.append(f"{p}: environment file must not be packaged")
        if Path(p).suffix.lower() in TEXT_SUFFIXES:
            text = (ROOT / p).read_text(encoding="utf-8", errors="ignore")
            if any(rx.search(text) for rx in KEY_PATTERNS):
                problems.append(f"{p}: contains a key-like string")
    return problems


def main() -> int:
    """Check the file list, write the zip and the listing, and return the exit code."""
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", required=True, help="Student name, used in the zip file name")
    ap.add_argument(
        "--out", default=str(ROOT.parent), help="Folder for the zip (default: parent of the repo)"
    )
    args = ap.parse_args()
    files = listed_files()
    problems = scan(files)
    if problems:
        print("Refusing to package:\n  " + "\n  ".join(problems))
        return 1
    safe = re.sub(r"[^A-Za-z0-9]+", "_", args.name).strip("_")
    target = Path(args.out) / f"Capstone_Project_{safe}.zip"
    if target.exists():
        print(f"{target} already exists; not overwriting.")
        return 1
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
        for p in files:
            z.write(ROOT / p, f"Capstone_Project_{safe}/{p}")
    listing = ROOT / "docs" / "evidence" / "package-listing.txt"
    listing.parent.mkdir(parents=True, exist_ok=True)
    listing.write_text(
        f"{target.name}: {len(files)} files\n" + "\n".join(files) + "\n", encoding="utf-8"
    )
    print(
        f"Wrote {target} ({len(files)} files, {target.stat().st_size:,} bytes); listing in {listing.relative_to(ROOT)}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
