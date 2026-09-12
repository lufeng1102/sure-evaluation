"""Tests for local-path node loading (``--extra-node-path``) end to end."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from sure_eval.cli import app
from sure_eval.evaluation.node_registry import get_registry

_EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
_LOWERCASE_NODE = (
    _EXAMPLES / "node_plugin_lowercase" / "sure_eval_node_lowercase" / "node.py"
)
_EXACT_MATCH_NODE = (
    _EXAMPLES / "node_plugin_exact_match" / "sure_eval_node_exact_match" / "node.py"
)


def _install_local_paths(monkeypatch, *paths: str) -> None:
    monkeypatch.setattr(get_registry(), "local_paths", tuple(paths))


def test_local_path_resolve_and_build(tmp_path, monkeypatch) -> None:
    from sure_eval.evaluation.core.types import KeyTextFiles

    _install_local_paths(monkeypatch, str(_LOWERCASE_NODE))
    reg = get_registry()

    registration = reg.resolve("normalization/lowercase_norm")
    assert registration.source == "local"
    assert registration.build is not None

    ref = tmp_path / "ref.txt"
    hyp = tmp_path / "hyp.txt"
    ref.write_text("k1\tHELLO\n")
    hyp.write_text("k1\tWORLD\n")
    node = reg.build("normalization/lowercase_norm")
    out, _result = node(KeyTextFiles(ref_file=str(ref), hyp_file=str(hyp)))
    assert out.ref_file.endswith(".lower")


def test_local_path_find_by_selector_and_name(monkeypatch) -> None:
    _install_local_paths(monkeypatch, str(_LOWERCASE_NODE))
    reg = get_registry()

    assert (
        reg.find_by_selector("normalization", "normalizer", "lowercase_norm")
        == "normalization/lowercase_norm"
    )
    assert reg.find_node_by_name("normalization", "lowercase_norm") == "normalization/lowercase_norm"


def test_local_path_metadata(monkeypatch) -> None:
    _install_local_paths(monkeypatch, str(_LOWERCASE_NODE))
    reg = get_registry()

    assert reg.manifest("normalization/lowercase_norm")["id"] == "normalization/lowercase_norm"
    assert reg.node_env("normalization/lowercase_norm") is None
    assert "normalization/lowercase_norm" in reg.iter_node_ids()
    assert reg.manifest_path("normalization/lowercase_norm") == _LOWERCASE_NODE.resolve()


def test_local_path_directory_form(monkeypatch) -> None:
    # A directory containing node.py resolves the same way.
    _install_local_paths(monkeypatch, str(_LOWERCASE_NODE.parent))
    reg = get_registry()
    assert reg.resolve("normalization/lowercase_norm").source == "local"


def test_evaluate_asr_files_local_chain(tmp_path, monkeypatch) -> None:
    """A local normalizer + local scorer pair drive evaluate_asr_files end to end."""
    from sure_eval.evaluation.tasks.asr.pipeline import evaluate_asr_files

    _install_local_paths(monkeypatch, str(_LOWERCASE_NODE), str(_EXACT_MATCH_NODE))

    ref = tmp_path / "ref.txt"
    hyp = tmp_path / "hyp.txt"
    ref.write_text("k1\tHELLO\nk2\tWORLD\n")
    hyp.write_text("k1\thello\nk2\tworld\n")

    report = evaluate_asr_files(
        str(ref),
        str(hyp),
        language="en",
        metric="wer",
        normalizer="lowercase_norm",
        scorer="exact_match",
    )

    # lowercase normalization makes both rows match exactly -> score 1.0.
    assert report.score == 1.0
    assert report.pipeline_id.endswith("lowercase_norm_v1.exact_match_v1")
    assert [entry.node_id for entry in report.pipeline_trace] == [
        "normalization/lowercase_norm",
        "scoring/exact_match",
    ]


def _write_route_only_dir(tmp_path: Path) -> Path:
    package = tmp_path / "route_only"
    package.mkdir()
    (package / "routes.py").write_text(
        'ROUTES = [{\n'
        '    "language": "en",\n'
        '    "metric": "wer",\n'
        '    "pipeline_id": "asr.en.wer.whisper_norm_english_v1.wenet_wer_v1.local_only",\n'
        '    "nodes": ["normalization/whisper_norm", "scoring/wenet_wer"],\n'
        '    "input_contract": "scoring/wenet_wer",\n'
        '    "executor": "sure_eval.evaluation.tasks.asr.pipeline.evaluate_asr_files",\n'
        "}]\n"
    )
    return package


def _write_env_plugin_dir(tmp_path: Path) -> Path:
    package = tmp_path / "env_plugin"
    package.mkdir()
    (package / "node.py").write_text(
        'NODE_ID = "normalization/env_norm"\n'
        'STAGE = "normalization"\n'
        'VERSION = "v1"\n'
        'MANIFEST = {"id": NODE_ID, "version": VERSION, "stage": STAGE}\n'
        'NODE_ENV = {"runtime": {"type": "pip"}, "verify": {"imports": ["json"]}}\n'
        'SELECTORS = {"normalizer": "env_norm"}\n'
        'def build(**config):\n'
        '    return lambda files: (files, None)\n',
        encoding="utf-8",
    )
    (package / "routes.py").write_text(
        'ROUTES = [{\n'
        '    "language": "en",\n'
        '    "metric": "wer",\n'
        '    "pipeline_id": "asr.en.wer.env_norm_v1.wenet_wer_v1",\n'
        '    "nodes": ["normalization/env_norm", "scoring/wenet_wer"],\n'
        '    "input_contract": "scoring/wenet_wer",\n'
        '    "executor": "sure_eval.evaluation.tasks.asr.pipeline.evaluate_asr_files",\n'
        '}]\n',
        encoding="utf-8",
    )
    return package


def test_local_route_only_directory(tmp_path, monkeypatch) -> None:
    """A directory with only routes.py registers a route (no node.py needed)."""
    from sure_eval.evaluation.scripts.contracts import load_task_routes

    package = _write_route_only_dir(tmp_path)
    _install_local_paths(monkeypatch, str(package))

    routes, _ = load_task_routes("asr")
    ids = [route["pipeline_id"] for route in routes["routes"]]
    assert "asr.en.wer.whisper_norm_english_v1.wenet_wer_v1.local_only" in ids

    # The owning task is inferred from the executor, so another task ignores it.
    vad_routes, _ = load_task_routes("vad")
    vad_ids = [route["pipeline_id"] for route in vad_routes["routes"]]
    assert "asr.en.wer.whisper_norm_english_v1.wenet_wer_v1.local_only" not in vad_ids


def test_local_directory_registers_node_and_route(monkeypatch) -> None:
    """One --extra-node-path directory supplies both node.py and routes.py."""
    from sure_eval.evaluation.scripts.contracts import load_task_routes

    _install_local_paths(monkeypatch, str(_LOWERCASE_NODE.parent))

    assert get_registry().resolve("normalization/lowercase_norm").source == "local"

    routes, _ = load_task_routes("asr")
    ids = [route["pipeline_id"] for route in routes["routes"]]
    assert "asr.en.wer.lowercase_norm_v1.wenet_wer_v1" in ids


def test_metric_routes_cli_accepts_local_node_and_route_directory(monkeypatch) -> None:
    registry = get_registry()
    monkeypatch.setattr(registry, "local_paths", ())

    result = CliRunner().invoke(
        app,
        [
            "metric",
            "routes",
            "asr",
            "--language",
            "en",
            "--metric",
            "wer",
            "--extra-node-path",
            str(_LOWERCASE_NODE.parent),
            "--json",
        ],
    )

    assert result.exit_code == 0, result.stdout
    pipeline_ids = [route["pipeline_id"] for route in json.loads(result.stdout)["routes"]]
    assert "asr.en.wer.lowercase_norm_v1.wenet_wer_v1" in pipeline_ids


def test_env_commands_accept_local_pipeline(monkeypatch, tmp_path: Path) -> None:
    registry = get_registry()
    monkeypatch.setattr(registry, "local_paths", ())
    runner = CliRunner()
    pipeline_path = tmp_path / "pipeline.json"
    local_path = str(_write_env_plugin_dir(tmp_path))

    describe_result = runner.invoke(
        app,
        [
            "metric",
            "describe",
            "asr",
            "--pipeline-id",
            "asr.en.wer.env_norm_v1.wenet_wer_v1",
            "--extra-node-path",
            local_path,
            "--output",
            str(pipeline_path),
            "--json",
        ],
    )
    assert describe_result.exit_code == 0, describe_result.stdout

    for command in ("check", "setup"):
        registry.local_paths = ()
        args = [
            "env",
            command,
            "--pipeline",
            str(pipeline_path),
            "--extra-node-path",
            local_path,
            "--json",
        ]
        if command == "setup":
            args.append("--dry-run")
        result = runner.invoke(app, args)
        assert result.exit_code == 0, result.stdout
        payload = json.loads(result.stdout)
        items = payload["checks"] if command == "check" else payload["actions"]
        assert [item["node_id"] for item in items] == ["normalization/env_norm"]
        assert items[0]["runtime"] == ("pip_optional" if command == "check" else "pip")
