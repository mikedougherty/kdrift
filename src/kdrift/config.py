"""Configuration hierarchy: .kdrift.yaml (project > org > user)."""

from __future__ import annotations

import os
from pathlib import Path

import pydantic
import pydantic_settings
import yaml

from kdrift import safe_loader


class AppConfig(pydantic_settings.BaseSettings):
    """Application configuration loaded from environment variables."""

    model_config = pydantic_settings.SettingsConfigDict(
        env_prefix="KDRIFT_",
        frozen=True,
    )

    log_level: str = "INFO"
    log_format: str = "json"
    external_diff: str | None = None


class ProjectConfig(pydantic.BaseModel):
    """Configuration from .kdrift.yaml files."""

    model_config = pydantic.ConfigDict(frozen=True)

    kustomize_args: list[str] = pydantic.Field(
        default_factory=lambda: [
            "--enable-helm",
            "--load-restrictor",
            "LoadRestrictionsNone",
        ]
    )
    kustomize_binary: str | None = None


def load_project_config(start_dir: Path | None = None) -> ProjectConfig:
    """Load project config by walking upward from start_dir.

    Searches for .kdrift.yaml files from start_dir up to filesystem root,
    then checks the user-level XDG config. More specific files override
    less specific ones (per key).
    """
    configs: list[dict[str, object]] = []

    user_config = _user_config_path()
    if user_config.is_file():
        data = _load_yaml(user_config)
        if data:
            configs.append(data)

    start = start_dir or Path.cwd()
    path_configs = _walk_up_configs(start)
    configs.extend(reversed(path_configs))

    if not configs:
        return ProjectConfig()

    merged: dict[str, object] = {}
    for cfg in configs:
        merged.update(cfg)

    return ProjectConfig.model_validate(merged)


def _walk_up_configs(start: Path) -> list[dict[str, object]]:
    """Walk upward from start collecting .kdrift.yaml files (most specific first)."""
    configs: list[dict[str, object]] = []
    current = start.resolve()

    while True:
        cfg_file = current / ".kdrift.yaml"
        if cfg_file.is_file():
            data = _load_yaml(cfg_file)
            if data:
                configs.append(data)
        parent = current.parent
        if parent == current:
            break
        current = parent

    return configs


def _user_config_path() -> Path:
    """Get the user-level config path (XDG_CONFIG_HOME/kdrift/config.yaml)."""
    xdg = os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))
    return Path(xdg) / "kdrift" / "config.yaml"


def _load_yaml(path: Path) -> dict[str, object] | None:
    """Load a YAML file, returning None on error."""
    try:
        with path.open() as f:
            data = yaml.load(f, Loader=safe_loader)
        if isinstance(data, dict):
            return data
    except (yaml.YAMLError, OSError):
        pass
    return None
