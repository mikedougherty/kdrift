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

    def test_absolute_in_repo_file_matches_regular_ref(self, kustomize_repo):
        # An absolute path to a regular in-repo resource must map the same as
        # its repo-relative form (regression guard for the abs/relative asymmetry).
        graph = discover.DependencyGraph(kustomize_repo)
        graph.build()
        abs_path = kustomize_repo / "k8s" / "base" / "deployment.yaml"
        affected = graph.affected_overlays([abs_path])
        affected_paths = {str(o.path) for o in affected}
        assert "k8s/dev" in affected_paths
        assert "k8s/prod" in affected_paths


@pytest.mark.unit
class TestChartSubtreeWatches:
    def _write_local_chart(self, chart_dir: Path) -> None:
        chart_dir.mkdir(parents=True)
        (chart_dir / "Chart.yaml").write_text("apiVersion: v2\nname: c\nversion: 0.1.0\n")
        (chart_dir / "templates").mkdir()
        (chart_dir / "templates" / "deploy.yaml").write_text("kind: Deployment\n")

    def test_in_repo_chart_file_change_affects_overlay(self, tmp_path):
        app = tmp_path / "app"
        app.mkdir()
        (app / "kustomization.yaml").write_text("helmCharts:\n  - name: mychart\n")
        self._write_local_chart(app / "charts" / "mychart")

        graph = discover.DependencyGraph(tmp_path)
        graph.build()

        affected = graph.affected_overlays([Path("app/charts/mychart/templates/deploy.yaml")])
        assert {str(o.path) for o in affected} == {"app"}

    def test_out_of_repo_absolute_chart_change_affects_overlay(self, tmp_path):
        repo = tmp_path / "repo"
        app = repo / "app"
        app.mkdir(parents=True)
        ext_home = tmp_path / "ext"
        (app / "kustomization.yaml").write_text(
            f"helmGlobals:\n  chartHome: {ext_home}\nhelmCharts:\n  - name: mychart\n"
        )
        self._write_local_chart(ext_home / "mychart")

        graph = discover.DependencyGraph(repo)
        graph.build()

        changed = (ext_home / "mychart" / "templates" / "deploy.yaml").resolve()
        affected = graph.affected_overlays([changed])
        assert {str(o.path) for o in affected} == {"app"}

    def test_external_sources_reports_out_of_repo_dep(self, tmp_path):
        repo = tmp_path / "repo"
        app = repo / "app"
        app.mkdir(parents=True)
        ext_home = tmp_path / "ext"
        (app / "kustomization.yaml").write_text(
            f"helmGlobals:\n  chartHome: {ext_home}\nhelmCharts:\n  - name: mychart\n"
        )
        self._write_local_chart(ext_home / "mychart")

        graph = discover.DependencyGraph(repo)
        graph.build()

        external = graph.external_sources(graph.leaf_overlays)
        assert (ext_home / "mychart").resolve() in external

    def test_in_repo_chart_has_no_external_sources(self, tmp_path):
        app = tmp_path / "app"
        app.mkdir()
        (app / "kustomization.yaml").write_text("helmCharts:\n  - name: mychart\n")
        self._write_local_chart(app / "charts" / "mychart")

        graph = discover.DependencyGraph(tmp_path)
        graph.build()

        assert graph.external_sources(graph.leaf_overlays) == []


@pytest.mark.unit
class TestParseReferences:
    def test_simple_resources(self, tmp_path):
        kust = tmp_path / "kustomization.yaml"
        kust.write_text("resources:\n  - deployment.yaml\n  - service.yaml\n")
        refs = discover._parse_references(kust, tmp_path)
        assert Path("deployment.yaml") in refs.files
        assert Path("service.yaml") in refs.files

    def test_patches_with_path(self, tmp_path):
        kust = tmp_path / "kustomization.yaml"
        kust.write_text("patches:\n  - path: my-patch.yaml\n")
        refs = discover._parse_references(kust, tmp_path)
        assert Path("my-patch.yaml") in refs.files

    def test_config_map_generator_files(self, tmp_path):
        kust = tmp_path / "kustomization.yaml"
        kust.write_text(
            "configMapGenerator:\n"
            "  - name: my-config\n"
            "    files:\n"
            "      - config.properties\n"
            "      - key=other.properties\n"
            "    env: single.env\n"
        )
        refs = discover._parse_references(kust, tmp_path)
        assert Path("config.properties") in refs.files
        assert Path("other.properties") in refs.files
        assert Path("single.env") in refs.files

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
        assert Path("values.yaml") in refs.files
        assert Path("extra-values.yaml") in refs.files

    def test_helm_chart_dir_default_chart_home(self, tmp_path):
        kust = tmp_path / "kustomization.yaml"
        kust.write_text("helmCharts:\n  - name: my-chart\n")
        refs = discover._parse_references(kust, tmp_path)
        # Default chartHome is "charts", so the chart dir is charts/my-chart.
        assert Path("charts/my-chart") in refs.subtrees

    def test_helm_chart_dir_custom_relative_chart_home(self, tmp_path):
        kust = tmp_path / "kustomization.yaml"
        kust.write_text("helmGlobals:\n  chartHome: vendor\nhelmCharts:\n  - name: my-chart\n")
        refs = discover._parse_references(kust, tmp_path)
        assert Path("vendor/my-chart") in refs.subtrees

    def test_helm_chart_dir_absolute_chart_home_is_external(self, tmp_path):
        ext = tmp_path.parent / "ext-charts-home"
        kust = tmp_path / "kustomization.yaml"
        kust.write_text(f"helmGlobals:\n  chartHome: {ext}\nhelmCharts:\n  - name: my-chart\n")
        refs = discover._parse_references(kust, tmp_path)
        expected = (ext / "my-chart").resolve()
        assert expected in refs.subtrees
        assert expected.is_absolute()

    def test_helm_chart_dir_remote_chart_home_untracked(self, tmp_path):
        kust = tmp_path / "kustomization.yaml"
        kust.write_text("helmGlobals:\n  chartHome: https://example.com/charts\nhelmCharts:\n  - name: my-chart\n")
        refs = discover._parse_references(kust, tmp_path)
        assert refs.subtrees == []

    def test_helm_multiple_charts_share_chart_home(self, tmp_path):
        kust = tmp_path / "kustomization.yaml"
        kust.write_text("helmCharts:\n  - name: chart-a\n  - name: chart-b\n")
        refs = discover._parse_references(kust, tmp_path)
        assert Path("charts/chart-a") in refs.subtrees
        assert Path("charts/chart-b") in refs.subtrees

    def test_helm_chart_dir_symlink_followed_to_real_path(self, tmp_path):
        repo = tmp_path / "repo"
        app = repo / "app"
        (app / "charts").mkdir(parents=True)
        ext_chart = tmp_path / "ext" / "my-chart"
        ext_chart.mkdir(parents=True)
        (app / "charts" / "my-chart").symlink_to(ext_chart)
        kust = app / "kustomization.yaml"
        kust.write_text("helmCharts:\n  - name: my-chart\n")
        refs = discover._parse_references(kust, repo)
        # The symlink escapes the repo, so the resolved real path is tracked absolute.
        assert ext_chart.resolve() in refs.subtrees

    def test_secret_generator_env_singular(self, tmp_path):
        kust = tmp_path / "kustomization.yaml"
        kust.write_text("secretGenerator:\n  - name: s\n    env: secret.env\n")
        refs = discover._parse_references(kust, tmp_path)
        assert Path("secret.env") in refs.files

    def test_generators_transformers_configurations_crds(self, tmp_path):
        kust = tmp_path / "kustomization.yaml"
        kust.write_text(
            "generators:\n  - gen.yaml\n"
            "transformers:\n  - transform.yaml\n"
            "configurations:\n  - config.yaml\n"
            "crds:\n  - my-crd.yaml\n"
        )
        refs = discover._parse_references(kust, tmp_path)
        for expected in ("gen.yaml", "transform.yaml", "config.yaml", "my-crd.yaml"):
            assert Path(expected) in refs.files

    def test_openapi_path(self, tmp_path):
        kust = tmp_path / "kustomization.yaml"
        kust.write_text("openapi:\n  path: schema.json\n")
        refs = discover._parse_references(kust, tmp_path)
        assert Path("schema.json") in refs.files

    def test_remote_refs_excluded(self, tmp_path):
        kust = tmp_path / "kustomization.yaml"
        kust.write_text("resources:\n  - https://github.com/example/repo\n  - local.yaml\n")
        refs = discover._parse_references(kust, tmp_path)
        assert refs.files == [Path("local.yaml")]

    def test_replacements_path(self, tmp_path):
        kust = tmp_path / "kustomization.yaml"
        kust.write_text("replacements:\n  - path: replacements.yaml\n")
        refs = discover._parse_references(kust, tmp_path)
        assert Path("replacements.yaml") in refs.files

    def test_malformed_yaml_returns_empty(self, tmp_path):
        kust = tmp_path / "kustomization.yaml"
        kust.write_text("not_a_dict")
        refs = discover._parse_references(kust, tmp_path)
        assert refs.files == []
        assert refs.subtrees == []
