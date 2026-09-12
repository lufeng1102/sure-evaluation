# Documentation

## Use SURE-EVALUATION

- [Installation](installation.md): clean source installation and base smoke test
- [Task Guides](tasks/README.md): metrics, input contracts, exact pipelines, and examples
- [Environment Management](environment.md): prepare only selected optional nodes
- [Pipeline Catalog](pipeline_catalog.md): committed route-to-node inventory
- [Pipeline Atlas](atlas/index.html): the catalog drawn as one interactive map from task to report
- [Reproducibility](reproducibility.md): identity, reports, locks, and runtime assets
- [Plugin Management](plugin_management.md): project-local plugin declarations,
  locks, loading precedence, and the remote-code trust boundary

The standard user flow is:

```bash
sure-eval metric routes <task> --language <lang> --metric <metric>
sure-eval metric describe <task> --pipeline-id <pipeline-id> --output pipeline.json
sure-eval env setup --pipeline pipeline.json --dry-run
sure-eval env check --pipeline pipeline.json
sure-eval metric run --pipeline pipeline.json ...
```

For a project-local node or route plugin, add its directory once and then use
the same standard flow without repeating `--extra-node-path`:

```bash
sure-eval plugin add ./plugins/my_plugin
sure-eval plugin list
sure-eval plugin check my_plugin
sure-eval metric routes <task> --language <lang> --metric <metric> --json
```

A plugin directory may contain only `node.py`, only `routes.py`, or both.
`.sure-eval/plugins.yaml` declares the project plugins and
`.sure-eval/plugins.lock.json` fixes their inspected content. Use installed
entry points for formal Python distribution and reserve `--extra-node-path`
for temporary debugging. See [Plugin Management](plugin_management.md) for
the complete add, use, sync, and remove lifecycle.

## Contribute

Start with [Contributing](contributing.md). It classifies the PR and links to a
focused guide for a new task, metric, route, node/tool version, or maintenance
change. Use [Add Evaluation Capabilities](add_a_metric.md) when the category is
unclear.

To add a versioned node that ships outside the repository (a plugin), see
[Add an External Node](add_external_node.md) — it walks through writing
`node.py`, registering an entry point, injecting a route, and running the result.
The project-level plugin management architecture is documented in
[Plugin Management](plugin_management.md); the local-path `add/list/check/remove/sync`
commands are now available for node-only, route-only, and node-and-route
plugins. Open-Bench download and remote revision support remain planned for
the second phase.

## Agent Integration

Agents and evaluation harnesses should follow the
[Agent Contract](agent_contract.md). Machine consumers should prefer
`metric routes --json`, exact pipeline IDs, pipeline-based environment
commands, and the structured run artifacts.
