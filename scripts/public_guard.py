"""Reject private files and copies of local credentials in the Git index."""

import subprocess
import sys
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
PRIVATE_PARTS = {
    ".local",
    ".cache",
    ".openai",
    ".venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".temp",
    ".branches",
}
PRIVATE_FILES = {"supabase/cloud.json", "backend/.supabase-bootstrap.local.json", ".DS_Store"}
PRIVATE_SUFFIXES = {".db", ".sqlite", ".sqlite3", ".pem", ".key", ".p12", ".pfx", ".log"}


def git(root: Path, *args: str) -> bytes:
    return subprocess.check_output(["git", *args], cwd=root)


def local_credentials(root: Path) -> set[bytes]:
    # Values stay in memory and are never printed. CI has no such local files.
    values = set()
    for filename in ("backend/.env", "backend/.env.demo", "backend/.env.supabase-local"):
        path = root / filename
        if not path.is_file():
            continue
        for line in path.read_bytes().splitlines():
            key, separator, value = line.partition(b"=")
            if not separator or key.lstrip().startswith(b"#"):
                continue
            if any(
                marker in key.upper() for marker in (b"KEY", b"PASSWORD", b"TOKEN", b"DATABASE_URL")
            ):
                value = value.strip().strip(b"\"'")
                if len(value) >= 16:
                    values.add(value)
    return values


def check_index(root: Path, *, staged_only=False) -> list[str]:
    args = (
        ("diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z")
        if staged_only
        else ("ls-files", "-z")
    )
    paths = git(root, *args).split(b"\0")
    credentials = local_credentials(root)
    rejected = []
    for raw_path in filter(None, paths):
        name = raw_path.decode("utf-8", errors="surrogateescape")
        path = PurePosixPath(name)
        if (
            set(path.parts) & PRIVATE_PARTS
            or name in PRIVATE_FILES
            or path.name in PRIVATE_FILES
            or (path.name.startswith(".env") and path.name != ".env.example")
            or path.suffix.lower() in PRIVATE_SUFFIXES
        ):
            rejected.append(name)
            continue
        content = git(root, "show", ":" + name)
        if any(value in content for value in credentials):
            rejected.append(name)
    return sorted(set(rejected))


def main() -> int:
    rejected = check_index(ROOT, staged_only="--staged" in sys.argv)
    if rejected:
        print("Private files or local credential copies found in the Git index:")
        for filename in rejected:
            print(f"  {filename}")
        print("Unstage these files and remove credentials before committing.")
        return 1
    print("Public repository guard passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
