"""Typed configuration. Fails closed outside the local environment."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache


class ConfigError(RuntimeError):
    pass


def _env(name: str, default: str | None = None) -> str | None:
    v = os.environ.get(name)
    return v if v not in (None, "") else default


def _int(name: str, default: int) -> int:
    return int(_env(name, str(default)))  # type: ignore[arg-type]


def _float(name: str, default: float) -> float:
    return float(_env(name, str(default)))  # type: ignore[arg-type]


@dataclass(frozen=True)
class Settings:
    env: str = "local"  # local | test | staging | production
    database_url: str = "postgresql+psycopg://tensor_app:devpass@localhost/tensor"
    admin_database_url: str = "postgresql+psycopg://tensor_owner:devpass@localhost/tensor"

    # Auth
    oidc_issuer: str | None = None
    oidc_audience: str | None = None
    oidc_jwks_url: str | None = None
    dev_jwt_secret: str | None = None
    require_mfa_for_privileged: bool = True

    # Storage / ingestion
    storage_dir: str = "./var/objects"
    max_upload_bytes: int = 25 * 1024 * 1024
    upload_session_ttl_s: int = 900
    scanner: str = "dev"  # dev | clamav
    clamd_host: str = "localhost"
    clamd_port: int = 3310
    extract_timeout_s: int = 30
    extract_memory_mb: int = 512

    # AI
    ai_provider: str = "mock"  # mock | anthropic
    anthropic_api_key: str | None = None
    ai_model: str = "claude-sonnet-5-5"
    ai_timeout_s: int = 120
    ai_max_output_tokens: int = 4000
    ai_job_budget_usd: float = 0.50
    ai_tenant_daily_budget_usd: float = 20.0
    ai_price_in_per_mtok: float = 3.0
    ai_price_out_per_mtok: float = 15.0

    # Secrets
    secrets_key: str | None = None  # Fernet key for the dev/local secret store

    # Downloads
    download_ttl_s: int = 600

    # Delivery economics (from the business plan; editable)
    engineer_rate_usd: float = 9.0
    expert_rate_usd: float = 75.0
    target_hours_year1: float = 100.0
    target_hours_year2: float = 65.0

    cors_origins: list[str] = field(default_factory=list)
    static_dir: str | None = None

    @property
    def is_local(self) -> bool:
        return self.env in ("local", "test")

    def validate(self) -> None:
        if self.is_local:
            return
        missing = [n for n, v in {
            "TENSOR_OIDC_ISSUER": self.oidc_issuer,
            "TENSOR_OIDC_AUDIENCE": self.oidc_audience,
            "TENSOR_OIDC_JWKS_URL": self.oidc_jwks_url,
            "TENSOR_SECRETS_KEY": self.secrets_key,
        }.items() if not v]
        if missing:
            raise ConfigError(f"Refusing to start in {self.env}: missing {', '.join(missing)}")
        if self.dev_jwt_secret:
            raise ConfigError("TENSOR_DEV_JWT_SECRET must not be set outside local")
        if self.scanner == "dev":
            raise ConfigError("The dev malware scanner is not allowed outside local; set TENSOR_SCANNER=clamav")
        if self.ai_provider == "anthropic" and not self.anthropic_api_key:
            raise ConfigError("TENSOR_ANTHROPIC_API_KEY is required for the anthropic provider")


@lru_cache
def get_settings() -> Settings:
    env = _env("TENSOR_ENV", "local") or "local"
    s = Settings(
        env=env,
        database_url=_env("TENSOR_DATABASE_URL", Settings.database_url),  # type: ignore[arg-type]
        admin_database_url=_env("TENSOR_ADMIN_DATABASE_URL", Settings.admin_database_url),  # type: ignore[arg-type]
        oidc_issuer=_env("TENSOR_OIDC_ISSUER"),
        oidc_audience=_env("TENSOR_OIDC_AUDIENCE"),
        oidc_jwks_url=_env("TENSOR_OIDC_JWKS_URL"),
        dev_jwt_secret=_env("TENSOR_DEV_JWT_SECRET", "local-dev-only-secret-change-me-0123456789" if env in ("local", "test") else None),
        require_mfa_for_privileged=_env("TENSOR_REQUIRE_MFA", "true") == "true",
        storage_dir=_env("TENSOR_STORAGE_DIR", Settings.storage_dir),  # type: ignore[arg-type]
        max_upload_bytes=_int("TENSOR_MAX_UPLOAD_BYTES", Settings.max_upload_bytes),
        scanner=_env("TENSOR_SCANNER", "dev"),  # type: ignore[arg-type]
        clamd_host=_env("TENSOR_CLAMD_HOST", "localhost"),  # type: ignore[arg-type]
        clamd_port=_int("TENSOR_CLAMD_PORT", 3310),
        extract_timeout_s=_int("TENSOR_EXTRACT_TIMEOUT_S", 30),
        ai_provider=_env("TENSOR_AI_PROVIDER", "mock"),  # type: ignore[arg-type]
        anthropic_api_key=_env("TENSOR_ANTHROPIC_API_KEY") or _env("ANTHROPIC_API_KEY"),
        ai_model=_env("TENSOR_AI_MODEL", Settings.ai_model),  # type: ignore[arg-type]
        ai_job_budget_usd=_float("TENSOR_AI_JOB_BUDGET_USD", 0.50),
        ai_tenant_daily_budget_usd=_float("TENSOR_AI_TENANT_DAILY_BUDGET_USD", 20.0),
        ai_price_in_per_mtok=_float("TENSOR_AI_PRICE_IN_PER_MTOK", 3.0),
        ai_price_out_per_mtok=_float("TENSOR_AI_PRICE_OUT_PER_MTOK", 15.0),
        secrets_key=_env("TENSOR_SECRETS_KEY"),
        download_ttl_s=_int("TENSOR_DOWNLOAD_TTL_S", 600),
        engineer_rate_usd=_float("TENSOR_ENGINEER_RATE_USD", 9.0),
        expert_rate_usd=_float("TENSOR_EXPERT_RATE_USD", 75.0),
        cors_origins=[o for o in (_env("TENSOR_CORS_ORIGINS", "") or "").split(",") if o],
        static_dir=_env("TENSOR_STATIC_DIR"),
    )
    s.validate()
    return s
