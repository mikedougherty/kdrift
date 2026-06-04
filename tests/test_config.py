"""Tests for application configuration."""

import pydantic
import pytest

from kdrift import config


@pytest.mark.unit
class TestAppConfig:
    def test_defaults(self):
        cfg = config.AppConfig()
        assert cfg.log_level == "INFO"
        assert cfg.log_format == "json"
        assert cfg.external_diff is None

    def test_from_env(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("KDRIFT_LOG_LEVEL", "DEBUG")
        cfg = config.AppConfig()
        assert cfg.log_level == "DEBUG"

    def test_external_diff_from_env(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("KDRIFT_EXTERNAL_DIFF", "dyff")
        cfg = config.AppConfig()
        assert cfg.external_diff == "dyff"

    def test_frozen(self):
        cfg = config.AppConfig()
        with pytest.raises(pydantic.ValidationError):
            cfg.log_level = "DEBUG"  # type: ignore[misc]


@pytest.mark.unit
class TestProjectConfig:
    def test_defaults(self):
        cfg = config.ProjectConfig()
        assert "--enable-helm" in cfg.kustomize_args
        assert cfg.kustomize_binary is None

    def test_load_no_config_files(self, tmp_path):
        cfg = config.load_project_config(tmp_path)
        assert "--enable-helm" in cfg.kustomize_args

    def test_load_project_config_file(self, tmp_path):
        kdrift_yaml = tmp_path / ".kdrift.yaml"
        kdrift_yaml.write_text("kustomize_args:\n  - --enable-helm\n")
        cfg = config.load_project_config(tmp_path)
        assert cfg.kustomize_args == ["--enable-helm"]

    def test_walk_up_config(self, tmp_path):
        parent_config = tmp_path / ".kdrift.yaml"
        parent_config.write_text("kustomize_binary: /usr/local/bin/kustomize\n")
        child = tmp_path / "sub" / "dir"
        child.mkdir(parents=True)
        cfg = config.load_project_config(child)
        assert cfg.kustomize_binary == "/usr/local/bin/kustomize"

    def test_most_specific_wins(self, tmp_path):
        parent_config = tmp_path / ".kdrift.yaml"
        parent_config.write_text("kustomize_args:\n  - --parent-flag\n")
        child = tmp_path / "project"
        child.mkdir()
        child_config = child / ".kdrift.yaml"
        child_config.write_text("kustomize_args:\n  - --child-flag\n")
        cfg = config.load_project_config(child)
        assert cfg.kustomize_args == ["--child-flag"]

    def test_env_field_defaults_empty(self):
        cfg = config.ProjectConfig()
        assert cfg.env == {}

    def test_env_field_from_yaml(self, tmp_path):
        kdrift_yaml = tmp_path / ".kdrift.yaml"
        kdrift_yaml.write_text("env:\n  HELM_TOKEN: abc123\n  SOPS_KEY: /path/to/key\n")
        cfg = config.load_project_config(tmp_path)
        assert cfg.env == {"HELM_TOKEN": "abc123", "SOPS_KEY": "/path/to/key"}


@pytest.mark.unit
class TestResolveProjectConfig:
    def test_no_overrides(self):
        yaml_cfg = config.ProjectConfig()
        resolved = config.resolve_project_config(yaml_cfg, environ={})
        assert resolved.kustomize_args == yaml_cfg.kustomize_args
        assert resolved.kustomize_binary is None
        assert resolved.env == {}

    def test_kustomize_binary_override(self):
        yaml_cfg = config.ProjectConfig(kustomize_binary="/yaml/path")
        resolved = config.resolve_project_config(yaml_cfg, environ={"KDRIFT_KUSTOMIZE_BINARY": "/env/path"})
        assert resolved.kustomize_binary == "/env/path"

    def test_kustomize_args_override(self):
        yaml_cfg = config.ProjectConfig(kustomize_args=["--yaml-flag"])
        resolved = config.resolve_project_config(yaml_cfg, environ={"KDRIFT_KUSTOMIZE_ARGS": "--env-flag --other-flag"})
        assert resolved.kustomize_args == ["--env-flag", "--other-flag"]

    def test_kustomize_args_shlex_parsing(self):
        resolved = config.resolve_project_config(
            config.ProjectConfig(),
            environ={"KDRIFT_KUSTOMIZE_ARGS": '--set "key=value with spaces"'},
        )
        assert resolved.kustomize_args == ["--set", "key=value with spaces"]

    def test_kustomize_env_prefix_stripping(self):
        resolved = config.resolve_project_config(
            config.ProjectConfig(),
            environ={
                "KDRIFT_KUSTOMIZE_ENV_HELM_TOKEN": "secret",
                "KDRIFT_KUSTOMIZE_ENV_DEBUG": "1",
                "KDRIFT_LOG_LEVEL": "DEBUG",
                "HOME": "/home/user",
            },
        )
        assert resolved.env == {"HELM_TOKEN": "secret", "DEBUG": "1"}

    def test_env_var_overrides_yaml_env(self):
        yaml_cfg = config.ProjectConfig(env={"HELM_TOKEN": "from-yaml", "OTHER": "kept"})
        resolved = config.resolve_project_config(yaml_cfg, environ={"KDRIFT_KUSTOMIZE_ENV_HELM_TOKEN": "from-env"})
        assert resolved.env == {"HELM_TOKEN": "from-env", "OTHER": "kept"}

    def test_yaml_config_unchanged_when_no_overrides(self):
        yaml_cfg = config.ProjectConfig(
            kustomize_args=["--flag"],
            kustomize_binary="/bin/kustomize",
            env={"KEY": "val"},
        )
        resolved = config.resolve_project_config(yaml_cfg, environ={})
        assert resolved is yaml_cfg
