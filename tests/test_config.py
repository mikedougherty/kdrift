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
