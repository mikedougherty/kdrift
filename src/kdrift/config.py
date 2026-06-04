"""Configuration hierarchy: .kdrift.yaml (project > org > user) + env overrides."""

from __future__ import annotations

import os
import shlex
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
    env: dict[str, str] = pydantic.Field(default_factory=dict)


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


def resolve_project_config(
    yaml_config: ProjectConfig,
    environ: dict[str, str] | None = None,
) -> ProjectConfig:
    """Merge .kdrift.yaml config with KDRIFT_KUSTOMIZE_* env var overrides.

    Args:
        yaml_config: Config loaded from .kdrift.yaml files.
        environ: Environment to scan (defaults to os.environ).

    Returns:
        Resolved ProjectConfig with env var overrides applied.
    """
    env = environ if environ is not None else dict(os.environ)
    overrides: dict[str, object] = {}

    if "KDRIFT_KUSTOMIZE_BINARY" in env:
        overrides["kustomize_binary"] = env["KDRIFT_KUSTOMIZE_BINARY"]

    if "KDRIFT_KUSTOMIZE_ARGS" in env:
        overrides["kustomize_args"] = shlex.split(env["KDRIFT_KUSTOMIZE_ARGS"])

    kustomize_env = dict(yaml_config.env)
    prefix = "KDRIFT_KUSTOMIZE_ENV_"
    for key, value in env.items():
        if key.startswith(prefix):
            kustomize_env[key[len(prefix) :]] = value
    if kustomize_env != yaml_config.env:
        overrides["env"] = kustomize_env

    if not overrides:
        return yaml_config

    return yaml_config.model_copy(update=overrides)


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
