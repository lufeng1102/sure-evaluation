"""Real ``pip install -e`` integration test for entry-point node discovery.

This test mutates the project virtualenv (install + uninstall the example
package) and shells out to the CLI, so it is marked ``integration`` and excluded
from the default pytest run.  Run it explicitly with ``-m integration``.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_PACKAGE = "sure-eval-node-lowercase"

_UNIFIED_PACKAGES = [
    (
        "node_only_plugin",
        "sure-eval-node-only-plugin",
        ("node", "list", "--json"),
        "normalization/example_identity",
    ),
    (
        "pipeline_plugin_cer",
        "sure-eval-pipeline-plugin-cer",
        ("metric", "routes", "asr", "--language", "en", "--metric", "cer", "--json"),
        "asr.en.cer.aispeech_norm_en_v1.wenet_cer_v1",
    ),
    (
        "node_pipeline_plugin_wer",
        "sure-eval-node-pipeline-plugin-wer",
        ("metric", "routes", "asr", "--language", "en", "--metric", "wer", "--json"),
        "asr.en.wer.example_lowercase_v1.wenet_wer_v1",
    ),
]


def _venv_bin(name: str) -> Path:
    return _ROOT / ".venv" / "bin" / name


@pytest.mark.integration
def test_entry_point_installed_package_cli_end_to_end(tmp_path: Path) -> None:
    python = _venv_bin("python")
    pip = _venv_bin("pip")
    sure_eval = _venv_bin("sure-eval")
    package_dir = _ROOT / "examples" / "node_plugin_lowercase"

    if not (python.exists() and pip.exists() and sure_eval.exists()):
        pytest.skip("requires the project virtualenv (`.venv`) with the sure-eval console script")

    cache_dir = tmp_path / "cache"
    env = {**os.environ, "SURE_EVAL_CACHE_DIR": str(cache_dir)}

    def run(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(arg) for arg in args],
            capture_output=True,
            text=True,
            cwd=_ROOT,
            env=env,
        )

    install = run(pip, "install", "-e", str(package_dir), "--no-build-isolation")
    assert install.returncode == 0, install.stderr

    try:
        # 1) routes: the installed package injects its route via entry point
        routes = run(
            sure_eval,
            "metric",
            "routes",
            "asr",
            "--language",
            "en",
            "--metric",
            "wer",
            "--json",
        )
        assert routes.returncode == 0, routes.stderr
        pipeline_ids = [route["pipeline_id"] for route in json.loads(routes.stdout)["routes"]]
        assert "asr.en.wer.lowercase_norm_v1.wenet_wer_v1" in pipeline_ids

        # 2) describe: resolves the external node via the installed entry point
        pipeline_path = tmp_path / "pipeline.json"
        describe = run(
            sure_eval,
            "metric",
            "describe",
            "asr",
            "--pipeline-id",
            "asr.en.wer.lowercase_norm_v1.wenet_wer_v1",
            "--output",
            str(pipeline_path),
            "--json",
        )
        assert describe.returncode == 0, describe.stderr

        # 3) run: the executor dispatches the external lowercase_norm node
        output_dir = tmp_path / "out"
        run_result = run(
            sure_eval,
            "metric",
            "run",
            "--pipeline",
            str(pipeline_path),
            "--ref-file",
            str(_ROOT / "examples" / "readme" / "asr_en_ref.txt"),
            "--hyp-file",
            str(_ROOT / "examples" / "readme" / "asr_en_hyp.txt"),
            "--output-dir",
            str(output_dir),
            "--json",
        )
        assert run_result.returncode == 0, run_result.stderr
        payload = json.loads(run_result.stdout)
        assert payload["pipeline_id"] == "asr.en.wer.lowercase_norm_v1.wenet_wer_v1"
        report = json.loads((output_dir / "report.json").read_text(encoding="utf-8"))
        assert [entry["node_id"] for entry in report["pipeline_trace"]] == [
            "normalization/lowercase_norm",
            "scoring/wenet_wer",
        ]
    finally:
        run(pip, "uninstall", "-y", _PACKAGE)


@pytest.mark.integration
@pytest.mark.parametrize(("directory", "distribution", "command", "expected"), _UNIFIED_PACKAGES)
def test_unified_layout_entry_point_discovery(
    tmp_path: Path,
    directory: str,
    distribution: str,
    command: tuple[str, ...],
    expected: str,
) -> None:
    python = _venv_bin("python")
    pip = _venv_bin("pip")
    sure_eval = _venv_bin("sure-eval")
    if not (python.exists() and pip.exists() and sure_eval.exists()):
        pytest.skip("requires the project virtualenv (`.venv`) with the sure-eval console script")

    env = {**os.environ, "SURE_EVAL_CACHE_DIR": str(tmp_path / "cache")}

    def run(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(arg) for arg in args],
            capture_output=True,
            text=True,
            cwd=tmp_path,
            env=env,
        )

    install = run(
        pip,
        "install",
        "-e",
        str(_ROOT / "examples" / directory),
        "--no-build-isolation",
    )
    assert install.returncode == 0, install.stderr
    try:
        discovered = run(sure_eval, *command)
        assert discovered.returncode == 0, discovered.stderr
        assert expected in discovered.stdout
    finally:
        run(pip, "uninstall", "-y", distribution)


@pytest.mark.integration
def test_unified_layout_both_channels_equivalent_report(tmp_path: Path) -> None:
    """plugin add 与 pip install 两条通道对同一 pipeline 产出等价 report。

    先走 plugin add 通道（此时尚无同名 entry point），再 pip install -e 走
    entry point 通道（用干净的 project_dir 隔离，避免两条通道同时注册触发
    Duplicate external node_id）。两条通道产出的 report 在 pipeline_id、
    metric、score 和 pipeline_trace 的 node_id 链上必须完全一致。
    """

    python = _venv_bin("python")
    pip = _venv_bin("pip")
    sure_eval = _venv_bin("sure-eval")
    if not (python.exists() and pip.exists() and sure_eval.exists()):
        pytest.skip("requires the project virtualenv (`.venv`) with the sure-eval console script")

    directory = "node_pipeline_plugin_wer"
    distribution = "sure-eval-node-pipeline-plugin-wer"
    pipeline_id = "asr.en.wer.example_lowercase_v1.wenet_wer_v1"
    package_dir = _ROOT / "examples" / directory
    ref_file = _ROOT / "examples" / "readme" / "asr_en_ref.txt"
    hyp_file = _ROOT / "examples" / "readme" / "asr_en_hyp.txt"
    env = {**os.environ, "SURE_EVAL_CACHE_DIR": str(tmp_path / "cache")}

    def run(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(arg) for arg in args],
            capture_output=True,
            text=True,
            cwd=cwd,
            env=env,
        )

    def run_report(project: Path) -> dict[str, object]:
        pipeline_path = project / "pipeline.json"
        output_dir = project / "out"
        describe = run(
            project,
            sure_eval,
            "--project-dir", str(project),
            "metric", "describe", "asr",
            "--pipeline-id", pipeline_id,
            "--output", str(pipeline_path),
            "--json",
        )
        assert describe.returncode == 0, describe.stderr
        run_result = run(
            project,
            sure_eval,
            "--project-dir", str(project),
            "metric", "run",
            "--pipeline", str(pipeline_path),
            "--ref-file", str(ref_file),
            "--hyp-file", str(hyp_file),
            "--output-dir", str(output_dir),
            "--json",
        )
        assert run_result.returncode == 0, run_result.stderr
        return json.loads((output_dir / "report.json").read_text(encoding="utf-8"))

    # 通道 1：plugin add（先跑，此时尚无同名 entry point）
    proj_add = tmp_path / "proj_add"
    proj_add.mkdir()
    add = run(
        proj_add,
        sure_eval,
        "--project-dir", str(proj_add),
        "plugin", "add", str(package_dir), "--json",
    )
    assert add.returncode == 0, add.stderr
    report_add = run_report(proj_add)

    # 通道 2：pip install -e（entry point 全局生效，用干净 project_dir 隔离）
    install = run(tmp_path, pip, "install", "-e", str(package_dir), "--no-build-isolation")
    assert install.returncode == 0, install.stderr
    try:
        proj_ep = tmp_path / "proj_ep"
        proj_ep.mkdir()
        report_ep = run_report(proj_ep)
    finally:
        run(tmp_path, pip, "uninstall", "-y", distribution)

    # 两条通道的 report 等价
    assert report_add["pipeline_id"] == report_ep["pipeline_id"] == pipeline_id
    assert report_add["metric"] == report_ep["metric"]
    assert report_add["score"] == report_ep["score"]
    assert [entry["node_id"] for entry in report_add["pipeline_trace"]] == [
        entry["node_id"] for entry in report_ep["pipeline_trace"]
    ]
