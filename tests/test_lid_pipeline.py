from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


class _FakeLIDRunner:
    device = "cpu"

    def __init__(self, languages: list[str]) -> None:
        self.languages = languages

    def predict_batch(self, sample_ids, audio_paths):
        return [
            {
                "uttid": sample_id,
                "wav": audio_path,
                "lang": language,
                "confidence": 0.9 - index * 0.1,
                "dur_s": 1.25,
                "rtf": "0.1000",
            }
            for index, (sample_id, audio_path, language) in enumerate(
                zip(sample_ids, audio_paths, self.languages, strict=True)
            )
        ]


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def _write_key_labels(path: Path, rows: list[tuple[str, str]]) -> None:
    path.write_text("".join(f"{key}\t{label}\n" for key, label in rows), encoding="utf-8")


def test_lid_label_normalization_matches_firered_dialect_output() -> None:
    from sure_eval.evaluation.nodes.normalization.lid_label import normalize_lid_label

    assert normalize_lid_label(" zh Mandarin ") == "zh-mandarin"
    assert normalize_lid_label("ZH_yue") == "zh-yue"
    assert normalize_lid_label("<unk>") == ""
    assert normalize_lid_label("en") == "en"


def test_load_lid_samples_resolves_audio_and_validates_required_labels(tmp_path: Path) -> None:
    from sure_eval.evaluation.audio_samples import SampleJsonlError, load_lid_samples_jsonl

    (tmp_path / "sample.wav").write_bytes(b"fake")
    manifest = tmp_path / "samples.jsonl"
    _write_jsonl(
        manifest,
        [
            {
                "sample_id": "utt1",
                "audio_path": "sample.wav",
                "reference_language": "zh-mandarin",
                "metadata": {"dataset": "test"},
            }
        ],
    )

    samples = load_lid_samples_jsonl(manifest)
    assert samples[0].audio_path == str((tmp_path / "sample.wav").resolve())
    assert samples[0].reference_language == "zh-mandarin"
    assert samples[0].metadata == {"dataset": "test"}

    _write_jsonl(manifest, [{"sample_id": "utt1", "audio_path": "sample.wav"}])
    with pytest.raises(SampleJsonlError, match="line 1.*reference_language is required"):
        load_lid_samples_jsonl(manifest)


def test_lid_pipeline_scores_normalized_labels_and_records_trace(tmp_path: Path) -> None:
    from sure_eval.evaluation.tasks.lid import LIDSample, evaluate_lid_samples

    samples = [
        LIDSample(str(tmp_path / "zh.wav"), "zh-mandarin", "zh"),
        LIDSample(str(tmp_path / "en.wav"), "en", "en"),
        LIDSample(str(tmp_path / "fr.wav"), "fr", "fr"),
    ]
    report = evaluate_lid_samples(
        samples,
        runner=_FakeLIDRunner(["zh mandarin", "en", "de"]),
        input_manifest=str(tmp_path / "samples.jsonl"),
    )

    assert report.task == "LID"
    assert report.metric == "accuracy"
    assert report.score == pytest.approx(2 / 3)
    assert report.pipeline_id == (
        "lid.any.accuracy.firered_lid_v1.lid_label_canonical_v1.classify_v1"
    )
    assert report.computation_node_ids == (
        "inference/firered_lid",
        "normalization/lid_label",
        "scoring/classify",
    )
    assert [node.stage for node in report.pipeline_trace] == [
        "inference",
        "normalization",
        "scoring",
    ]
    assert report.details["rows"][0]["predicted_language"] == "zh-mandarin"
    assert report.details["rows"][2]["correct"] is False


def test_default_lid_pipeline_scores_any_system_label_files(tmp_path: Path) -> None:
    from sure_eval.evaluation.tasks.lid import evaluate_lid_files

    ref_file = tmp_path / "ref.txt"
    hyp_file = tmp_path / "hyp.txt"
    _write_key_labels(
        ref_file,
        [("zh", "zh-mandarin"), ("en", "en"), ("fr", "fr"), ("custom", "x-demo")],
    )
    _write_key_labels(
        hyp_file,
        [("zh", "ZH mandarin"), ("en", "en"), ("fr", "de"), ("custom", "X_demo")],
    )

    report = evaluate_lid_files(str(ref_file), str(hyp_file))
    assert report.score == pytest.approx(3 / 4)
    assert report.pipeline_id == "lid.any.accuracy.lid_label_canonical_v1.classify_v1"
    assert report.computation_node_ids == (
        "normalization/lid_label",
        "scoring/classify",
    )
    assert report.input_files.as_dict() == {"ref": str(ref_file), "hyp": str(hyp_file)}
    assert report.details["rows"][0]["predicted_language"] == "zh-mandarin"


def test_lid_script_describes_and_runs_exact_route(tmp_path: Path) -> None:
    from sure_eval.evaluation.scripts.lid import describe_pipeline, run
    from sure_eval.evaluation.tasks.lid import LIDSample

    default_pipeline_id = "lid.any.accuracy.lid_label_canonical_v1.classify_v1"
    description = describe_pipeline()
    assert description.pipeline_id == default_pipeline_id
    assert description.required_roles == ("hyp", "ref")
    assert description.node_ids == ("normalization/lid_label", "scoring/classify")

    ref_file = tmp_path / "ref.txt"
    hyp_file = tmp_path / "hyp.txt"
    _write_key_labels(ref_file, [("utt1", "en")])
    _write_key_labels(hyp_file, [("utt1", "en")])
    default_report = run(
        str(ref_file),
        str(hyp_file),
        output_dir=str(tmp_path / "default-out"),
        pipeline_id=default_pipeline_id,
    )
    assert default_report.score == 1.0

    pipeline_id = "lid.any.accuracy.firered_lid_v1.lid_label_canonical_v1.classify_v1"
    description = describe_pipeline(pipeline_id=pipeline_id)
    assert description.required_roles == ("samples_jsonl",)
    assert description.node_ids == (
        "inference/firered_lid",
        "normalization/lid_label",
        "scoring/classify",
    )

    from sure_eval.evaluation.cli_adapters import build_pipeline_spec

    pipeline = build_pipeline_spec("lid", pipeline_id=pipeline_id)
    assert pipeline["pipeline"][0]["stage"] == "inference"
    assert pipeline["pipeline"][0]["nullable"] is False

    report = run(
        output_dir=str(tmp_path / "out"),
        pipeline_id=pipeline_id,
        samples=[LIDSample(str(tmp_path / "en.wav"), "en", "utt1")],
        runner=_FakeLIDRunner(["en"]),
    )
    assert report.score == 1.0
    assert json.loads((tmp_path / "out" / "report.json").read_text())["pipeline_id"] == pipeline_id


def test_lid_cli_route_and_pipeline_run_with_injected_runtime(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from typer.testing import CliRunner

    from sure_eval.cli import app
    from sure_eval.evaluation import audio_runtime

    audio = tmp_path / "en.wav"
    audio.write_bytes(b"fake")
    samples = tmp_path / "samples.jsonl"
    _write_jsonl(
        samples,
        [{"sample_id": "utt1", "audio_path": "en.wav", "reference_language": "en"}],
    )
    monkeypatch.setattr(
        audio_runtime,
        "build_lid_runtime",
        lambda **kwargs: {"runner": _FakeLIDRunner(["en"])},
    )
    runner = CliRunner()
    route_result = runner.invoke(app, ["metric", "routes", "lid", "--json"])
    assert route_result.exit_code == 0, route_result.stdout
    inventory = json.loads(route_result.stdout)
    assert inventory["count"] == 2
    assert inventory["default_pipeline_id"] == (
        "lid.any.accuracy.lid_label_canonical_v1.classify_v1"
    )
    route = next(route for route in inventory["routes"] if route["selectors"].get("method"))
    pipeline_path = tmp_path / "pipeline.json"
    describe_result = runner.invoke(
        app,
        [
            "metric",
            "describe",
            "lid",
            "--pipeline-id",
            route["pipeline_id"],
            "--output",
            str(pipeline_path),
            "--json",
        ],
    )
    assert describe_result.exit_code == 0, describe_result.stdout
    run_result = runner.invoke(
        app,
        [
            "metric",
            "run",
            "--pipeline",
            str(pipeline_path),
            "--samples-jsonl",
            str(samples),
            "--device",
            "cpu",
            "--output-dir",
            str(tmp_path / "out"),
            "--json",
        ],
    )
    assert run_result.exit_code == 0, run_result.stdout
    assert json.loads(run_result.stdout)["score"] == 1.0


def test_default_lid_cli_describe_and_run_label_files(tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from sure_eval.cli import app

    ref_file = tmp_path / "ref.txt"
    hyp_file = tmp_path / "hyp.txt"
    pipeline_path = tmp_path / "pipeline.json"
    _write_key_labels(ref_file, [("utt1", "zh-mandarin"), ("utt2", "en")])
    _write_key_labels(hyp_file, [("utt1", "zh mandarin"), ("utt2", "en")])
    runner = CliRunner()

    describe_result = runner.invoke(
        app,
        ["metric", "describe", "lid", "--output", str(pipeline_path), "--json"],
    )
    assert describe_result.exit_code == 0, describe_result.stdout
    pipeline = json.loads(pipeline_path.read_text(encoding="utf-8"))
    assert pipeline["pipeline_id"] == "lid.any.accuracy.lid_label_canonical_v1.classify_v1"
    assert pipeline["required_roles"] == ["hyp", "ref"]

    run_result = runner.invoke(
        app,
        [
            "metric",
            "run",
            "--pipeline",
            str(pipeline_path),
            "--ref-file",
            str(ref_file),
            "--hyp-file",
            str(hyp_file),
            "--output-dir",
            str(tmp_path / "out"),
            "--json",
        ],
    )
    assert run_result.exit_code == 0, run_result.stdout
    assert json.loads(run_result.stdout)["score"] == 1.0


def test_lid_agent_plan_exposes_pinned_model_setup(monkeypatch: pytest.MonkeyPatch) -> None:
    from sure_eval.evaluation.agent_plan import build_agent_plan
    from sure_eval.evaluation.env_check import EnvCheckResult, NodeEnvChecker

    def fake_check_node(self, node_id: str) -> EnvCheckResult:
        if node_id == "inference/firered_lid":
            return EnvCheckResult(
                name=node_id,
                node_id=node_id,
                runtime="node_local_project",
                required=True,
                status="failed",
                message="checkpoint is missing",
                fix=f"sure-eval env setup --node {node_id}",
            )
        return EnvCheckResult(
            name=node_id,
            node_id=node_id,
            runtime="in_process",
            required=False,
            status="ok",
            message="in-process node",
        )

    monkeypatch.setattr(NodeEnvChecker, "check_node", fake_check_node)

    payload = build_agent_plan(
        "lid",
        pipeline_id="lid.any.accuracy.firered_lid_v1.lid_label_canonical_v1.classify_v1",
        include_root_env=False,
    )
    route = payload["selected_routes"][0]
    assert route["pipeline_id"] == (
        "lid.any.accuracy.firered_lid_v1.lid_label_canonical_v1.classify_v1"
    )
    check = next(item for item in route["env_checks"] if item["node_id"] == "inference/firered_lid")
    assert check["runtime"] == "node_local_project"
    assert check["blocking"] is True
    asset = check["setup"]["assets"][0]
    assert asset["provider"] == "modelscope"
    assert asset["revision"] == "fedf637f03d1d62b499df647cb0da0ad004e6642"
    assert asset["layout"] == "local_dir"
    assert asset["sha256"] == "7dee2a280e9b11d5241a0e3d4fa60ee1520a036a2e8385f17960371cfea10093"
    assert payload["next_steps"][-1] == "sure-eval env download --node inference/firered_lid"


def test_default_lid_agent_plan_is_model_independent() -> None:
    from sure_eval.evaluation.agent_plan import build_agent_plan

    payload = build_agent_plan("lid", metric="accuracy", include_root_env=False)
    route = payload["selected_routes"][0]
    assert route["pipeline_id"] == "lid.any.accuracy.lid_label_canonical_v1.classify_v1"
    assert route["required_roles"] == ["hyp", "ref"]
    assert route["computation_node_ids"] == [
        "normalization/lid_label",
        "scoring/classify",
    ]
    assert route["setup_required"] is False
    assert route["can_run_now"] is True


def test_firered_lid_node_json_mode_keeps_upstream_noise_off_stdout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from sure_eval.evaluation.nodes.inference.firered_lid import node

    input_jsonl = tmp_path / "input.jsonl"
    _write_jsonl(input_jsonl, [{"sample_id": "utt1", "audio_path": "/tmp/utt1.wav"}])

    class NoisyRunner:
        def __init__(self, **kwargs) -> None:
            pass

        def predict_batch(self, sample_ids, audio_paths):
            print("upstream diagnostic")
            return [{"uttid": "utt1", "wav": audio_paths[0], "lang": "en"}]

    monkeypatch.setattr(node, "FireRedLIDRunner", NoisyRunner)
    assert node.main(["--input-jsonl", str(input_jsonl), "--device", "cpu", "--json"]) == 0
    captured = capsys.readouterr()
    assert "upstream diagnostic" not in captured.out
    assert "upstream diagnostic" in captured.err
    assert json.loads(captured.out)["language"] == "en"


def test_firered_lid_node_local_runner_parses_jsonl(monkeypatch: pytest.MonkeyPatch) -> None:
    from sure_eval.evaluation.nodes.inference.firered_lid import NodeLocalFireRedLIDRunner

    runner = NodeLocalFireRedLIDRunner(device="cpu")
    payload = {
        "sample_id": "utt1",
        "audio_path": "/tmp/utt1.wav",
        "language": "en",
        "confidence": 0.99,
    }
    monkeypatch.setattr(
        runner,
        "_run_node",
        lambda args: SimpleNamespace(stdout=json.dumps(payload) + "\n"),
    )
    assert runner.predict_batch(["utt1"], ["/tmp/utt1.wav"])[0]["language"] == "en"


def test_modelscope_download_uses_pinned_revision_and_local_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from sure_eval.evaluation.cli import _download_asset

    calls = []
    monkeypatch.setitem(
        sys.modules,
        "modelscope",
        SimpleNamespace(snapshot_download=lambda **kwargs: calls.append(kwargs)),
    )
    target = tmp_path / "FireRedLID" / "model.pth.tar"
    _download_asset(
        {
            "provider": "modelscope",
            "id": "FireRedTeam/FireRedLID",
            "revision": "pinned",
            "layout": "local_dir",
            "target_path": str(target),
        }
    )
    assert calls == [
        {
            "model_id": "FireRedTeam/FireRedLID",
            "revision": "pinned",
            "local_dir": str(target.parent),
        }
    ]


def test_modelscope_download_preserves_legacy_cache_layout_without_new_fields(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from sure_eval.evaluation.cli import _download_asset

    calls = []
    monkeypatch.setitem(
        sys.modules,
        "modelscope",
        SimpleNamespace(snapshot_download=lambda **kwargs: calls.append(kwargs)),
    )
    target = tmp_path / "modelscope" / "models" / "iic" / "example" / "model.pt"
    _download_asset(
        {
            "provider": "modelscope",
            "id": "iic/example",
            "target_path": str(target),
        }
    )
    assert calls == [
        {
            "model_id": "iic/example",
            "cache_dir": str(target.parents[1]),
        }
    ]


def test_downloaded_asset_checksum_is_enforced(tmp_path: Path) -> None:
    import hashlib

    from sure_eval.evaluation.cli import _verify_downloaded_asset

    target = tmp_path / "model.bin"
    target.write_bytes(b"model")
    expected = hashlib.sha256(b"model").hexdigest()
    _verify_downloaded_asset({"target_path": str(target), "sha256": expected})
    with pytest.raises(RuntimeError, match="checksum mismatch"):
        _verify_downloaded_asset({"target_path": str(target), "sha256": "0" * 64})


def test_firered_lid_prepare_stages_locked_source(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from sure_eval.evaluation.nodes.inference.firered_lid import prepare_firered_lid

    node_dir = tmp_path / "node"
    node_dir.mkdir()
    lock = {
        "repository": "https://example.test/FireRedASR2S.git",
        "revision": "a" * 40,
        "subdirectory": "fireredasr2s/fireredlid",
        "source_tree": "b" * 40,
        "license_file": "LICENSE",
    }
    lock_file = node_dir / "source_lock.json"
    lock_file.write_text(json.dumps(lock), encoding="utf-8")
    runtime_dir = node_dir / "runtime"
    monkeypatch.setattr(prepare_firered_lid, "NODE_DIR", node_dir)
    monkeypatch.setattr(prepare_firered_lid, "LOCK_FILE", lock_file)
    monkeypatch.setattr(prepare_firered_lid, "RUNTIME_DIR", runtime_dir)
    monkeypatch.setattr(
        prepare_firered_lid,
        "MARKER_FILE",
        runtime_dir / "fireredlid_revision.json",
    )

    def fake_checkout(checkout_dir: Path, source_lock: dict[str, str]) -> str:
        package_dir = checkout_dir / source_lock["subdirectory"]
        package_dir.mkdir(parents=True)
        (package_dir / "__init__.py").write_text("", encoding="utf-8")
        (checkout_dir / "LICENSE").write_text("Apache", encoding="utf-8")
        return "b" * 40

    monkeypatch.setattr(prepare_firered_lid, "_checkout_source", fake_checkout)
    assert prepare_firered_lid.prepare()["status"] == "prepared"
    assert prepare_firered_lid.prepare()["status"] == "already_prepared"
    assert (runtime_dir / "fireredlid" / "__init__.py").is_file()
    assert (runtime_dir / "LICENSE").read_text(encoding="utf-8") == "Apache"


def test_firered_lid_source_lock_matches_manifest() -> None:
    import yaml

    node_dir = (
        Path(__file__).resolve().parents[1] / "src/sure_eval/evaluation/nodes/inference/firered_lid"
    )
    source_lock = json.loads((node_dir / "source_lock.json").read_text(encoding="utf-8"))
    manifest = yaml.safe_load((node_dir / "manifest.yaml").read_text(encoding="utf-8"))
    assert manifest["runtime"]["source_repository"] == source_lock["repository"]
    assert manifest["runtime"]["source_revision"] == source_lock["revision"]
    assert manifest["runtime"]["source_subdirectory"] == source_lock["subdirectory"]
    assert manifest["runtime"]["source_tree"] == source_lock["source_tree"]


def test_lid_env_check_accepts_external_model_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from sure_eval.evaluation import env_check

    node_dir = tmp_path / "inference" / "firered_lid"
    (node_dir / ".venv" / "bin").mkdir(parents=True)
    (node_dir / ".venv" / "bin" / "python3.11").write_bytes(b"")
    (node_dir / "runtime" / "fireredlid").mkdir(parents=True)
    (node_dir / "runtime" / "fireredlid_revision.json").write_text("{}")
    (node_dir / "runtime" / "fireredlid" / "__init__.py").write_text("")
    (node_dir / "runtime" / "LICENSE").write_text("Apache")
    model_dir = tmp_path / "external-model"
    model_dir.mkdir()
    for name in ("model.pth.tar", "cmvn.ark", "dict.txt"):
        (model_dir / name).write_bytes(b"model")
    source_node_env = Path("src/sure_eval/evaluation/nodes/inference/firered_lid/node_env.yaml")
    import yaml

    node_env = yaml.safe_load(source_node_env.read_text(encoding="utf-8"))
    node_env["verify"]["import_check"] = False
    node_env["models"][0].pop("sha256")
    (node_dir / "node_env.yaml").write_text(yaml.safe_dump(node_env))
    monkeypatch.setenv("FIRERED_LID_CHECKPOINT", str(model_dir))
    monkeypatch.setattr(env_check, "load_node_manifest", lambda node_id: ({}, node_dir))

    result = env_check.NodeEnvChecker(nodes_root=tmp_path).check_node("inference/firered_lid")
    assert result.status == "ok"
    assert result.details["checkpoint_path"] == str(model_dir / "model.pth.tar")


def test_env_check_preserves_existing_directory_checkpoint_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sure_eval.evaluation import env_check

    checker = env_check.NodeEnvChecker()
    node_path = Path("src/sure_eval/evaluation/nodes/transcription/qwen3_asr_1_7b")
    node_env = checker.load_node_env("transcription/qwen3_asr_1_7b")
    monkeypatch.setenv("QWEN3_ASR_1_7B_CHECKPOINT", "/tmp")

    checkpoint_path, checkpoint_env = checker._checkpoint_path(
        "transcription/qwen3_asr_1_7b",
        node_path,
        node_env,
    )
    assert checkpoint_env == "QWEN3_ASR_1_7B_CHECKPOINT"
    assert checkpoint_path == Path("/tmp")


@pytest.mark.parametrize("declare_sha256", [False, True])
def test_env_check_skips_or_accepts_declared_checkpoint_checksum(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    declare_sha256: bool,
) -> None:
    import hashlib
    import yaml

    from sure_eval.evaluation import env_check

    node_dir = tmp_path / "inference" / "example"
    (node_dir / ".venv" / "bin").mkdir(parents=True)
    (node_dir / ".venv" / "bin" / "python3.11").write_bytes(b"")
    checkpoint = node_dir / "checkpoints" / "model.bin"
    checkpoint.parent.mkdir()
    checkpoint.write_bytes(b"model")
    model = {"id": "example/model", "target": "checkpoints/model.bin"}
    if declare_sha256:
        model["sha256"] = hashlib.sha256(b"model").hexdigest()
    (node_dir / "node_env.yaml").write_text(
        yaml.safe_dump(
            {
                "runtime": {"type": "uv", "python": "3.11"},
                "models": [model],
                "verify": {"files": []},
            }
        )
    )
    monkeypatch.setattr(env_check, "load_node_manifest", lambda node_id: ({}, node_dir))

    result = env_check.NodeEnvChecker(nodes_root=tmp_path).check_node("inference/example")
    assert result.status == "ok"
    if declare_sha256:
        assert result.details["checkpoint_sha256"] == model["sha256"]
    else:
        assert "checkpoint_sha256" not in result.details


def test_env_check_rejects_bad_checkpoint_checksum(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import yaml

    from sure_eval.evaluation import env_check

    node_dir = tmp_path / "inference" / "example"
    (node_dir / ".venv" / "bin").mkdir(parents=True)
    (node_dir / ".venv" / "bin" / "python3.11").write_bytes(b"")
    checkpoint = node_dir / "checkpoints" / "model.bin"
    checkpoint.parent.mkdir()
    checkpoint.write_bytes(b"corrupt")
    (node_dir / "node_env.yaml").write_text(
        yaml.safe_dump(
            {
                "runtime": {"type": "uv", "python": "3.11"},
                "models": [
                    {
                        "id": "example/model",
                        "target": "checkpoints/model.bin",
                        "sha256": "0" * 64,
                    }
                ],
            }
        )
    )
    monkeypatch.setattr(env_check, "load_node_manifest", lambda node_id: ({}, node_dir))

    result = env_check.NodeEnvChecker(nodes_root=tmp_path).check_node("inference/example")
    assert result.status == "failed"
    assert "checkpoint checksum failed" in result.message


def test_firered_lid_runner_fills_upstream_skipped_samples(tmp_path: Path) -> None:
    from sure_eval.evaluation.nodes.inference.firered_lid import FireRedLIDRunner

    class SkippingModel:
        def process(self, sample_ids, audio_paths):
            return [{"uttid": sample_ids[0], "wav": audio_paths[0], "lang": "en"}]

    runner = FireRedLIDRunner(device="cpu", model_dir=tmp_path, batch_size=2)
    runner._model = SkippingModel()
    rows = runner.predict_batch(["one", "short"], ["one.wav", "short.wav"])
    assert [row["uttid"] for row in rows] == ["one", "short"]
    assert rows[1]["lang"] == ""


def test_node_local_import_check_reports_runtime_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    from sure_eval.evaluation import env_check

    monkeypatch.setattr(
        env_check.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=1,
            stdout="",
            stderr="ModuleNotFoundError: No module named 'missing_runtime_dependency'\n",
        ),
    )
    assert env_check._check_node_local_imports(Path("python"), ["kaldiio"]) == (
        "ModuleNotFoundError: No module named 'missing_runtime_dependency'"
    )


def test_node_local_import_check_does_not_inherit_pythonpath(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sure_eval.evaluation import env_check

    observed = {}

    def fake_run(*args, **kwargs):
        observed.update(kwargs["env"])
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setenv("PYTHONPATH", "/host/private/modules")
    monkeypatch.setattr(env_check.subprocess, "run", fake_run)
    assert env_check._check_node_local_imports(Path("python"), ["example"]) == ""
    assert "PYTHONPATH" not in observed
    assert observed["PYTHONNOUSERSITE"] == "1"


def test_firered_lid_real_model_smoke_when_prepared() -> None:
    from sure_eval.evaluation.env_check import NodeEnvChecker
    from sure_eval.evaluation.nodes.inference.firered_lid import NodeLocalFireRedLIDRunner
    from sure_eval.evaluation.nodes.normalization.lid_label import normalize_lid_label

    check = NodeEnvChecker().check_node("inference/firered_lid")
    if check.status != "ok":
        pytest.skip(f"FireRedLID node is not prepared: {check.message}")
    repo_root = Path(__file__).resolve().parents[1]
    sample_ids = ["aishell-zh", "librispeech-en"]
    audio_paths = [
        str(repo_root / "tests/fixtures/aishell1-test/sample_1_BAC009S0764W0385.wav"),
        str(repo_root / "tests/fixtures/librispeech/sample_1_367-130732-0006.wav"),
    ]
    rows = NodeLocalFireRedLIDRunner(device="cpu").predict_batch(sample_ids, audio_paths)
    assert [row["sample_id"] for row in rows] == sample_ids
    assert [normalize_lid_label(row["language"]) for row in rows] == ["zh-mandarin", "en"]
