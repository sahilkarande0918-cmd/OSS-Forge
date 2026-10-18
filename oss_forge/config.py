"""Configuration loaded from environment variables (and an optional .env file)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from pathlib import Path

SUPPORTED_MODELS = ("gemma-4-26b-a4b-it", "gemma-4-31b-it")
DEFAULT_MODEL = SUPPORTED_MODELS[0]


class ConfigError(ValueError):
    pass


def load_dotenv(path: Path) -> None:
    """Minimal .env loader: KEY=VALUE lines, no override of already-set variables."""
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and value and key not in os.environ:
            os.environ[key] = value


def _bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int, minimum: int = 1) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer, got {raw!r}") from exc
    if value < minimum:
        raise ConfigError(f"{name} must be >= {minimum}, got {value}")
    return value


def _float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    try:
        return float(raw) if raw else default
    except ValueError as exc:
        raise ConfigError(f"{name} must be a number, got {raw!r}") from exc


@dataclass(frozen=True)
class Config:
    api_key: str | None = field(default=None, repr=False)  # never printed
    model: str = DEFAULT_MODEL
    max_context_tokens: int = 12000
    max_api_calls: int = 30
    max_patch_attempts: int = 3
    dry_run: bool = True
    thinking: str = "minimal"
    timeout_seconds: int = 120
    sandbox: str = "auto"
    home: Path = Path(".oss-forge")
    price_input_per_m: float = 0.0
    price_output_per_m: float = 0.0

    @property
    def has_api_key(self) -> bool:
        return bool(self.api_key)

    def with_overrides(self, **kwargs) -> "Config":
        return replace(self, **{k: v for k, v in kwargs.items() if v is not None})

    def public_dict(self) -> dict:
        """Config safe to print or store: the API key is reduced to a presence flag."""
        return {
            "api_key_configured": self.has_api_key,
            "model": self.model,
            "max_context_tokens": self.max_context_tokens,
            "max_api_calls": self.max_api_calls,
            "max_patch_attempts": self.max_patch_attempts,
            "dry_run": self.dry_run,
            "thinking": self.thinking,
            "timeout_seconds": self.timeout_seconds,
            "sandbox": self.sandbox,
            "home": str(self.home),
        }

    @classmethod
    def from_env(cls, env_file: Path | None = Path(".env")) -> "Config":
        if env_file:
            load_dotenv(env_file)
        model = os.environ.get("OSS_FORGE_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL
        if not model.startswith("gemma-4"):
            raise ConfigError(f"OSS_FORGE_MODEL must be a hosted Gemma 4 model {SUPPORTED_MODELS}, got {model!r}")
        thinking = os.environ.get("OSS_FORGE_THINKING", "minimal").strip().lower()
        if thinking not in {"minimal", "high"}:
            raise ConfigError("OSS_FORGE_THINKING must be 'minimal' or 'high'")
        sandbox = os.environ.get("OSS_FORGE_SANDBOX", "auto").strip().lower()
        if sandbox not in {"auto", "docker", "host"}:
            raise ConfigError("OSS_FORGE_SANDBOX must be auto, docker or host")
        return cls(
            api_key=os.environ.get("GEMINI_API_KEY", "").strip() or None,
            model=model,
            max_context_tokens=_int("OSS_FORGE_MAX_CONTEXT_TOKENS", 12000, 500),
            max_api_calls=_int("OSS_FORGE_MAX_API_CALLS", 30),
            max_patch_attempts=_int("OSS_FORGE_MAX_PATCH_ATTEMPTS", 3),
            dry_run=_bool(os.environ.get("OSS_FORGE_DRY_RUN", "true")),
            thinking=thinking,
            timeout_seconds=_int("OSS_FORGE_TIMEOUT_SECONDS", 120),
            sandbox=sandbox,
            home=Path(os.environ.get("OSS_FORGE_HOME", ".oss-forge")),
            price_input_per_m=_float("OSS_FORGE_PRICE_INPUT_PER_M", 0.0),
            price_output_per_m=_float("OSS_FORGE_PRICE_OUTPUT_PER_M", 0.0),
        )
