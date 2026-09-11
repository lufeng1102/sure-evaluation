"""Node plugin protocol: the contract every evaluation node satisfies.

A node — builtin or external — is a Python module exposing a small set of
conventions (module attributes plus a ``build`` factory).  This module defines
the :class:`NodeRegistration` record produced from those conventions and the
helpers that turn a raw module into that record.

``MANIFEST`` declares the node's identity and metadata, ``build`` is the
factory for the unified callable that ``run_pipeline`` executes, and
``NODE_ENV`` declares optional runtime dependencies.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any

import yaml

from sure_eval.evaluation.core.types import KeyTextFiles, PipelineNodeResult

NodeCallable = Callable[[KeyTextFiles], tuple[KeyTextFiles, PipelineNodeResult]]
NodeFactory = Callable[..., NodeCallable]

#: entry point group name under which external nodes register.
ENTRY_POINT_GROUP = "sure_eval.nodes"


@dataclass(frozen=True)
class NodeRegistration:
    """One discovered, loadable evaluation node."""

    node_id: str
    stage: str
    version: str
    manifest: dict[str, Any]
    build: NodeFactory | None = None
    node_env: dict[str, Any] | None = None
    module: str = ""
    source: str = "builtin"
    # Optional selector hints for dynamic dispatch (used in Phase 2).
    selectors: dict[str, Any] = field(default_factory=dict)
    # Artifact keys this node reads from / writes to NodePayload.artifacts.
    consumes: tuple[str, ...] = ()
    produces: tuple[str, ...] = ()

    @property
    def name(self) -> str:
        return self.node_id.split("/", 1)[-1]


def load_manifest_dict(raw: Any, module: ModuleType) -> dict[str, Any]:
    """Resolve a ``MANIFEST`` attribute to a dict (inline dict or YAML path)."""

    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, (str, Path)):
        return _load_yaml(_resolve_module_path(raw, module))
    raise TypeError(f"MANIFEST must be a dict or a path, got {type(raw).__name__}")


def load_node_env_dict(raw: Any, module: ModuleType) -> dict[str, Any] | None:
    """Resolve a ``NODE_ENV`` attribute to a dict or ``None``."""

    if raw is None:
        return None
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, (str, Path)):
        return _load_yaml(_resolve_module_path(raw, module))
    raise TypeError(f"NODE_ENV must be None, a dict or a path, got {type(raw).__name__}")


def registration_from_module(
    module: ModuleType,
    *,
    node_id: str | None = None,
    source: str = "builtin",
) -> NodeRegistration:
    """Build a :class:`NodeRegistration` from a module following the convention.

    ``node_id`` is authoritative when given (entry point name); otherwise the
    module's ``NODE_ID`` attribute is used (local-path nodes).  If both are
    present and disagree, that is an error rather than a silent override.
    """

    module_node_id = getattr(module, "NODE_ID", None)
    effective_id = node_id or module_node_id
    if not effective_id:
        raise ValueError(
            f"Node module {module.__name__!r} provides neither an explicit "
            f"node_id nor a NODE_ID attribute"
        )
    if node_id and module_node_id and node_id != module_node_id:
        raise ValueError(
            f"Node id mismatch: entry point name {node_id!r} != module "
            f"NODE_ID {module_node_id!r}"
        )

    stage = _required_attr(module, "STAGE", effective_id)
    version = _required_attr(module, "VERSION", effective_id)
    build = getattr(module, "build", None)
    if build is not None and not callable(build):
        raise TypeError(f"Node {effective_id}: 'build' must be callable")

    manifest = load_manifest_dict(getattr(module, "MANIFEST", None) or {}, module)
    manifest.setdefault("id", effective_id)
    manifest.setdefault("version", str(version))
    manifest.setdefault("stage", str(stage))

    node_env = load_node_env_dict(getattr(module, "NODE_ENV", None), module)
    selectors = dict(getattr(module, "SELECTORS", None) or {})
    consumes = tuple(manifest.get("consumes") or ())
    produces = tuple(manifest.get("produces") or ())

    return NodeRegistration(
        node_id=str(effective_id),
        stage=str(stage),
        version=str(version),
        manifest=manifest,
        build=build,
        node_env=node_env,
        module=module.__name__,
        source=source,
        selectors=selectors,
        consumes=consumes,
        produces=produces,
    )


def _resolve_module_path(raw: str | Path, module: ModuleType) -> Path:
    path = Path(raw)
    if not path.is_absolute():
        module_file = getattr(module, "__file__", None)
        if module_file:
            path = Path(module_file).resolve().parent / path
    return path


def _required_attr(module: ModuleType, name: str, node_id: str) -> Any:
    value = getattr(module, name, None)
    if value is None:
        raise ValueError(
            f"Node {node_id}: module {module.__name__!r} is missing required "
            f"attribute {name!r}"
        )
    return value


def _load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise TypeError(f"Expected a YAML mapping at {path}")
    return data
