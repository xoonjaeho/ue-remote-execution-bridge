# UE Python API Cheatsheet

Frequently-called `unreal.*` APIs used via the `run_python` MCP tool, with signatures, examples, and usage counts.

> **Heads up — Python API escape hatch.** When a needed symbol is missing from `unreal.*` (typical sign: `AttributeError: module 'unreal' has no attribute …`), do not chase a Python workaround past two attempts. Add a `UFUNCTION` to the companion C++ plugin (`Plugins/RemoteExecutionBridge/` in this repo) and call it from Python. Recipe and UFUNCTION catalog: [mcp README §C++ Plugin Extension](../README.md#c-plugin-extension-python-api-escape-hatch). System overview and naming map: [repo root README §How it works](../../../README.md#how-it-works).

## How this file is maintained

Incremental aggregation. The Frequency table rows are the persistent state; `usage.log` is an ephemeral delta.

1. `server.py::run_python` appends every invocation to `mcp/ue_remote_execution_bridge/usage.log` (timestamp + raw code).
2. When asked (e.g. "update CHEATSHEET from usage.log"), Claude extracts `unreal.X.Y` patterns via regex/AST and produces a delta (per-API count + last-seen).
3. Merge rules:
   - Existing row + delta → `Count` += delta count; `Last seen` ← delta date (overwrite).
   - New API in delta → append new row.
   - Row not present in delta → keep unchanged.
4. Truncate `usage.log` to zero bytes after the merge is committed. The delta is now folded into the table; the raw log is no longer needed.

Internal `start_pie` / `stop_pie` / `tail_output_log` snippets bypass logging (they call `_run` directly), so counts reflect user-driven usage only.

## Frequency table

_Last aggregated: never. Empty template — populate by using the server and asking Claude to "fold usage.log into CHEATSHEET"._

| API | Count | Last seen | Notes |
|---|---:|---|---|
| _empty_ | — | — | Run a few `run_python` calls, then ask Claude to update this table from `usage.log`. |

## Common snippets

Hand-curated, grown from observed usage.

### Object lookup by full UObject path

```python
import unreal

# Find any UObject by its full path (package.outer:object format)
# Useful for locating specific graph nodes when you know the path from a prior inspection
node = unreal.find_object(
    None,
    "/Game/Foo/Bar/MyBP.MyBP:EventGraph.K2Node_CallFunction_79"
)
if node:
    print(node.get_name(), node.get_class().get_name())
```

### Asset scanning (inventory / class breakdown)

```python
import unreal, collections

root_paths = ["/Game/Foo", "/Game/Bar"]
class_counts = collections.Counter()

for root in root_paths:
    assets = unreal.EditorAssetLibrary.list_assets(root, recursive=True, include_folder=False)
    for path in assets:
        data = unreal.EditorAssetLibrary.find_asset_data(path)
        if data.is_valid():
            class_counts[str(data.asset_class_path.asset_name)] += 1

for cls, cnt in class_counts.most_common(20):
    print(f"{cls}: {cnt}")
```

### RemoteExecutionBridge C++ plugin APIs

These APIs are exposed via `Plugins/RemoteExecutionBridge/` — NOT part of the standard `unreal` module. Split across two UE modules (both load automatically with the editor):

- **`RemoteExecutionBridge`** (runtime) — `RemoteExecutionBridgeLibrary` (heartbeat, session metadata)
- **`RemoteExecutionBridgeEditor`** (editor-only) — `BlueprintEditorUtilityLibrary`, `MaterialEditorUtilityLibrary`

#### Blueprint graph traversal

```python
import unreal

bp = unreal.load_asset("/Game/Path/To/MyBlueprint")
lib = unreal.BlueprintEditorUtilityLibrary

# List all graphs in a blueprint (EventGraph, function graphs, macro graphs, etc.)
graphs = lib.get_blueprint_graphs(bp)
for g in graphs:
    print(g.get_name())

# List all nodes in a specific graph
nodes = lib.get_graph_nodes(graphs[0])
for node in nodes:
    print(node.get_class().get_name(), node.node_comment)

# Find all K2Node_CallFunction nodes that call a given function (searches all graphs)
nodes = lib.find_nodes_by_function_name(bp, "SomeFunctionName")
# Returns a list of UEdGraphNode objects; empty list if none found.
# Works even when the function doesn't resolve (e.g. disabled plugin) — uses serialized MemberName.

# Delete a node (breaks all pin links, removes from graph)
for node in nodes:
    lib.delete_blueprint_node(bp, node)

# Mark blueprint modified, then save + recompile
lib.mark_blueprint_modified(bp)
unreal.EditorAssetLibrary.save_asset("/Game/Path/To/MyBlueprint")
unreal.BlueprintEditorLibrary.compile_blueprint(bp)
```

#### Blueprint graph lookup & removal

```python
import unreal

bp = unreal.load_asset("/Game/Path/To/MyBlueprint")

# Get the main EventGraph
event_graph = unreal.BlueprintEditorLibrary.find_event_graph(bp)

# Find a function or macro graph by name
vr_graph = unreal.BlueprintEditorLibrary.find_graph(bp, "ResetVROrientation")

# Remove a graph (function, macro, or nested graph) — wrap in a transaction for undo support
if vr_graph:
    with unreal.ScopedEditorTransaction("Remove ResetVR Graph"):
        unreal.BlueprintEditorLibrary.remove_graph(bp, vr_graph)
    unreal.BlueprintEditorLibrary.compile_blueprint(bp)
    unreal.EditorAssetLibrary.save_asset("/Game/Path/To/MyBlueprint", only_if_is_dirty=True)

# Remove all nodes with no connections (cleanup pass)
unreal.BlueprintEditorLibrary.remove_unused_nodes(bp)
```

#### Alternative compile (KismetEditorUtilities)

```python
import unreal

bp = unreal.load_asset("/Game/Path/To/MyBlueprint")

# Lower-level compile — returns EBlueprintCompileOptions result
result = unreal.KismetEditorUtilities.compile_blueprint(bp)
print(f"Compile result: {result}")
```

#### Material expression enumeration

```python
import unreal

mat = unreal.load_asset("/Game/Path/To/MyMaterial")
lib = unreal.MaterialEditorUtilityLibrary

expressions = lib.get_material_expressions(mat)
for expr in expressions:
    print(expr.get_class().get_name())
```

## References

- Full API: https://dev.epicgames.com/documentation/en-us/unreal-engine/python-api/?application_version=5.7
- Design & decisions: [DESIGN.md](./DESIGN.md)
