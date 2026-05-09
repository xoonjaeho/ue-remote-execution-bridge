# UE Python API Cheatsheet

Frequently-called `unreal.*` APIs used via the `run_python` MCP tool, with signatures, examples, and usage counts.

> **Python API escape hatch.** When `unreal.*` is missing a symbol, don't loop on Python workarounds — add a `UFUNCTION` to `Plugins/RemoteExecutionBridge/`. Recipe and catalog: [mcp README §C++ Plugin Extension](../README.md#c-plugin-extension-python-api-escape-hatch).

## How this file is maintained

Frequency table rows = persistent state. `usage.log` = ephemeral delta. `update_cheatsheet.py` extracts `unreal.X.Y` patterns and folds them in:

- Existing row → `Count` += delta; `Last seen` ← delta date.
- New API → append row.
- Row absent from delta → unchanged.

`--truncate-usage` clears the raw log after a verified merge. Internal `start_pie`/`stop_pie`/`tail_output_log` bypass logging, so counts reflect user usage only.

## Frequency table

_Empty — populated by `update_cheatsheet.py` after server usage._

| API | Count | Last seen | Notes |
|---|---:|---|---|
| _empty_ | — | — | — |

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

Custom plugin APIs (not stock `unreal.*`). Full UFUNCTION catalog: [mcp README §C++ Plugin Extension](../README.md#c-plugin-extension-python-api-escape-hatch).

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
