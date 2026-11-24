"""The index guard must reject forced additions and copied local credentials."""

import runpy
import subprocess
from pathlib import Path

import pytest

GUARD = runpy.run_path(str(Path(__file__).resolve().parents[2] / "scripts/public_guard.py"))


@pytest.mark.parametrize(
    "private", ["backend/.env.demo", ".local/HANDOFF.md", "supabase/cloud.json"]
)
def test_guard_rejects_private_files_even_if_force_added(tmp_path, private):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    file = tmp_path / private
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text("synthetic private fixture\n")
    subprocess.run(["git", "add", "--force", private], cwd=tmp_path, check=True)
    assert GUARD["check_index"](tmp_path, staged_only=True) == [private]


def test_guard_checks_staged_content_and_never_reports_secret_values(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "backend").mkdir()
    secret = "synthetic-" + "s" * 40
    (tmp_path / "backend/.env").write_text("GEMINI_API_KEY=" + secret + "\n")
    source = tmp_path / "example.py"
    source.write_text("value = '" + secret + "'\n")
    subprocess.run(["git", "add", "example.py"], cwd=tmp_path, check=True)
    # Cleaning the working tree does not hide a credential already in the index.
    source.write_text("value = 'safe-placeholder'\n")
    assert GUARD["check_index"](tmp_path) == ["example.py"]
    subprocess.run(["git", "add", "example.py"], cwd=tmp_path, check=True)
    assert GUARD["check_index"](tmp_path) == []


def test_guard_allows_configuration_template(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / ".env.example").write_text("GEMINI_API_KEY=\n")
    subprocess.run(["git", "add", ".env.example"], cwd=tmp_path, check=True)
    assert GUARD["check_index"](tmp_path) == []
