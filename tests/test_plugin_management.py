"""Tests for project-local plugin declarations and runtime injection."""

from __future__ import annotations

import json
import importlib.metadata
import shutil
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from sure_eval.cli import app
from sure_eval.evaluation import node_registry as node_registry_module
from sure_eval.evaluation.node_registry import get_registry
from sure_eval.evaluation.plugin_management import (
    PluginError,
    add_plugin,
    inspect_plugin,
    plugin_records,
    remove_plugin,
    sync_plugins,
)
from sure_eval.evaluation.scripts.contracts import load_task_routes

REF_FILE = str(Path(__file__).resolve().parent.parent / "examples" / "readme" / "asr_en_ref.txt")
HYP_FILE = str(Path(__file__).resolve().parent.parent / "examples" / "readme" / "asr_en_hyp.txt")
ROOT = Path(__file__).resolve().parent.parent


def _write_node(
    path: Path,
    node_id: str = "normalization/project_norm",
    *,
    default_for: bool = False,
) -> None:
    name = node_id.split("/", 1)[-1]
    profiles = ', "profiles": {"default": {"default_for": ["asr/en/wer"]}}' if default_for else ""
    path.write_text(
        f'NODE_ID = "{node_id}"\n'
        'STAGE = "normalization"\n'
        'VERSION = "v1"\n'
        f'MANIFEST = {{"id": NODE_ID, "stage": STAGE, "version": VERSION{profiles}}}\n'
        f'SELECTORS = {{"normalizer": "{name}"}}\n'
        "from sure_eval.evaluation.core.types import PipelineNodeResult\n"
        "def build(**config):\n"
        "    def process(files):\n"
        "        return files, PipelineNodeResult(stage=STAGE, node_id=NODE_ID, version=VERSION, details={})\n"
        "    return process\n",
        encoding="utf-8",
    )


def _write_route(
    path: Path,
    pipeline_id: str = "asr.en.cer.aispeech_norm_en_v1.wenet_cer_v1",
    nodes: tuple[str, ...] = ("normalization/aispeech_norm", "scoring/wenet_cer"),
) -> None:
    nodes_json = ", ".join(json.dumps(node) for node in nodes)
    path.write_text(
        "ROUTES = [{\n"
        '  "language": "en",\n'
        '  "metric": "cer",\n'
        f'  "pipeline_id": "{pipeline_id}",\n'
        f'  "nodes": [{nodes_json}],\n'
        '  "input_contract": "scoring/wenet_cer",\n'
        '  "executor": "sure_eval.evaluation.tasks.asr.pipeline.evaluate_asr_files"\n'
        "}]\n",
        encoding="utf-8",
    )


def _write_package_plugin(
    root: Path,
    module_name: str,
    *,
    node_id: str | None = None,
    pipeline_id: str | None = None,
    with_pyproject: bool = True,
) -> None:
    package = root / "src" / module_name
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    manifest: dict[str, object] = {
        "name": root.name,
        "plugin_api": "sure-eval.plugin.v1",
        "package": {"module": module_name},
    }
    entry_points: list[str] = []
    if node_id:
        _write_node(package / "node.py", node_id)
        manifest["node"] = {"module": f"{module_name}.node"}
        entry_points.extend(
            [
                '[project.entry-points."sure_eval.nodes"]',
                f'"{node_id}" = "{module_name}.node"',
                "",
            ]
        )
    if pipeline_id:
        nodes = (
            (node_id, "scoring/wenet_cer")
            if node_id
            else (
                "normalization/aispeech_norm",
                "scoring/wenet_cer",
            )
        )
        _write_route(package / "routes.py", pipeline_id, nodes=nodes)
        manifest["route"] = {"module": f"{module_name}.routes"}
        entry_points.extend(
            [
                '[project.entry-points."sure_eval.routes"]',
                f'asr = "{module_name}.routes"',
                "",
            ]
        )
    manifest["kind"] = (
        "node-and-route" if node_id and pipeline_id else "node" if node_id else "route"
    )
    (root / "sure_eval_plugin.yaml").write_text(
        yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8"
    )
    if with_pyproject:
        (root / "pyproject.toml").write_text(
            "[build-system]\n"
            'requires = ["setuptools>=61"]\n'
            'build-backend = "setuptools.build_meta"\n\n'
            "[project]\n"
            f'name = "{root.name}"\n'
            'version = "0.1.0"\n\n'
            + "\n".join(entry_points)
            + '\n[tool.setuptools.packages.find]\nwhere = ["src"]\n',
            encoding="utf-8",
        )


def _reset_registry() -> None:
    get_registry().invalidate_project_plugins()
    get_registry().local_paths = ()


# ---- core add / lock / replace / drift / remove ----


def test_add_node_only_and_lock(tmp_path: Path) -> None:
    plugin = tmp_path / "node_only"
    plugin.mkdir()
    _write_node(plugin / "node.py")

    inspection = add_plugin(plugin, project_dir=tmp_path)
    assert inspection.effective_kind == "node"
    record = plugin_records(tmp_path)[0]
    assert record["lock_status"] == "ready"
    assert record["node_ids"] == ["normalization/project_norm"]
    assert (tmp_path / ".sure-eval" / "plugins.yaml").exists()
    lock = json.loads((tmp_path / ".sure-eval" / "plugins.lock.json").read_text())
    assert lock["format"] == "sure-eval.plugins.lock.v1"


def test_add_route_only_is_visible_to_metric_routes(tmp_path: Path) -> None:
    plugin = tmp_path / "route_only"
    plugin.mkdir()
    _write_route(plugin / "routes.py")
    add_plugin(plugin, project_dir=tmp_path)

    result = CliRunner().invoke(
        app,
        [
            "--project-dir",
            str(tmp_path),
            "metric",
            "routes",
            "asr",
            "--language",
            "en",
            "--metric",
            "cer",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert "asr.en.cer.aispeech_norm_en_v1.wenet_cer_v1" in result.stdout
    _reset_registry()


def test_add_node_and_route_and_replace(tmp_path: Path) -> None:
    plugin = tmp_path / "combined"
    plugin.mkdir()
    _write_node(plugin / "node.py")
    _write_route(plugin / "routes.py")
    first = add_plugin(plugin, project_dir=tmp_path)
    second = add_plugin(plugin, project_dir=tmp_path)
    assert first.content_hash == second.content_hash
    assert plugin_records(tmp_path)[0]["effective_kind"] == "node-and-route"

    (plugin / "README.txt").write_text("changed", encoding="utf-8")
    with pytest.raises(PluginError, match="different source or hash"):
        add_plugin(plugin, project_dir=tmp_path)
    add_plugin(plugin, project_dir=tmp_path, replace=True)
    assert plugin_records(tmp_path)[0]["lock_status"] == "ready"


def test_hash_drift_and_remove(tmp_path: Path) -> None:
    plugin = tmp_path / "drift"
    plugin.mkdir()
    _write_node(plugin / "node.py")
    add_plugin(plugin, project_dir=tmp_path)
    (plugin / "node.py").write_text(
        (plugin / "node.py").read_text() + "# drift\n", encoding="utf-8"
    )
    assert plugin_records(tmp_path)[0]["lock_status"] == "drifted"
    with pytest.raises(PluginError, match="sync failed"):
        sync_plugins(project_dir=tmp_path)
    assert remove_plugin("drift", project_dir=tmp_path) is True
    assert plugin_records(tmp_path) == []


def test_resource_manifest_hash_is_locked(tmp_path: Path) -> None:
    plugin = tmp_path / "resource_plugin"
    plugin.mkdir()
    _write_node(plugin / "node.py")
    (plugin / "node_env.yaml").write_text("runtime: {type: pip}\n", encoding="utf-8")
    (plugin / "sure_eval_plugin.yaml").write_text(
        "resource_manifest: node_env.yaml\n", encoding="utf-8"
    )
    add_plugin(plugin, project_dir=tmp_path)
    lock = json.loads((tmp_path / ".sure-eval" / "plugins.lock.json").read_text())
    assert lock["plugins"][0]["resource_hashes"]["node_env.yaml"].startswith("sha256:")
    (plugin / "node_env.yaml").write_text("runtime: {type: uv}\n", encoding="utf-8")
    assert plugin_records(tmp_path)[0]["lock_status"] == "drifted"


def test_drifted_plugin_is_skipped_from_registry(tmp_path: Path) -> None:
    plugin = tmp_path / "drifted"
    plugin.mkdir()
    _write_node(plugin / "node.py")
    add_plugin(plugin, project_dir=tmp_path)
    (plugin / "node.py").write_text(
        (plugin / "node.py").read_text() + "# drift\n", encoding="utf-8"
    )
    registry = get_registry()
    registry.invalidate_project_plugins()
    with pytest.warns(RuntimeWarning, match="Skipping project plugin"):
        registry.ensure_project_plugins(tmp_path)
    assert all(Path(path).name != "drifted" for path in registry.project_local_paths)
    _reset_registry()


def test_drifted_route_reports_plugin_status_on_describe(tmp_path: Path) -> None:
    plugin = tmp_path / "drifted_route"
    plugin.mkdir()
    pipeline_id = "asr.en.cer.drifted_norm_v1.wenet_cer_v1"
    _write_route(
        plugin / "routes.py", pipeline_id, ("normalization/aispeech_norm", "scoring/wenet_cer")
    )
    add_plugin(plugin, project_dir=tmp_path)
    (plugin / "routes.py").write_text(
        (plugin / "routes.py").read_text(encoding="utf-8") + "\n# drift\n", encoding="utf-8"
    )
    result = CliRunner().invoke(
        app,
        [
            "--project-dir",
            str(tmp_path),
            "metric",
            "describe",
            "asr",
            "--pipeline-id",
            pipeline_id,
            "--json",
        ],
    )
    assert result.exit_code == 1
    assert "drifted_route" in result.stdout
    assert "drifted" in result.stdout
    _reset_registry()


def test_duplicate_external_node_ids_fail(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    _write_node(first / "node.py")
    _write_node(second / "node.py")
    registry = get_registry()
    registry.project_local_paths = (first, second)
    registry._project_dir = tmp_path
    registry.local_paths = ()
    with pytest.raises(ValueError, match="Duplicate external node_id"):
        registry.resolve("normalization/project_norm")
    _reset_registry()


# ---- CLI-level --project-dir routing ----


def test_cli_add_targets_project_dir_not_cwd(tmp_path: Path, monkeypatch) -> None:
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    plugin = tmp_path / "route_only"
    plugin.mkdir()
    _write_route(plugin / "routes.py")

    result = CliRunner().invoke(
        app, ["--project-dir", str(tmp_path), "plugin", "add", str(plugin), "--json"]
    )
    assert result.exit_code == 0, result.stdout
    assert (tmp_path / ".sure-eval" / "plugins.yaml").exists()
    assert not (cwd / ".sure-eval").exists()
    _reset_registry()


def test_cli_without_project_dir_resets_to_current_directory(tmp_path: Path, monkeypatch) -> None:
    project = tmp_path / "project"
    project.mkdir()
    plugin = project / "route_only"
    plugin.mkdir()
    _write_route(plugin / "routes.py")
    runner = CliRunner()
    assert (
        runner.invoke(
            app, ["--project-dir", str(project), "plugin", "add", str(plugin), "--json"]
        ).exit_code
        == 0
    )

    clean = tmp_path / "clean"
    clean.mkdir()
    monkeypatch.chdir(clean)
    result = runner.invoke(app, ["plugin", "list", "--json"])
    assert result.exit_code == 0, result.stdout
    assert json.loads(result.stdout)["plugins"] == []
    _reset_registry()


def test_cli_add_list_sync_remove_roundtrip(tmp_path: Path) -> None:
    plugin = tmp_path / "route_only"
    plugin.mkdir()
    _write_route(plugin / "routes.py")
    runner = CliRunner()

    result = runner.invoke(
        app, ["--project-dir", str(tmp_path), "plugin", "add", str(plugin), "--json"]
    )
    assert result.exit_code == 0, result.stdout

    result = runner.invoke(app, ["--project-dir", str(tmp_path), "plugin", "list", "--json"])
    assert result.exit_code == 0, result.stdout
    assert "route_only" in result.stdout

    result = runner.invoke(app, ["--project-dir", str(tmp_path), "plugin", "sync", "--json"])
    assert result.exit_code == 0, result.stdout

    result = runner.invoke(
        app, ["--project-dir", str(tmp_path), "plugin", "remove", "route_only", "--json"]
    )
    assert result.exit_code == 0, result.stdout
    assert plugin_records(tmp_path) == []
    _reset_registry()


# ---- acceptance: node-only visibility ----


def test_node_only_visible_in_node_list(tmp_path: Path) -> None:
    plugin = tmp_path / "node_only"
    plugin.mkdir()
    _write_node(plugin / "node.py")
    runner = CliRunner()
    assert (
        runner.invoke(
            app, ["--project-dir", str(tmp_path), "plugin", "add", str(plugin), "--json"]
        ).exit_code
        == 0
    )

    result = runner.invoke(app, ["--project-dir", str(tmp_path), "node", "list", "--json"])
    assert result.exit_code == 0, result.stdout
    assert "normalization/project_norm" in result.stdout
    _reset_registry()


def test_node_only_visible_in_describe_choices(tmp_path: Path) -> None:
    plugin = tmp_path / "node_only"
    plugin.mkdir()
    _write_node(plugin / "node.py", default_for=True)
    runner = CliRunner()
    assert (
        runner.invoke(
            app, ["--project-dir", str(tmp_path), "plugin", "add", str(plugin), "--json"]
        ).exit_code
        == 0
    )

    result = runner.invoke(
        app,
        [
            "--project-dir",
            str(tmp_path),
            "metric",
            "describe",
            "asr",
            "--pipeline-id",
            "asr.en.wer.aispeech_norm_en_v1.wenet_wer_v1",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    choices = [choice for slot in payload["pipeline"] for choice in slot["choices"]]
    assert "normalization/project_norm" in choices
    _reset_registry()


# ---- acceptance: route-only full pipeline ----


def test_route_only_full_pipeline(tmp_path: Path) -> None:
    plugin = tmp_path / "route_only"
    plugin.mkdir()
    _write_route(plugin / "routes.py")
    runner = CliRunner()
    assert (
        runner.invoke(
            app, ["--project-dir", str(tmp_path), "plugin", "add", str(plugin), "--json"]
        ).exit_code
        == 0
    )

    pipeline_path = tmp_path / "pipeline.json"
    result = runner.invoke(
        app,
        [
            "--project-dir",
            str(tmp_path),
            "metric",
            "describe",
            "asr",
            "--pipeline-id",
            "asr.en.cer.aispeech_norm_en_v1.wenet_cer_v1",
            "--output",
            str(pipeline_path),
            "--json",
        ],
    )
    assert result.exit_code == 0, result.stdout

    out = tmp_path / "out"
    result = runner.invoke(
        app,
        [
            "--project-dir",
            str(tmp_path),
            "metric",
            "run",
            "--pipeline",
            str(pipeline_path),
            "--ref-file",
            REF_FILE,
            "--hyp-file",
            HYP_FILE,
            "--output-dir",
            str(out),
            "--json",
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert (out / "report.json").exists()
    _reset_registry()


def test_route_only_can_reference_node_from_another_plugin(tmp_path: Path) -> None:
    node_plugin = tmp_path / "node_only"
    route_plugin = tmp_path / "route_only"
    node_plugin.mkdir()
    route_plugin.mkdir()
    _write_node(node_plugin / "node.py", "normalization/project_norm")
    _write_route(
        route_plugin / "routes.py",
        "asr.en.cer.project_norm_v1.wenet_cer_v1",
        ("normalization/project_norm", "scoring/wenet_cer"),
    )
    runner = CliRunner()
    assert (
        runner.invoke(
            app, ["--project-dir", str(tmp_path), "plugin", "add", str(node_plugin), "--json"]
        ).exit_code
        == 0
    )
    assert (
        runner.invoke(
            app, ["--project-dir", str(tmp_path), "plugin", "add", str(route_plugin), "--json"]
        ).exit_code
        == 0
    )

    pipeline_path = tmp_path / "pipeline.json"
    result = runner.invoke(
        app,
        [
            "--project-dir",
            str(tmp_path),
            "metric",
            "describe",
            "asr",
            "--pipeline-id",
            "asr.en.cer.project_norm_v1.wenet_cer_v1",
            "--output",
            str(pipeline_path),
            "--json",
        ],
    )
    assert result.exit_code == 0, result.stdout
    out = tmp_path / "out"
    result = runner.invoke(
        app,
        [
            "--project-dir",
            str(tmp_path),
            "metric",
            "run",
            "--pipeline",
            str(pipeline_path),
            "--ref-file",
            REF_FILE,
            "--hyp-file",
            HYP_FILE,
            "--output-dir",
            str(out),
            "--json",
        ],
    )
    assert result.exit_code == 0, result.stdout
    report = json.loads((out / "report.json").read_text())
    assert "normalization/project_norm" in [item["node_id"] for item in report["pipeline_trace"]]
    _reset_registry()


# ---- acceptance: node-and-route run + trace ----


def test_node_and_route_run_trace(tmp_path: Path) -> None:
    plugin = tmp_path / "combined"
    plugin.mkdir()
    _write_node(plugin / "node.py", "normalization/project_norm")
    _write_route(
        plugin / "routes.py",
        "asr.en.cer.project_norm_v1.wenet_cer_v1",
        nodes=("normalization/project_norm", "scoring/wenet_cer"),
    )
    runner = CliRunner()
    assert (
        runner.invoke(
            app, ["--project-dir", str(tmp_path), "plugin", "add", str(plugin), "--json"]
        ).exit_code
        == 0
    )

    pipeline_path = tmp_path / "pipeline.json"
    result = runner.invoke(
        app,
        [
            "--project-dir",
            str(tmp_path),
            "metric",
            "describe",
            "asr",
            "--pipeline-id",
            "asr.en.cer.project_norm_v1.wenet_cer_v1",
            "--output",
            str(pipeline_path),
            "--json",
        ],
    )
    assert result.exit_code == 0, result.stdout

    out = tmp_path / "out"
    result = runner.invoke(
        app,
        [
            "--project-dir",
            str(tmp_path),
            "metric",
            "run",
            "--pipeline",
            str(pipeline_path),
            "--ref-file",
            REF_FILE,
            "--hyp-file",
            HYP_FILE,
            "--output-dir",
            str(out),
            "--json",
        ],
    )
    assert result.exit_code == 0, result.stdout
    report = json.loads((out / "report.json").read_text())
    trace_ids = [entry["node_id"] for entry in report["pipeline_trace"]]
    assert "normalization/project_norm" in trace_ids
    _reset_registry()


# ---- acceptance: config vs --extra-node-path ----


def test_project_vs_extra_node_path_conflict(tmp_path: Path) -> None:
    project_plugin = tmp_path / "conf_a"
    extra_plugin = tmp_path / "conf_b"
    project_plugin.mkdir()
    extra_plugin.mkdir()
    _write_node(project_plugin / "node.py", "normalization/dup_norm")
    _write_node(extra_plugin / "node.py", "normalization/dup_norm")
    add_plugin(project_plugin, project_dir=tmp_path)

    registry = get_registry()
    registry.invalidate_project_plugins()
    registry.local_paths = (extra_plugin,)
    registry.ensure_project_plugins(tmp_path)
    with pytest.raises(ValueError, match="Duplicate external node_id"):
        registry.resolve("normalization/dup_norm")
    _reset_registry()


def test_project_and_extra_node_path_merge(tmp_path: Path) -> None:
    project_plugin = tmp_path / "proj_a"
    extra_plugin = tmp_path / "extra_b"
    project_plugin.mkdir()
    extra_plugin.mkdir()
    _write_node(project_plugin / "node.py", "normalization/proj_norm")
    _write_node(extra_plugin / "node.py", "normalization/extra_norm")
    add_plugin(project_plugin, project_dir=tmp_path)

    registry = get_registry()
    registry.invalidate_project_plugins()
    registry.local_paths = (extra_plugin,)
    registry.ensure_project_plugins(tmp_path)
    ids = set(registry.iter_node_ids())
    assert "normalization/proj_norm" in ids
    assert "normalization/extra_norm" in ids
    _reset_registry()


# ---- acceptance: entry point channel unaffected ----


def test_entry_point_still_resolves_with_project_dir(tmp_path: Path, monkeypatch) -> None:
    class _FakeEP:
        def __init__(self, name: str, value: str) -> None:
            self.name = name
            self.value = value

    class _FakeEPS:
        def select(self, group: str | None = None):  # noqa: ANN001
            return [_FakeEP("normalization/ep_norm", "sure_eval.evaluation.plugin_management")]

    monkeypatch.setattr(node_registry_module, "entry_points", lambda: _FakeEPS())

    plugin = tmp_path / "proj"
    plugin.mkdir()
    _write_node(plugin / "node.py", "normalization/proj_norm")
    add_plugin(plugin, project_dir=tmp_path)

    registry = get_registry()
    registry.invalidate_project_plugins()
    registry.ensure_project_plugins(tmp_path)
    ids = set(registry.iter_node_ids())
    assert "normalization/proj_norm" in ids
    assert "normalization/ep_norm" in ids
    _reset_registry()


# ---- unified src package layout ----


def test_package_layout_node_only_visible_in_node_list(tmp_path: Path) -> None:
    plugin = tmp_path / "package_node"
    plugin.mkdir()
    _write_package_plugin(
        plugin,
        "sure_eval_package_node",
        node_id="normalization/package_node",
    )
    add_plugin(plugin, project_dir=tmp_path)

    result = CliRunner().invoke(app, ["--project-dir", str(tmp_path), "node", "list", "--json"])
    assert result.exit_code == 0, result.stdout
    assert "normalization/package_node" in result.stdout
    assert plugin_records(tmp_path)[0]["effective_kind"] == "node"
    _reset_registry()


def test_package_layout_route_only_describe_and_run(tmp_path: Path) -> None:
    plugin = tmp_path / "package_route"
    plugin.mkdir()
    pipeline_id = "asr.en.cer.aispeech_norm_en_v1.wenet_cer_v1"
    _write_package_plugin(
        plugin,
        "sure_eval_package_route",
        pipeline_id=pipeline_id,
    )
    add_plugin(plugin, project_dir=tmp_path)

    pipeline_path = tmp_path / "package-route.json"
    runner = CliRunner()
    describe = runner.invoke(
        app,
        [
            "--project-dir",
            str(tmp_path),
            "metric",
            "describe",
            "asr",
            "--pipeline-id",
            pipeline_id,
            "--output",
            str(pipeline_path),
            "--json",
        ],
    )
    assert describe.exit_code == 0, describe.stdout
    output_dir = tmp_path / "package-route-out"
    result = runner.invoke(
        app,
        [
            "--project-dir",
            str(tmp_path),
            "metric",
            "run",
            "--pipeline",
            str(pipeline_path),
            "--ref-file",
            REF_FILE,
            "--hyp-file",
            HYP_FILE,
            "--output-dir",
            str(output_dir),
            "--json",
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert (output_dir / "report.json").exists()
    _reset_registry()


def test_package_layout_node_and_route_run_trace(tmp_path: Path) -> None:
    plugin = tmp_path / "package_combined"
    plugin.mkdir()
    node_id = "normalization/package_combined"
    pipeline_id = "asr.en.cer.package_combined_v1.wenet_cer_v1"
    _write_package_plugin(
        plugin,
        "sure_eval_package_combined",
        node_id=node_id,
        pipeline_id=pipeline_id,
    )
    inspection = add_plugin(plugin, project_dir=tmp_path)
    assert inspection.effective_kind == "node-and-route"

    pipeline_path = tmp_path / "package-combined.json"
    output_dir = tmp_path / "package-combined-out"
    runner = CliRunner()
    describe = runner.invoke(
        app,
        [
            "--project-dir",
            str(tmp_path),
            "metric",
            "describe",
            "asr",
            "--pipeline-id",
            pipeline_id,
            "--output",
            str(pipeline_path),
            "--json",
        ],
    )
    assert describe.exit_code == 0, describe.stdout
    result = runner.invoke(
        app,
        [
            "--project-dir",
            str(tmp_path),
            "metric",
            "run",
            "--pipeline",
            str(pipeline_path),
            "--ref-file",
            REF_FILE,
            "--hyp-file",
            HYP_FILE,
            "--output-dir",
            str(output_dir),
            "--json",
        ],
    )
    assert result.exit_code == 0, result.stdout
    report = json.loads((output_dir / "report.json").read_text(encoding="utf-8"))
    assert node_id in [entry["node_id"] for entry in report["pipeline_trace"]]
    _reset_registry()


def test_package_layout_supports_relative_imports_and_optional_pyproject(tmp_path: Path) -> None:
    plugin = tmp_path / "package_relative"
    plugin.mkdir()
    module_name = "sure_eval_package_relative"
    _write_package_plugin(
        plugin,
        module_name,
        node_id="normalization/package_relative",
        with_pyproject=False,
    )
    package = plugin / "src" / module_name
    (package / "helper.py").write_text('MARKER = "relative-import-ok"\n', encoding="utf-8")
    node_path = package / "node.py"
    node_path.write_text(
        "from .helper import MARKER\n" + node_path.read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    inspection = add_plugin(plugin, project_dir=tmp_path)
    assert inspection.node_ids == ("normalization/package_relative",)
    assert get_registry().resolve("normalization/package_relative").module.endswith(".node")
    _reset_registry()


@pytest.mark.parametrize(
    ("manifest", "message"),
    [
        ({"plugin_api": "sure-eval.plugin.v1", "node": {"module": "pkg.node"}}, "package.module"),
        (
            {
                "plugin_api": "sure-eval.plugin.v1",
                "package": {"module": "pkg"},
                "node": {"module": "outside.node"},
            },
            "must be inside",
        ),
        (
            {
                "plugin_api": "sure-eval.plugin.v1",
                "package": {"module": "pkg"},
                "node": {"module": "pkg.node"},
                "nodes": ["node.py"],
            },
            "cannot be mixed",
        ),
    ],
)
def test_package_layout_rejects_invalid_manifest(
    tmp_path: Path,
    manifest: dict[str, object],
    message: str,
) -> None:
    plugin = tmp_path / message.replace(" ", "_").replace(".", "_")
    package = plugin / "src" / "pkg"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    _write_node(package / "node.py", "normalization/pkg")
    (plugin / "sure_eval_plugin.yaml").write_text(
        yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8"
    )
    with pytest.raises(PluginError, match=message):
        add_plugin(plugin, project_dir=tmp_path)


def test_package_layout_requires_manifest_and_init(tmp_path: Path) -> None:
    missing_manifest = tmp_path / "missing_manifest"
    package = missing_manifest / "src" / "pkg_missing_manifest"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    _write_node(package / "node.py")
    with pytest.raises(PluginError, match="requires sure_eval_plugin.yaml"):
        add_plugin(missing_manifest, project_dir=tmp_path)

    missing_init = tmp_path / "missing_init"
    package = missing_init / "src" / "pkg_missing_init"
    package.mkdir(parents=True)
    _write_node(package / "node.py")
    (missing_init / "sure_eval_plugin.yaml").write_text(
        yaml.safe_dump(
            {
                "plugin_api": "sure-eval.plugin.v1",
                "kind": "node",
                "package": {"module": "pkg_missing_init"},
                "node": {"module": "pkg_missing_init.node"},
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(PluginError, match="requires __init__.py"):
        add_plugin(missing_init, project_dir=tmp_path)


def test_package_layout_rejects_pyproject_identity_mismatch(tmp_path: Path) -> None:
    plugin = tmp_path / "package_bad_identity"
    plugin.mkdir()
    _write_package_plugin(
        plugin,
        "sure_eval_package_bad_identity",
        node_id="normalization/package_bad_identity",
    )
    pyproject = plugin / "pyproject.toml"
    pyproject.write_text(
        pyproject.read_text(encoding="utf-8").replace(
            '"normalization/package_bad_identity"',
            '"normalization/wrong_identity"',
        ),
        encoding="utf-8",
    )
    with pytest.raises(PluginError, match="must match NODE_ID"):
        add_plugin(plugin, project_dir=tmp_path)


def test_package_layout_rejects_route_task_mismatch(tmp_path: Path) -> None:
    plugin = tmp_path / "package_bad_task"
    plugin.mkdir()
    _write_package_plugin(
        plugin,
        "sure_eval_package_bad_task",
        pipeline_id="asr.en.cer.aispeech_norm_en_v1.wenet_cer_v1",
    )
    pyproject = plugin / "pyproject.toml"
    pyproject.write_text(
        pyproject.read_text(encoding="utf-8").replace(
            'asr = "sure_eval_package_bad_task.routes"',
            'tts = "sure_eval_package_bad_task.routes"',
        ),
        encoding="utf-8",
    )
    with pytest.raises(PluginError, match="map each route task"):
        add_plugin(plugin, project_dir=tmp_path)


def test_package_layout_rejects_malformed_pyproject(tmp_path: Path) -> None:
    plugin = tmp_path / "package_bad_toml"
    plugin.mkdir()
    _write_package_plugin(
        plugin,
        "sure_eval_package_bad_toml",
        node_id="normalization/package_bad_toml",
    )
    (plugin / "pyproject.toml").write_text("[project\n", encoding="utf-8")
    with pytest.raises(PluginError, match="Cannot read pyproject.toml"):
        add_plugin(plugin, project_dir=tmp_path)


def test_package_layout_extra_node_path_is_supported(tmp_path: Path) -> None:
    plugin = tmp_path / "package_extra"
    plugin.mkdir()
    _write_package_plugin(
        plugin,
        "sure_eval_package_extra",
        node_id="normalization/package_extra",
    )
    registry = get_registry()
    registry.invalidate_project_plugins()
    registry.local_paths = (plugin,)
    assert registry.resolve("normalization/package_extra").source == "local"
    _reset_registry()


def test_package_layout_replace_reloads_changed_module(tmp_path: Path) -> None:
    plugin = tmp_path / "package_reload"
    plugin.mkdir()
    module_name = "sure_eval_package_reload"
    _write_package_plugin(
        plugin,
        module_name,
        node_id="normalization/package_reload",
        with_pyproject=False,
    )
    add_plugin(plugin, project_dir=tmp_path)
    assert get_registry().resolve("normalization/package_reload").version == "v1"

    node_path = plugin / "src" / module_name / "node.py"
    node_path.write_text(
        node_path.read_text(encoding="utf-8").replace('VERSION = "v1"', 'VERSION = "v2"'),
        encoding="utf-8",
    )
    add_plugin(plugin, project_dir=tmp_path, replace=True)
    assert get_registry().resolve("normalization/package_reload").version == "v2"
    _reset_registry()


def test_package_layout_node_dual_registration_conflicts(
    tmp_path: Path,
    monkeypatch,
) -> None:
    plugin = tmp_path / "package_dual"
    plugin.mkdir()
    module_name = "sure_eval_package_dual"
    node_id = "normalization/package_dual"
    _write_package_plugin(plugin, module_name, node_id=node_id)
    add_plugin(plugin, project_dir=tmp_path)
    monkeypatch.setattr(
        node_registry_module.NodeRegistry,
        "iter_entry_point_specs",
        staticmethod(lambda: [(node_id, f"{module_name}.node")]),
    )
    with pytest.raises(ValueError, match="Duplicate external node_id"):
        get_registry().resolve(node_id)
    _reset_registry()


def test_package_layout_route_dual_registration_conflicts(
    tmp_path: Path,
    monkeypatch,
) -> None:
    plugin = tmp_path / "package_dual_route"
    plugin.mkdir()
    module_name = "sure_eval_package_dual_route"
    _write_package_plugin(
        plugin,
        module_name,
        pipeline_id="asr.en.cer.aispeech_norm_en_v1.wenet_cer_v1",
    )
    add_plugin(plugin, project_dir=tmp_path)

    class _FakeEP:
        name = "asr"
        value = f"{module_name}.routes"

    monkeypatch.setattr(importlib.metadata, "entry_points", lambda **kwargs: [_FakeEP()])
    with pytest.raises(ValueError, match="Duplicate route pipeline_id"):
        load_task_routes("asr")
    _reset_registry()


@pytest.mark.parametrize(
    ("directory", "kind"),
    [
        ("node_only_plugin", "node"),
        ("pipeline_plugin_cer", "route"),
        ("node_pipeline_plugin_wer", "node-and-route"),
    ],
)
def test_unified_layout_examples_are_valid(directory: str, kind: str) -> None:
    inspection = inspect_plugin(ROOT / "examples" / directory)
    assert inspection.effective_kind == kind


# ---- acceptance: manifest validation ----


def test_manifest_rejections(tmp_path: Path) -> None:
    before = len(plugin_records(tmp_path))

    bad_api = tmp_path / "bad_api"
    bad_api.mkdir()
    _write_node(bad_api / "node.py")
    (bad_api / "sure_eval_plugin.yaml").write_text(
        'plugin_api: "sure-eval.plugin.v9"\n', encoding="utf-8"
    )
    with pytest.raises(PluginError, match="plugin_api"):
        add_plugin(bad_api, project_dir=tmp_path)
    assert len(plugin_records(tmp_path)) == before

    with pytest.raises(PluginError, match="second phase"):
        add_plugin("open-bench://org/plugin@deadbeef", project_dir=tmp_path)

    bad_kind = tmp_path / "bad_kind"
    bad_kind.mkdir()
    _write_node(bad_kind / "node.py")
    (bad_kind / "sure_eval_plugin.yaml").write_text('kind: "route"\n', encoding="utf-8")
    with pytest.raises(PluginError, match="does not match"):
        add_plugin(bad_kind, project_dir=tmp_path)
    assert len(plugin_records(tmp_path)) == before

    bad_version = tmp_path / "bad_version"
    bad_version.mkdir()
    _write_node(bad_version / "node.py")
    (bad_version / "sure_eval_plugin.yaml").write_text(
        'requires_sure_eval: ">=9999"\n', encoding="utf-8"
    )
    with pytest.raises(PluginError, match="requires"):
        add_plugin(bad_version, project_dir=tmp_path)
    assert len(plugin_records(tmp_path)) == before


# ---- acceptance: duplicate pipeline_id ----


def test_duplicate_pipeline_id_fails(tmp_path: Path) -> None:
    first = tmp_path / "r1"
    second = tmp_path / "r2"
    first.mkdir()
    second.mkdir()
    _write_route(first / "routes.py")
    _write_route(second / "routes.py")
    add_plugin(first, project_dir=tmp_path)
    add_plugin(second, project_dir=tmp_path)

    result = CliRunner().invoke(
        app,
        [
            "--project-dir",
            str(tmp_path),
            "metric",
            "routes",
            "asr",
            "--language",
            "en",
            "--metric",
            "cer",
            "--json",
        ],
    )
    assert result.exit_code == 1
    assert "Duplicate route pipeline_id" in result.stdout
    _reset_registry()


# ---- acceptance: missing / invalid status ----


def test_missing_plugin_is_skipped(tmp_path: Path) -> None:
    plugin = tmp_path / "gone"
    plugin.mkdir()
    _write_node(plugin / "node.py", "normalization/gone_norm")
    add_plugin(plugin, project_dir=tmp_path)
    shutil.rmtree(plugin)

    record = [r for r in plugin_records(tmp_path) if r["name"] == "gone"][0]
    assert record["lock_status"] == "missing"

    registry = get_registry()
    registry.invalidate_project_plugins()
    with pytest.warns(RuntimeWarning, match="Skipping project plugin"):
        registry.ensure_project_plugins(tmp_path)
    assert all(Path(path).name != "gone" for path in registry.project_local_paths)
    _reset_registry()


def test_invalid_lock_status(tmp_path: Path) -> None:
    plugin = tmp_path / "invalid_case"
    plugin.mkdir()
    _write_node(plugin / "node.py", "normalization/inv_norm")
    add_plugin(plugin, project_dir=tmp_path)

    lock_path = tmp_path / ".sure-eval" / "plugins.lock.json"
    lock = json.loads(lock_path.read_text())
    for entry in lock["plugins"]:
        if entry["name"] == "invalid_case":
            entry["resolved_path"] = "wrong/path"
    lock_path.write_text(json.dumps(lock))

    record = [r for r in plugin_records(tmp_path) if r["name"] == "invalid_case"][0]
    assert record["lock_status"] == "invalid"


# ---- acceptance: transaction recovery ----


def test_transaction_recovery(tmp_path: Path) -> None:
    sub = tmp_path / ".sure-eval"
    sub.mkdir()
    config_tmp = sub / "plugins.recover.yaml.tmp"
    lock_tmp = sub / "plugins.recover.json.tmp"
    config_tmp.write_text(
        yaml.safe_dump(
            {
                "plugins": [
                    {
                        "name": "recovered",
                        "source": "path",
                        "path": "recovered",
                        "path_mode": "project_relative",
                    }
                ]
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    lock_tmp.write_text(
        json.dumps({"format": "sure-eval.plugins.lock.v1", "plugins": [{"name": "recovered"}]}),
        encoding="utf-8",
    )
    (sub / ".plugins.transaction.json").write_text(
        json.dumps({"config_tmp": str(config_tmp), "lock_tmp": str(lock_tmp)}),
        encoding="utf-8",
    )

    records = plugin_records(tmp_path)
    assert [r["name"] for r in records] == ["recovered"]
    assert not (sub / ".plugins.transaction.json").exists()
    assert "recovered" in (sub / "plugins.yaml").read_text(encoding="utf-8")
