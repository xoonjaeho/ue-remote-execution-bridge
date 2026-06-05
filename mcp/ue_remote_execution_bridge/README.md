# UE Remote Execution Bridge — MCP Server

Exposes Unreal Editor 5.7's Python interpreter as MCP tools, enabling Claude to execute arbitrary Python inside the editor, control PIE, and stream the Output Log. When the stock `unreal.*` Python API is missing a symbol, extend it by adding a `UFUNCTION` to the companion C++ plugin shipped in this repo — see [§C++ Plugin Extension](#c-plugin-extension-python-api-escape-hatch) below. This escape hatch is a core part of the design, not a workaround.

Architecture and design decisions: [docs/DESIGN.md](./docs/DESIGN.md)

> This document is the MCP server subproject's **operations & extension manual**. For a public overview, install steps, the tool list, and security warnings, see the [root README](../../README.md).

## Files

| File | Role |
|---|---|
| `server.py` | FastMCP server — tool definitions, connection lifecycle management |
| `execute.py` | Epic's `remote_execution.py` (UDP discovery + TCP command client), renamed to avoid import-path collision |
| `update_cheatsheet.py` | Folds `usage.log` API frequency deltas into `docs/CHEATSHEET.md` |
| `docs/DESIGN.md` | Protocol, architecture, ADR |
| `docs/CHEATSHEET.md` | Frequently-used UE Python API reference (manually curated from `usage.log`) |
| `README.md` | This file |

## Prerequisites

Setup is covered in the [root README §Install](../../README.md#install). Operational requirements:

- Editor must be **running** (commandlet/headless mode is not supported)
- Multicast `239.0.0.1:6766` reachable on local loopback
- Firewall: allow UDP 6766 and the editor's TCP listen port (Private profile)
- Python 3.10+ with `mcp` (FastMCP)

## MCP Registration

Base setup (`.mcp.json` template) is in the [root README §Install Step 5](../../README.md#install). Operational notes:

- `args` path is relative to where Claude Code launches — typically the UE project root.
- Set `UE_PROJECT_ROOT` (absolute path) to override project inference when the MCP workspace is not the UE project root.
- After editing `.mcp.json`, restart Claude Code or run `/mcp`.
- The server starts even if the editor is off — it retries discovery on the first tool call. First successful connect logs `[MCP] ue_remote_execution_bridge server connected` to the UE Output Log.

## C++ Plugin Extension (Python API Escape Hatch)

The four MCP tools (see [root README §MCP Tools](../../README.md#mcp-tools)) don't add editor-internal capabilities to Python. When `unreal.*` is missing a symbol — `AttributeError`, absent from [the 5.7 Python API docs](https://dev.epicgames.com/documentation/en-us/unreal-engine/python-api/?application_version=5.7), needs engine-internal `private:` members (`FBlueprintEditorUtils`, `FAssetToolsModule`, …), or two Python workaround attempts failed — add a `UFUNCTION` to `Plugins/RemoteExecutionBridge/`. This is the plugin's primary role.

### Existing UFUNCTION catalog

> SoT — this table is referenced from `docs/DESIGN.md §4.7`. Edit here when UFUNCTIONs are added or changed.

Check here before adding a new UFUNCTION.

| Module | Class | Python call | Purpose |
|---|---|---|---|
| `RemoteExecutionBridge` | `URemoteExecutionBridgeLibrary` | `unreal.RemoteExecutionBridgeLibrary.heartbeat()` | MCP session alive signal |
| `RemoteExecutionBridge` | `URemoteExecutionBridgeLibrary` | `unreal.RemoteExecutionBridgeLibrary.set_connected_{node_id,pid,ppid,parent_name,cwd,start_time}(…)`, `set_active_sessions(n)` | MCP session metadata (used by the toolbar badge) |
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

For graph-mutating UFUNCTIONs (`RemoveNode`, pin-link edits, etc.), wrap the mutation in `FScopedTransaction` so Ctrl+Z works. Verify integrity after: `unreal.EditorAssetLibrary.save_asset(<path>)`, close + reopen the Blueprint, confirm the graph.

## Workspace-Aware Editor Matching

The server attaches only to the UE editor whose *project* matches its workspace, and
**refuses** otherwise — so commands never reach the wrong project when several editors or
Claude sessions run at once.

How it works (auto mode):

1. `_PROJECT_STEM` is resolved once at startup: `UE_PROJECT_ROOT` if set, else the nearest
   `*.uproject` walking from the MCP process cwd (the directory itself, then its parents).
   If none is found the workspace is **unresolved** and every auto-connect is refused — a
   coincidental cwd name must not pose as a project.
2. UE's discovery pong carries no project identity (only a per-session UUID), so the server
   verifies *after* connecting: for each discovered editor it opens the command channel and
   evaluates `unreal.Paths.get_project_file_path()`, comparing the `.uproject` filename stem
   (case-insensitive) to `_PROJECT_STEM`. The probe is read-only; a non-matching editor is
   left untouched (no heartbeat, no green badge).
3. Outcomes: **match** → connect; **no match** → refuse with an actionable error; **two
   editors report the same project** → refuse as ambiguous (pin with `UE_PROJECT_ROOT`);
   **identity not readable yet** (editor mid-boot) → retry, never a false refuse.
4. The decision is cached on the *set* of discovered editor ids — steady-state heartbeats and
   tool calls don't re-probe until an editor appears or disappears.

**Pinning.** Set `UE_PROJECT_ROOT` (absolute path) to fix the target project explicitly; the
same matching is enforced against the pinned stem.

**Escape hatch.** `UE_BRIDGE_ALLOW_ANY=1` restores the legacy "attach to the first discovered
editor" behavior (default off). Use only when you knowingly want any editor.

This is project matching, not arbitrary editor selection — the server does not parse prompts
like "connect to PID 2968" (PID is not in the pong). To target another project, launch the
MCP server from that workspace or set `UE_PROJECT_ROOT`.

**Verifying the match.** `run_python("unreal.Paths.get_project_file_path()", mode="eval_statement")`
returns the connected editor's `.uproject` — its stem must equal your workspace's.

> Multiple editors are discoverable concurrently on the shared loopback group as long as the
> server waits for all pongs (a short settle after the first responder). Each session then
> matches its own editor among several. If your workspace's editor isn't among those
> discovered, the server refuses rather than connecting to another project's editor.

## Connection Lifecycle

- **Eager connect**: 2-second discovery on server startup. Connects immediately if a *matching* editor is running; skips silently otherwise (including when only non-workspace editors are up — see Workspace-Aware Editor Matching).
- **Per-call connect**: Each tool call opens a fresh UDP discovery + TCP handshake (~100–200 ms overhead) and closes the connection when the call completes. Connection reuse is deferred to a later revision.
- A single `[MCP] ue_remote_execution_bridge server connected` line is written to the editor Output Log on the first successful heartbeat (not repeated per tool call, to avoid spam).
- If the editor is not reachable, the call returns an error immediately.
- The toolbar badge turns red when heartbeats time out. On disconnect, stored MCP session metadata is cleared; the tooltip shows `0` sessions and `—` for stale MCP fields.

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

Every `run_python` call appends raw code to `usage.log` (`---\n<ISO timestamp>\n<code>\n`); `start_pie`/`stop_pie`/`tail_output_log` skip logging.

```powershell
# preview
python mcp\ue_remote_execution_bridge\update_cheatsheet.py --dry-run
# apply + clear delta
python mcp\ue_remote_execution_bridge\update_cheatsheet.py --truncate-usage
```

Merge semantics and snippet curation policy: [`docs/CHEATSHEET.md`](./docs/CHEATSHEET.md).

Security: raw code is stored plaintext until aggregated. Do not pass credentials or secrets through `run_python`.
