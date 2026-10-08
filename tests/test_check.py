"""``--check`` reports the credential source without any network call or secret value."""

import pytest

from mcp_server_minibrew import server


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for key in ("MINIBREW_ENV_FILE", "MINIBREW_TOKEN", "MINIBREW_EMAIL", "MINIBREW_PASSWORD"):
        monkeypatch.delenv(key, raising=False)


def test_no_credentials_exits_1(capsys):
    assert server._check() == 1
    out = capsys.readouterr().out
    assert "NONE set" in out


def test_email_and_password_reported_without_values(monkeypatch, capsys):
    monkeypatch.setenv("MINIBREW_EMAIL", "someone@example.com")
    monkeypatch.setenv("MINIBREW_PASSWORD", "hunter2-secret")
    assert server._check() == 0
    out = capsys.readouterr().out
    assert "email + password" in out
    assert "hunter2-secret" not in out
    assert "someone@example.com" not in out


def test_missing_env_file_is_reported(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("MINIBREW_ENV_FILE", str(tmp_path / "nope.env"))
    assert server._check() == 1
    assert "MISSING" in capsys.readouterr().out


def test_token_from_env_file(monkeypatch, tmp_path, capsys):
    env = tmp_path / ".env"
    env.write_text("MINIBREW_TOKEN=abc123secret\n")
    monkeypatch.setenv("MINIBREW_ENV_FILE", str(env))
    assert server._check() == 0
    out = capsys.readouterr().out
    assert "pasted token" in out
    assert "abc123secret" not in out
