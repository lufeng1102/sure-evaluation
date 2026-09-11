"""Tests for local-path node loading (``--extra-node-path``) end to end."""

from __future__ import annotations

from pathlib import Path

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
