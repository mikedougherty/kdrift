"""Application configuration via environment variables."""

import pydantic_settings


class AppConfig(pydantic_settings.BaseSettings):
    """Application configuration loaded from environment variables.

    Customize fields and env_prefix for your project. Credential fields
    use SecretStr to prevent exposure in logs and tracebacks.
    """

    model_config = pydantic_settings.SettingsConfigDict(
        env_prefix="APP_",
        frozen=True,
    )

    log_level: str = "INFO"
    log_format: str = "json"
