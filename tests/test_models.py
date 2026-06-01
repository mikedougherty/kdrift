"""Tests for Pydantic models."""

import pydantic
import pytest

from kdrift import models


@pytest.mark.unit
class TestResourceId:
    def test_from_manifest_core_resource(self):
        manifest = {
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": {"name": "my-config", "namespace": "default"},
        }
        rid = models.ResourceId.from_manifest(manifest)
        assert rid.group == ""
        assert rid.version == "v1"
        assert rid.kind == "ConfigMap"
        assert rid.name == "my-config"
        assert rid.namespace == "default"
        assert rid.gvk == "v1/ConfigMap"

    def test_from_manifest_apps_resource(self):
        manifest = {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {"name": "api", "namespace": "prod"},
        }
        rid = models.ResourceId.from_manifest(manifest)
        assert rid.group == "apps"
        assert rid.version == "v1"
        assert rid.gvk == "apps/v1/Deployment"

    def test_from_manifest_cluster_scoped(self):
        manifest = {
            "apiVersion": "rbac.authorization.k8s.io/v1",
            "kind": "ClusterRole",
            "metadata": {"name": "admin"},
        }
        rid = models.ResourceId.from_manifest(manifest)
        assert rid.namespace == ""
        assert rid.gvk == "rbac.authorization.k8s.io/v1/ClusterRole"

    def test_from_manifest_missing_metadata(self):
        manifest = {"apiVersion": "v1", "kind": "Namespace"}
        rid = models.ResourceId.from_manifest(manifest)
        assert rid.name == ""
        assert rid.namespace == ""

    def test_frozen(self):
        rid = models.ResourceId(kind="Pod", name="test")
        with pytest.raises(pydantic.ValidationError):
            rid.name = "changed"  # type: ignore[misc]


@pytest.mark.unit
class TestOverlayResult:
    def test_has_changes_empty(self):
        result = models.OverlayResult(path="k8s/dev")
        assert not result.has_changes

    def test_has_changes_with_changes(self):
        change = models.ResourceChange(
            resource_id=models.ResourceId(kind="ConfigMap", name="test"),
            status=models.DiffStatus.MODIFIED,
        )
        result = models.OverlayResult(path="k8s/dev", changes=[change])
        assert result.has_changes

    def test_has_error(self):
        result = models.OverlayResult(path="k8s/dev", error="build failed")
        assert result.has_error

    def test_no_error(self):
        result = models.OverlayResult(path="k8s/dev")
        assert not result.has_error


@pytest.mark.unit
class TestDiffResult:
    def test_has_changes_empty(self):
        result = models.DiffResult(ref="abc1234")
        assert not result.has_changes

    def test_has_errors_from_overlay(self):
        overlay = models.OverlayResult(path="k8s/dev", error="failed")
        result = models.DiffResult(ref="abc1234", overlays=[overlay])
        assert result.has_errors

    def test_has_errors_from_top_level(self):
        result = models.DiffResult(ref="abc1234", errors=["something went wrong"])
        assert result.has_errors


@pytest.mark.unit
class TestRenderResult:
    def test_success(self):
        result = models.RenderResult(overlay_path="k8s/dev", output="yaml: here", exit_code=0)
        assert result.success

    def test_failure(self):
        result = models.RenderResult(overlay_path="k8s/dev", error="bad yaml", exit_code=1)
        assert not result.success
