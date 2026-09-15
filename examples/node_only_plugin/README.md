# Example: node-only plugin

This unified-layout plugin adds `normalization/example_identity` without adding
a route. Register the same directory through either channel:

```bash
sure-eval plugin add examples/node_only_plugin
sure-eval node list --json
```

or:

```bash
python -m pip install -e examples/node_only_plugin
sure-eval node list --json
```

Do not activate both channels in the same project. A node-only plugin becomes
part of scoring only after a route references its `NODE_ID`.
