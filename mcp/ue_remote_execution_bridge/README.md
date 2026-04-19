# UE Remote Execution Bridge — MCP Server

Exposes Unreal Editor 5.7's Python interpreter as MCP tools, enabling Claude to execute arbitrary Python inside the editor, control PIE, and stream the Output Log.

Architecture and design decisions: [docs/DESIGN.md](./docs/DESIGN.md)

## Files

| File | Role |
|---|---|
| `server.py` | FastMCP server — tool definitions, connection lifecycle management |
| `execute.py` | Epic's `remote_execution.py` (UDP discovery + TCP command client), renamed to avoid import-path collision |
| `docs/DESIGN.md` | Protocol, architecture, ADR |
| `docs/CHEATSHEET.md` | Frequently-used UE Python API reference (manually curated from `usage.log`) |
| `README.md` | This file |

## Prerequisites

Editor-side configuration (`Config/DefaultEngine.ini`):

```ini
[/Script/PythonScriptPlugin.PythonScriptPluginSettings]
bRemoteExecution=True
RemoteExecutionMulticastGroupEndpoint=239.0.0.1:6766
RemoteExecutionMulticastBindAddress=127.0.0.1
RemoteExecutionMulticastTtl=0
```

See `Config/DefaultEngine.ini.snippet` at the repo root for the full block to append.

- `PythonScriptPlugin` enabled in the project
- Editor must be **running** (commandlet/headless mode is not supported)
- Multicast `239.0.0.1:6766` must be reachable on the local loopback
- Firewall: allow UDP 6766 and the editor's TCP listen port (Private network profile)

Python runtime: 3.10+ (FastMCP requirement). Only one dependency: `pip install mcp` (or `pip install -r requirements.txt` from the repo root).

## MCP Registration

Copy `.mcp.json.example` from the repo root to your project root as `.mcp.json`:

```json
{
  "mcpServers": {
    "ue_remote_execution_bridge": {
      "command": "python",
      "args": ["mcp/ue_remote_execution_bridge/server.py"]
    }
  }
}
```

The path in `args` is relative to the directory Claude Code is launched from (your project root). If the `mcp/` folder is at a different location, adjust the path accordingly.

Alternatively, set the `UE_PROJECT_ROOT` environment variable to the absolute path of your UE project directory if you cannot use the mirrored layout above.

After adding or editing `.mcp.json`, restart Claude Code or run `/mcp` to reload. The server starts via stdio.

The MCP server starts regardless of whether the editor is running — if the editor is off, it waits silently and retries discovery on the first tool call. Confirm the connection: after the first tool call, look for `[MCP] ue_remote_execution_bridge server connected` in the UE Output Log.

## Exposed Tools

> SoT — this table is referenced from `docs/DESIGN.md §4.3`. Edit here when signatures change.

| Tool | Parameters (defaults) | Behavior |
|---|---|---|
| `run_python` | `code: str`, `mode: "exec_file"\|"exec_statement"\|"eval_statement" = "exec_file"`, `unattended: bool = True` | Execute Python inside the editor. Only `eval_statement` returns a value. |
| `start_pie` | — | `LevelEditorSubsystem.editor_request_begin_play()` |
| `stop_pie` | — | `LevelEditorSubsystem.editor_request_end_play()` |
| `tail_output_log` | `since_offset: int\|None = None`, `filter_regex: str\|None = None`, `max_lines: int = 500` | Paginate `Saved/Logs/<Project>.log` using a byte-offset cursor (max 256 KB per call). Parses timestamp, category, and verbosity. |

## Connection Lifecycle

- **Eager connect**: 2-second discovery on server startup. Connects immediately if the editor is running; skips silently otherwise.
- **Per-call connect**: Each tool call opens a fresh UDP discovery + TCP handshake (~100–200 ms overhead) and closes the connection when the call completes. Connection reuse is deferred to a later revision.
- A single `[MCP] ue_remote_execution_bridge server connected` line is written to the editor Output Log on the first successful heartbeat (not repeated per tool call, to avoid spam).
- If the editor is not reachable, the call returns an error immediately.

## Troubleshooting

**`No Unreal Editor discovered within 5s`**
- Is the editor running?
- Is `bRemoteExecution=True`? Check Project Settings → Plugins → Python.
- Can `127.0.0.1` reach the multicast group? VPNs, containers, and strict firewalls may block it.
- Is another process holding UDP 6766? (Another UE editor instance, or an orphaned server process.)

**`Remote party failed to send a valid response!`**
- Returned payload too large. Increase `RemoteExecutionSendBufferSizeBytes` and `RemoteExecutionReceiveBufferSizeBytes` to 2 MiB or more.

**`tail_output_log` returns empty results**
- Log file may not be flushed yet. Run any Python from inside the editor, then retry.

## Usage Logging

Every `run_python` invocation appends the raw code to `usage.log` in the format `---\n<ISO timestamp>\n<code>\n` (internal `start_pie`/`stop_pie`/`tail_output_log` are not logged).

`usage.log` is an **ephemeral delta between aggregations** — ask Claude to "scan usage.log and update CHEATSHEET" to incrementally merge counts into `docs/CHEATSHEET.md` (cumulative count, updated last-seen), then truncate the log. Full procedure: `docs/CHEATSHEET.md §How this file is maintained`.

Security note: the raw code is stored in plaintext until aggregated. Do not pass credentials or secrets through `run_python`.

## Security

Remote Execution has **no authentication**. Keep `MulticastTtl=0` so packets never leave the local machine. Do not use on shared machines or LAN environments. See `docs/DESIGN.md §5` for details.

## C++ Plugin Extension (Python API Escape Hatch)

### When to escalate

Add a `UFUNCTION` to `Plugins/RemoteExecutionBridge/` (companion C++ plugin in this repo) instead of working around in Python when any of these are true:

- `AttributeError: module 'unreal' has no attribute …` or `'X' object has no attribute 'Y'`
- Symbol is absent from the [official Python API docs](https://dev.epicgames.com/documentation/en-us/unreal-engine/python-api/?application_version=5.7)
- Needed functionality requires `FBlueprintEditorUtils`, `FAssetToolsModule`, or other engine-internal `private:` members
- Two Python workaround attempts for the same goal both failed

### Existing UFUNCTION catalog

> SoT — this table is referenced from `docs/DESIGN.md §4.7`. Edit here when UFUNCTIONs are added or changed.

Check here before adding a new UFUNCTION.

| Module | Class | Python call | Purpose |
|---|---|---|---|
| `RemoteExecutionBridge` | `URemoteExecutionBridgeLibrary` | `unreal.RemoteExecutionBridgeLibrary.heartbeat()` | MCP session alive signal |
| `RemoteExecutionBridge` | `URemoteExecutionBridgeLibrary` | `unreal.RemoteExecutionBridgeLibrary.set_connected_{node_id,pid,ppid,cwd,start_time}(…)`, `set_active_sessions(n)` | MCP session metadata (used by the toolbar badge) |
| `RemoteExecutionBridgeEditor` | `UBlueprintEditorUtilityLibrary` | `unreal.BlueprintEditorUtilityLibrary.get_blueprint_graphs(bp) -> [UEdGraph]` | Return all graphs (EventGraph, function, macro, etc.) in a Blueprint |
| `RemoteExecutionBridgeEditor` | `UBlueprintEditorUtilityLibrary` | `unreal.BlueprintEditorUtilityLibrary.get_graph_nodes(graph) -> [UEdGraphNode]` | Return all nodes in a graph |
| `RemoteExecutionBridgeEditor` | `UBlueprintEditorUtilityLibrary` | `unreal.BlueprintEditorUtilityLibrary.find_nodes_by_function_name(bp, function_name) -> [UEdGraphNode]` | Return all `K2Node_CallFunction` nodes across all Blueprint graphs matching `function_name` |
| `RemoteExecutionBridgeEditor` | `UBlueprintEditorUtilityLibrary` | `unreal.BlueprintEditorUtilityLibrary.delete_blueprint_node(bp, node)` | Unlink all pins and remove the node from the graph |
| `RemoteExecutionBridgeEditor` | `UBlueprintEditorUtilityLibrary` | `unreal.BlueprintEditorUtilityLibrary.mark_blueprint_modified(bp)` | Set Blueprint dirty flag (marks it as needing recompile) |
| `RemoteExecutionBridgeEditor` | `UMaterialEditorUtilityLibrary` | `unreal.MaterialEditorUtilityLibrary.get_material_expressions(material) -> [UMaterialExpression]` | Return all expression nodes in a Material |

### Adding a new UFUNCTION — 5 steps

1. **Choose module**: runtime UE APIs → `RemoteExecutionBridge`; editor-only engine APIs (`FBlueprintEditorUtils`, `FAssetToolsModule`, `UEditorEngine`, etc.) → `RemoteExecutionBridgeEditor`
2. **Edit header and source**: add `UFUNCTION(BlueprintCallable, Category="RemoteExecutionBridge")` to `Source/<Module>/Public/*.h` and `Private/*.cpp`. Follow Epic's [UE C++ Coding Standard](https://dev.epicgames.com/documentation/en-us/unreal-engine/epic-cplusplus-coding-standard-for-unreal-engine).
3. **Add Build.cs dependencies**: for editor-module APIs — `FBlueprintEditorUtils` → `Kismet`; `UEditorEngine`/`GEditor` → `UnrealEd`; `FAssetToolsModule` → `AssetTools`; `FNotificationInfo` → `Slate` + `SlateCore`
4. **Build**: Live Coding (`Ctrl+Alt+F11`) for body-only edits, or rebuild `<YourProject>.sln` and relaunch the editor for new `UCLASS`/`UFUNCTION` declarations. Note: Live Coding only supports modifying existing function bodies reliably — new declarations require a full rebuild.
5. **Verify and document**: confirm `unreal.ClassName.method_name(...)` works, then ① add a row to the catalog table above, ② add a usage snippet to `docs/CHEATSHEET.md`
