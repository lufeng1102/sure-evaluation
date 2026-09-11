"""SD configured script route descriptors."""

from __future__ import annotations

from sure_eval.evaluation.scripts.contracts import (
    call_route_executor,
    contract_from_manifest,
    describe_from_contracts,
    find_pipeline_route,
    find_task_route,
    load_task_manifest,
    load_task_routes,
    route_execution_metric,
    write_route_run_outputs,
)


def describe_pipeline(*, metric: str = "der", pipeline_id: str | None = None):
    manifest, manifest_path, routes_path, route, normalized_metric = _select_route(
        metric=metric, pipeline_id=pipeline_id
    )
    return describe_from_contracts(
        task="SD",
        pipeline_id=route["pipeline_id"],
        metric=normalized_metric,
        language="n/a",
        node_ids=tuple(route["nodes"]),
        contracts=(contract_from_manifest(manifest, route["input_contract"]),),
        task_config_path=manifest_path,
        route_config_path=routes_path,
        computation_node_ids=tuple(route["nodes"]),
        execution_metrics=(normalized_metric,),
        script_module=__name__,
        executor=str(route.get("executor") or ""),
    )


def run(
    ref_file: str,
    hyp_file: str,
    *,
    output_dir: str,
    metric: str = "der",
    pipeline_id: str | None = None,
    collar: float | None = None,
):
    if not output_dir:
        raise ValueError("output_dir is required")
    description = describe_pipeline(metric=metric, pipeline_id=pipeline_id)
    _, _, _, route, normalized_metric = _select_route(metric=metric, pipeline_id=pipeline_id)
    params = dict(route.get("params") or {})
    if collar is not None:
        params["collar"] = collar
    report = call_route_executor(
        route,
        ref_file=ref_file,
        hyp_file=hyp_file,
        metric=normalized_metric,
        scorer=_executor_selectors_from_route(route).get("scorer"),
        **params,
    )
    return write_route_run_outputs(report=report, description=description, output_dir=output_dir)


def _executor_selectors_from_route(route: dict) -> dict[str, str]:
    selectors: dict[str, str] = {}
    for node_id in route.get("nodes") or ():
        if node_id == "scoring/meeteval":
            selectors["scorer"] = "meeteval"
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


def _select_route(*, metric: str = "der", pipeline_id: str | None = None):
    manifest, manifest_path = load_task_manifest("sd")
    normalized_metric = metric.lower()
    routes, routes_path = load_task_routes("sd")
    if pipeline_id:
        route = find_pipeline_route(routes, pipeline_id=pipeline_id)
    else:
        route = find_task_route(routes, metric=normalized_metric)
    return manifest, manifest_path, routes_path, route, route_execution_metric(route) or normalized_metric
