"""SLU configured script route descriptors."""

from __future__ import annotations

from sure_eval.evaluation.scripts.contracts import (
    call_route_executor,
    contract_from_manifest,
    describe_from_contracts,
    find_pipeline_route,
    find_task_route,
    load_task_manifest,
    load_task_routes,
    write_route_run_outputs,
)


def describe_pipeline(
    *, metric: str = "accuracy", output_mode: str = "choice_id", pipeline_id: str | None = None
):
    if metric.lower() != "accuracy":
        raise ValueError(f"Unsupported SLU metric: {metric}")
    manifest, manifest_path, routes_path, route = _select_route(
        output_mode=output_mode, pipeline_id=pipeline_id
    )
    return describe_from_contracts(
        task="SLU",
        pipeline_id=route["pipeline_id"],
        metric="accuracy",
        language="n/a",
        node_ids=tuple(route["nodes"]),
        contracts=(contract_from_manifest(manifest, route["input_contract"]),),
        task_config_path=manifest_path,
        route_config_path=routes_path,
        computation_node_ids=tuple(route["nodes"]),
        execution_metrics=("accuracy",),
        script_module=__name__,
        executor=str(route.get("executor") or ""),
    )


def run(
    ref_file: str,
    hyp_file: str,
    *,
    prompt_jsonl: str,
    output_dir: str,
    output_mode: str = "choice_id",
    pipeline_id: str | None = None,
):
    if not output_dir:
        raise ValueError("output_dir is required")
    _, _, _, route = _select_route(output_mode=output_mode, pipeline_id=pipeline_id)
    resolved_output_mode = str(route.get("output_mode") or output_mode)
    description = describe_pipeline(output_mode=resolved_output_mode, pipeline_id=pipeline_id)
    selectors = _executor_selectors_from_route(route)
    report = call_route_executor(
        route,
        ref_file=ref_file,
        hyp_file=hyp_file,
        prompt_jsonl=prompt_jsonl,
        output_mode=resolved_output_mode,
        normalizer=selectors.get("normalizer"),
        scorer=selectors.get("scorer"),
    )
    return write_route_run_outputs(report=report, description=description, output_dir=output_dir)


def _executor_selectors_from_route(route: dict) -> dict[str, str]:
    selectors: dict[str, str] = {}
    for node_id in route.get("nodes") or ():
        if node_id == "normalization/prompt_norm":
            selectors["normalizer"] = "prompt_norm"
        elif node_id == "scoring/classify":
            selectors["scorer"] = "classify"
        else:
            _apply_external_selectors(selectors, node_id)
    return selectors


def _apply_external_selectors(selectors: dict[str, str], node_id: str) -> None:
    """Fallback: read selector hints from an external (plugin) node registration."""

    from sure_eval.evaluation.node_registry import get_registry

    try:
        registration = get_registry().resolve(node_id)
    except KeyError:
        return
    selectors.update({key: str(value) for key, value in registration.selectors.items()})


def _select_route(*, output_mode: str = "choice_id", pipeline_id: str | None = None):
    manifest, manifest_path = load_task_manifest("slu")
    routes, routes_path = load_task_routes("slu")
    if pipeline_id:
        route = find_pipeline_route(routes, pipeline_id=pipeline_id)
    else:
        route = find_task_route(routes, metric="accuracy", output_mode=output_mode)
    return manifest, manifest_path, routes_path, route
