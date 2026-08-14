"""Application configuration."""

from __future__ import annotations

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    wafpass_api_base_url: str = "http://localhost:8000"
    wafpass_token_mode: str = "introspection"  # "introspection" | "jwt_secret"
    wafpass_jwt_secret: str = ""
    wafpass_refresh_token: str = ""
    wafpass_refresh_threshold_seconds: int = 300

    mcp_host: str = "0.0.0.0"
    mcp_port: int = 3001

    log_level: str = "INFO"

    @field_validator("wafpass_token_mode")
    @classmethod
    def _validate_token_mode(cls, value: str) -> str:
        value = value.lower()
        if value not in {"introspection", "jwt_secret"}:
            raise ValueError(
                "wafpass_token_mode must be 'introspection' or 'jwt_secret'"
            )
        return value


settings = Settings()
