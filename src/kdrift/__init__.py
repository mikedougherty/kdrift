"""Kustomize manifest drift detection tool."""

import yaml

safe_loader: type = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
