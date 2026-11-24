from pathlib import Path

from dotenv import dotenv_values

from canopy.local_setup import update_env


def test_local_config_update_preserves_existing_gemini_key_and_restricts_permissions(tmp_path):
    path = tmp_path / ".env"
    path.write_text(
        "# Existing configuration\nGEMINI_API_KEY='do-not-change'\nDATABASE_URL=sqlite:///old.db\n"
    )
    update_env(
        path,
        {
            "DATABASE_URL": "postgresql+psycopg://postgres:password@127.0.0.1:54332/postgres",
            "SUPABASE_URL": "http://127.0.0.1:54331",
        },
    )
    values = dotenv_values(path)
    assert values["GEMINI_API_KEY"] == "do-not-change"
    assert values["DATABASE_URL"].startswith("postgresql+psycopg://")
    assert values["SUPABASE_URL"] == "http://127.0.0.1:54331"
    assert path.stat().st_mode & 0o777 == 0o600
    assert "# Existing configuration" in path.read_text()
    assert list(Path(tmp_path).glob(".env-setup-*")) == []
