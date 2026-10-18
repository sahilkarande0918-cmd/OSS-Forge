from pathlib import Path

import pytest
from conftest import FakeClient

from oss_forge.ai.gemma_provider import BudgetExceeded, GemmaProvider, ProviderError, extract_json
from oss_forge.ai.schemas import Ping
from oss_forge.config import Config, ConfigError
from oss_forge.security.execution_policy import (PolicyViolation, TestSandbox, redact, run_command, safe_path,
                                                 scrubbed_env)
from oss_forge.skill_validator import validate_skill

REPO = Path(__file__).resolve().parents[2]


def test_config_from_env(monkeypatch, tmp_path):
    monkeypatch.setenv("GEMINI_API_KEY", "abc12345678")
    monkeypatch.setenv("OSS_FORGE_MAX_API_CALLS", "7")
    monkeypatch.setenv("OSS_FORGE_DRY_RUN", "false")
    cfg = Config.from_env(env_file=None)
    assert cfg.max_api_calls == 7 and cfg.dry_run is False
    assert "abc12345678" not in repr(cfg) and "abc12345678" not in str(cfg.public_dict())
    monkeypatch.setenv("OSS_FORGE_MODEL", "gemini-2.5-pro")
    with pytest.raises(ConfigError):
        Config.from_env(env_file=None)


def test_redaction():
    text = "key AIzaSyA1234567890abcdefghijklmnopqrstu and gho_abcdefghijklmnopqrstuvwx\nAPI_TOKEN=hunter2secret"
    out = redact(text, ("hunter2secret",))
    assert "AIza" not in out and "gho_" not in out and "hunter2secret" not in out


def test_safe_path(tmp_path):
    (tmp_path / "a.py").write_text("x")
    assert safe_path(tmp_path, "a.py") == (tmp_path / "a.py").resolve()
    for bad in ("../x.py", "/etc/passwd", "C:/Windows/x", ".env", "keys/server.pem"):
        with pytest.raises(PolicyViolation):
            safe_path(tmp_path, bad)


def test_scrubbed_env_and_allowlist(monkeypatch, tmp_path):
    monkeypatch.setenv("GEMINI_API_KEY", "secret-value-123")
    assert "GEMINI_API_KEY" not in scrubbed_env()
    with pytest.raises(PolicyViolation):
        run_command(["curl", "http://example.com"], tmp_path)


def test_sandbox_blocks_without_isolation_or_approval(monkeypatch, tmp_path):
    monkeypatch.setattr("oss_forge.security.execution_policy.docker_available", lambda: False)
    result = TestSandbox("auto", allow_host_exec=False).run_pytest(tmp_path, [])
    assert result.runner == "blocked" and result.exit_code == -2


def test_extract_json_variants():
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json('Sure! {"a": {"b": 2}} trailing') == {"a": {"b": 2}}


def test_provider_json_repair_and_usage(config):
    client = FakeClient(["not json at all", {"ok": True, "message": "hi"}])
    provider = GemmaProvider(config, client=client)
    assert provider.generate_json("t", "ping", Ping).ok
    usage = provider.usage()
    assert usage["api_calls"] == 2 and usage["prompt_tokens"] > 0 and usage["reported_by_api"]


def test_provider_retries_transient(monkeypatch, config):
    monkeypatch.setattr("time.sleep", lambda s: None)
    err = Exception("503 UNAVAILABLE")
    err.code = 503
    provider = GemmaProvider(config, client=FakeClient([err, {"ok": True}]))
    assert provider.generate_json("t", "ping", Ping).ok
    assert provider.ledger.calls[0].attempts == 2


def test_provider_auth_error_not_retried(config):
    err = Exception("API key not valid test-key-not-real")
    err.code = 403
    provider = GemmaProvider(config, client=FakeClient([err]))
    with pytest.raises(ProviderError) as info:
        provider.generate_json("t", "ping", Ping)
    assert "test-key-not-real" not in str(info.value)


def test_provider_budgets(config):
    provider = GemmaProvider(config.with_overrides(max_api_calls=1), client=FakeClient([{"ok": True}]))
    provider.generate_json("t", "ping", Ping)
    with pytest.raises(BudgetExceeded):
        provider.generate_json("t", "ping", Ping)
    small = GemmaProvider(config.with_overrides(max_context_tokens=500), client=FakeClient([]))
    with pytest.raises(BudgetExceeded):
        small.generate_json("t", "x" * 10000, Ping)


def test_provider_requires_key_and_gemma(config):
    with pytest.raises(ProviderError):
        GemmaProvider(config.with_overrides(model="gpt-4o"))
    with pytest.raises(ProviderError):
        GemmaProvider(Config(api_key=None)).client


def test_bundled_skill_is_valid():
    assert validate_skill(REPO / ".agents/skills/oss-forge") == []


def test_validator_rejects_bad_skill(tmp_path):
    d = tmp_path / "my-skill"
    d.mkdir()
    (d / "SKILL.md").write_text("---\nname: Other--Name\ndescription: x\nfoo: bar\n---\nbody\n")
    errors = validate_skill(d)
    assert any("unknown" in e for e in errors) and any("name" in e for e in errors)
