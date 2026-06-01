"""Tests for overlay discovery and dependency graph."""

from pathlib import Path

import pytest

from kdrift import discover


@pytest.fixture()
def kustomize_repo(tmp_path):
    """Create a minimal kustomize repo structure."""
    base = tmp_path / "k8s" / "base"
    base.mkdir(parents=True)
    (base / "kustomization.yaml").write_text("resources:\n  - deployment.yaml\n  - service.yaml\n")
    (base / "deployment.yaml").write_text("kind: Deployment\n")
    (base / "service.yaml").write_text("kind: Service\n")

    dev = tmp_path / "k8s" / "dev"
    dev.mkdir()
    (dev / "kustomization.yaml").write_text("resources:\n  - ../base\npatches:\n  - path: replicas-patch.yaml\n")
    (dev / "replicas-patch.yaml").write_text("kind: Deployment\n")

    prod = tmp_path / "k8s" / "prod"
    prod.mkdir()
    (prod / "kustomization.yaml").write_text("resources:\n  - ../base\n")

    return tmp_path


@pytest.mark.unit
class TestDependencyGraph:
    def test_build_finds_overlays(self, kustomize_repo):
        graph = discover.DependencyGraph(kustomize_repo)
        graph.build()
        leaves = graph.leaf_overlays
        leaf_paths = {str(o.path) for o in leaves}
        assert "k8s/dev" in leaf_paths
        assert "k8s/prod" in leaf_paths
        assert "k8s/base" not in leaf_paths

    def test_affected_overlays_base_file(self, kustomize_repo):
        graph = discover.DependencyGraph(kustomize_repo)
        graph.build()
        affected = graph.affected_overlays([Path("k8s/base/deployment.yaml")])
        affected_paths = {str(o.path) for o in affected}
        assert "k8s/dev" in affected_paths
        assert "k8s/prod" in affected_paths

    def test_affected_overlays_overlay_patch(self, kustomize_repo):
        graph = discover.DependencyGraph(kustomize_repo)
        graph.build()
        affected = graph.affected_overlays([Path("k8s/dev/replicas-patch.yaml")])
        affected_paths = {str(o.path) for o in affected}
        assert "k8s/dev" in affected_paths
        assert "k8s/prod" not in affected_paths

    def test_affected_overlays_kustomization_change(self, kustomize_repo):
        graph = discover.DependencyGraph(kustomize_repo)
        graph.build()
        affected = graph.affected_overlays([Path("k8s/dev/kustomization.yaml")])
        affected_paths = {str(o.path) for o in affected}
        assert "k8s/dev" in affected_paths

    def test_no_kustomization_files(self, tmp_path):
        graph = discover.DependencyGraph(tmp_path)
        graph.build()
        assert graph.leaf_overlays == []

    def test_must_build_before_query(self, tmp_path):
        graph = discover.DependencyGraph(tmp_path)
        with pytest.raises(discover.DiscoveryError):
            _ = graph.leaf_overlays

    def test_empty_changed_files(self, kustomize_repo):
        graph = discover.DependencyGraph(kustomize_repo)
        graph.build()
        affected = graph.affected_overlays([])
        assert affected == []


@pytest.mark.unit
class TestParseReferences:
    def test_simple_resources(self, tmp_path):
        kust = tmp_path / "kustomization.yaml"
        kust.write_text("resources:\n  - deployment.yaml\n  - service.yaml\n")
        refs = discover._parse_references(kust, tmp_path)
        assert Path("deployment.yaml") in refs
        assert Path("service.yaml") in refs

    def test_patches_with_path(self, tmp_path):
        kust = tmp_path / "kustomization.yaml"
        kust.write_text("patches:\n  - path: my-patch.yaml\n")
        refs = discover._parse_references(kust, tmp_path)
        assert Path("my-patch.yaml") in refs

    def test_config_map_generator_files(self, tmp_path):
        kust = tmp_path / "kustomization.yaml"
        kust.write_text(
            "configMapGenerator:\n"
            "  - name: my-config\n"
            "    files:\n"
            "      - config.properties\n"
            "      - key=other.properties\n"
        )
        refs = discover._parse_references(kust, tmp_path)
        assert Path("config.properties") in refs
        assert Path("other.properties") in refs

    def test_helm_values_file(self, tmp_path):
        kust = tmp_path / "kustomization.yaml"
        kust.write_text(
            "helmCharts:\n"
            "  - name: my-chart\n"
            "    valuesFile: values.yaml\n"
            "    additionalValuesFiles:\n"
            "      - extra-values.yaml\n"
        )
        refs = discover._parse_references(kust, tmp_path)
        assert Path("values.yaml") in refs
        assert Path("extra-values.yaml") in refs

    def test_remote_refs_excluded(self, tmp_path):
        kust = tmp_path / "kustomization.yaml"
        kust.write_text("resources:\n  - https://github.com/example/repo\n  - local.yaml\n")
        refs = discover._parse_references(kust, tmp_path)
        assert len(refs) == 1
        assert Path("local.yaml") in refs

    def test_replacements_path(self, tmp_path):
        kust = tmp_path / "kustomization.yaml"
        kust.write_text("replacements:\n  - path: replacements.yaml\n")
        refs = discover._parse_references(kust, tmp_path)
        assert Path("replacements.yaml") in refs

    def test_malformed_yaml_returns_empty(self, tmp_path):
        kust = tmp_path / "kustomization.yaml"
        kust.write_text("not_a_dict")
        refs = discover._parse_references(kust, tmp_path)
        assert refs == []
