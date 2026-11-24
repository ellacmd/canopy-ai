"""Run checksum-pinned release binaries without a global tool installation."""

import hashlib
import io
import json
import os
import platform
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]


def executable(name: str) -> Path:
    manifest = json.loads((ROOT / "scripts/tool-versions.json").read_text())
    tool = manifest[name]
    system = {"Darwin": "darwin", "Linux": "linux"}.get(platform.system())
    machine = {"x86_64": "amd64", "AMD64": "amd64", "aarch64": "arm64", "arm64": "arm64"}.get(
        platform.machine()
    )
    target = f"{system}_{machine}"
    if target not in tool["assets"]:
        raise RuntimeError("Supported tooling platforms: macOS/Linux, amd64/arm64")
    asset = tool["assets"][target]
    destination = ROOT / ".cache/tools" / name / tool["version"] / target / name
    if destination.is_file():
        return destination
    url = (
        f"https://github.com/{tool['repository']}/releases/download/"
        f"v{tool['version']}/{asset['filename']}"
    )
    with httpx.Client(timeout=60, follow_redirects=True) as client:
        response = client.get(url)
        response.raise_for_status()
        archive = response.content
    if hashlib.sha256(archive).hexdigest() != asset["sha256"]:
        raise RuntimeError("Tool download checksum mismatch; refusing to execute")
    # Extract only the expected executable, never archive-provided filesystem paths.
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as bundle:
        member = bundle.getmember(name)
        if not member.isfile():
            raise RuntimeError("Expected a regular executable in the release archive")
        source = bundle.extractfile(member)
        if source is None:
            raise RuntimeError("Missing executable in release archive")
        binary = source.read()
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as temporary:
        temporary.write(binary)
        temporary_path = Path(temporary.name)
    temporary_path.chmod(0o755)
    os.replace(temporary_path, destination)
    return destination


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] not in {"gitleaks", "actionlint"}:
        print("Usage: python3 scripts/repo_tools.py {gitleaks|actionlint} [arguments]")
        return 2
    try:
        binary = executable(sys.argv[1])
    except (OSError, RuntimeError, tarfile.TarError, httpx.HTTPError):
        print("Could not prepare the verified tool. Check network access and tool-versions.json.")
        return 2
    return subprocess.run([str(binary), *sys.argv[2:]], cwd=ROOT, check=False).returncode


if __name__ == "__main__":
    sys.exit(main())
