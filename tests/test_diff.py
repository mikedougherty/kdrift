"""Tests for per-resource structured diffs."""

from pathlib import Path

import pytest

from kdrift import diff, models

BASELINE_YAML = """\
apiVersion: apps/v1
kind: Deployment
metadata:
  name: api
  namespace: prod
spec:
  replicas: 2
---
apiVersion: v1
kind: Service
metadata:
  name: api
  namespace: prod
spec:
  type: ClusterIP
"""

CANDIDATE_YAML = """\
apiVersion: apps/v1
kind: Deployment
metadata:
  name: api
  namespace: prod
spec:
  replicas: 3
---
apiVersion: v1
kind: Service
metadata:
  name: api
  namespace: prod
spec:
  type: ClusterIP
"""


@pytest.mark.unit
class TestDiffRendered:
    def test_detects_modified_resource(self):
        result = diff.diff_rendered(BASELINE_YAML, CANDIDATE_YAML, Path("k8s/dev"))
        assert len(result.changes) == 1
        change = result.changes[0]
        assert change.status == models.DiffStatus.MODIFIED
        assert change.resource_id.kind == "Deployment"
        assert change.resource_id.name == "api"
        assert change.lines_added >= 1
        assert change.lines_removed >= 1

    def test_no_changes(self):
        result = diff.diff_rendered(BASELINE_YAML, BASELINE_YAML, Path("k8s/dev"))
        assert len(result.changes) == 0

    def test_added_resource(self):
        candidate = (
            BASELINE_YAML
            + """\
---
apiVersion: v1
kind: ConfigMap
metadata:
  name: new-config
  namespace: prod
data:
  key: value
"""
        )
        result = diff.diff_rendered(BASELINE_YAML, candidate, Path("k8s/dev"))
        added = [c for c in result.changes if c.status == models.DiffStatus.ADDED]
        assert len(added) == 1
        assert added[0].resource_id.kind == "ConfigMap"
        assert added[0].resource_id.name == "new-config"

    def test_removed_resource(self):
        candidate = """\
apiVersion: apps/v1
kind: Deployment
metadata:
  name: api
  namespace: prod
spec:
  replicas: 2
"""
        result = diff.diff_rendered(BASELINE_YAML, candidate, Path("k8s/dev"))
        removed = [c for c in result.changes if c.status == models.DiffStatus.REMOVED]
        assert len(removed) == 1
        assert removed[0].resource_id.kind == "Service"

    def test_empty_baseline(self):
        result = diff.diff_rendered("", CANDIDATE_YAML, Path("k8s/dev"))
        assert all(c.status == models.DiffStatus.ADDED for c in result.changes)

    def test_empty_candidate(self):
        result = diff.diff_rendered(BASELINE_YAML, "", Path("k8s/dev"))
        assert all(c.status == models.DiffStatus.REMOVED for c in result.changes)

    def test_empty_both(self):
        result = diff.diff_rendered("", "", Path("k8s/dev"))
        assert len(result.changes) == 0


@pytest.mark.unit
class TestGeneratorAwareMatching:
    def test_matches_hash_suffixed_names(self):
        baseline = """\
apiVersion: v1
kind: ConfigMap
metadata:
  name: my-config-g4hk9dk4hg
  namespace: default
data:
  key: old
"""
        candidate = """\
apiVersion: v1
kind: ConfigMap
metadata:
  name: my-config-8kft2kbcf2
  namespace: default
data:
  key: new
"""
        result = diff.diff_rendered(baseline, candidate, Path("k8s/dev"))
        modified = [c for c in result.changes if c.status == models.DiffStatus.MODIFIED]
        assert len(modified) == 1

    def test_longest_name_first_prevents_false_match(self):
        baseline = """\
apiVersion: v1
kind: ConfigMap
metadata:
  name: dex-config-g4hk9dk4hg
  namespace: default
data:
  key: old-dex-config
---
apiVersion: v1
kind: ConfigMap
metadata:
  name: dex-kh5m9m69h9
  namespace: default
data:
  key: old-dex
"""
        candidate = """\
apiVersion: v1
kind: ConfigMap
metadata:
  name: dex-config-8kft2kbcf2
  namespace: default
data:
  key: new-dex-config
---
apiVersion: v1
kind: ConfigMap
metadata:
  name: dex-t7fbd22n6c
  namespace: default
data:
  key: new-dex
"""
        result = diff.diff_rendered(baseline, candidate, Path("k8s/dev"))
        modified = [c for c in result.changes if c.status == models.DiffStatus.MODIFIED]
        assert len(modified) == 2
        names = {c.resource_id.name for c in modified}
        assert "dex-config-8kft2kbcf2" in names
        assert "dex-t7fbd22n6c" in names


@pytest.mark.unit
class TestStripHashSuffix:
    def test_strips_real_kustomize_hash(self):
        assert diff._strip_hash_suffix("my-config-g4hk9dk4hg") == "my-config"

    def test_strips_shorter_hash(self):
        assert diff._strip_hash_suffix("my-config-g4hk9") == "my-config"

    def test_preserves_name_without_hash(self):
        assert diff._strip_hash_suffix("my-config") == "my-config"

    def test_preserves_name_with_english_suffix(self):
        assert diff._strip_hash_suffix("cert-manager") == "cert-manager"

    def test_preserves_name_with_vowels_in_suffix(self):
        assert diff._strip_hash_suffix("nginx-ingress") == "nginx-ingress"

    def test_preserves_dex_config(self):
        assert diff._strip_hash_suffix("dex-config") == "dex-config"

    def test_short_suffix_not_stripped(self):
        assert diff._strip_hash_suffix("my-config-ab") == "my-config-ab"

    def test_hash_with_only_digits(self):
        assert diff._strip_hash_suffix("my-config-24567") == "my-config"

    def test_hash_with_only_consonants(self):
        assert diff._strip_hash_suffix("my-config-bcdfg") == "my-config"
