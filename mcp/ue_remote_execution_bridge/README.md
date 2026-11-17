# ue-remote-execution-bridge MCP Server — Operations Manual

An MCP server that runs arbitrary Python inside the UE editor from Claude Code via UE's built-in Remote Execution. For installation, see the [repository root README](../../README.md).

> 🧬 **It grows the more you use it.** The `run_python` call history accumulates in [`docs/CHEATSHEET.md`](./docs/CHEATSHEET.md), and recurring patterns are promoted to `scripts/`.
>
> 🛠️ **Work around Python API limits with the C++ plugin.** When you need a member that `unreal.*` doesn't expose, add a `UFUNCTION` to `Plugins/RemoteExecutionBridge/` (procedure and catalog: [`docs/DESIGN.md §6–7`](./docs/DESIGN.md)).

- Design, protocol, ADR: [`docs/DESIGN.md`](./docs/DESIGN.md)
- API frequency table and snippets: [`docs/CHEATSHEET.md`](./docs/CHEATSHEET.md)

---

## Files

| Path | Role |
|---|---|
| `src/server.py` | MCP server — tool definitions, connection lifecycle, heartbeat |
| `scripts/*.py` | Scripts that dedicated tools run inside the editor (list them with `list_scripts`) |
| `docs/DESIGN.md` | Protocol, architecture, ADR, UFUNCTION catalog |
| `docs/CHEATSHEET.md` | API frequency table and common snippets (updated by `update_cheatsheet`) |
| `src/usage.log`, `src/.sessions/` | Files created at runtime (git-ignored) |

---

## Environment variables

| Variable | Default | Description |
|------|--------|------|
| `UE_PROJECT_ROOT` | unset (no workspace filter) | The folder with the `*.uproject`, relative to `.mcp.json` (e.g. `.`, `Client`). **With multiple editors, command routing is scoped to the editor whose project_root matches this** (workspace isolation). If unset there is no workspace filter: calls route to the single visible editor, and the server refuses to guess when several editors are visible (set this variable or use `select_ue_node()`) |
| `UE_ENGINE_DIR` | — (required) | Absolute path of the engine install, the folder that contains `Engine/`. The server loads Epic's `remote_execution.py` (UDP discovery + TCP command client) from `Engine/Plugins/Experimental/PythonScriptPlugin/Content/Python/` there and fails at startup if it is missing |
| `UE_NODE_ID` | — | Default node_id to connect to when multiple editors are running (takes precedence over the project_root filter) |

After editing `.mcp.json`, restart Claude Code or reload with `/mcp`.

---

## Connection behavior

Every tool call does UDP multicast (`239.0.0.1:6766`) discovery → reverse TCP handshake → execute → close (fresh per call).

Multiple sessions are serialized with the Win32 named mutex `Local\UE_RemoteExecution_Bridge` (waits up to 15 seconds). A heartbeat thread pushes `pid`, `cwd`, `git_branch`, `active_sessions`, etc. to the UE toolbar badge tooltip every 2 seconds. On the first successful connection, `[MCP] ue-remote-execution-bridge server connected` is printed to the editor Output Log.

Internal details (session lock, full list of metadata): `docs/DESIGN.md`.

---

## Tools

| Category | Tool | Purpose |
|---------|------|-----|
| Execution | `run_python` | Run arbitrary Python (`exec_file`/`exec_statement`/`eval_statement`) |
| Multi-node | `list_ue_nodes` / `select_ue_node` / `clear_ue_node` | Choose an editor instance |
| Virtual PIE input | `simulate_pointer_input` / `simulate_key_input` | Inject mouse click / drag / long press and key input into PIE (Slate / GameViewportClient backends) |
| Virtual PIE input | `enable_automation_driver` / `run_driver_sequence` / `disable_automation_driver` | AutomationDriver keyboard sequences (real input is blocked while enabled) |
| Maintenance | `update_cheatsheet` | Merge the `src/usage.log` delta into `docs/CHEATSHEET.md`, then truncate |
| Self-check | `list_scripts` | List registered scripts |

For PIE start/stop, Output Log queries, asset search/save/move, and DataTable queries, use the official unreal-mcp toolsets. PIE pause, which the official toolsets lack, is done by sending the console `pause` command through `run_python`.

For each tool's arguments and return values, see the MCP tool docstring (look it up with `/mcp`).

---

## Quick start

```python
# Run arbitrary Python
run_python("""
import unreal
print(unreal.SystemLibrary.get_engine_version())
""")

# Evaluate a single expression (returns a value)
run_python("unreal.SystemLibrary.get_project_directory()", mode="eval_statement")
```

If the same code keeps recurring, promote it to `scripts/` and register it as a dedicated MCP tool.

---

## Multi-editor connection targets

Even when editors from several workspaces are running at the same time, commands are **automatically scoped to the editor matching `UE_PROJECT_ROOT`**, so they don't leak into the wrong workspace. If there is no matching editor, or there are two or more candidates with no discriminator (filter match or pin), it does not guess `remote_nodes[0]`; it **fails and lists the candidates** (preventing silent misrouting).

To deliberately target an editor from another workspace, pin it in natural language:

- `"list the running editors"` → `list_ue_nodes()` (check the current routing basis with `selection_source`)
- `"connect to the X editor"` → `select_ue_node(node_id)` (bypasses the project_root filter and guard. The ID is stored even if the editor isn't visible right now — deferred binding)
- `"unpin the connection"` → `clear_ue_node()` (returns to env/workspace-filter auto-selection)

Each Claude Code session's MCP server starts independently, so two sessions can target different editors at the same time. Check which session is attached where from the Branch, PID, and Project in the toolbar badge tooltip.

---

## Usage Logging

Code from `run_python` calls is appended in plaintext to `src/usage.log` (do not include secrets). The MCP tool `update_cheatsheet` extracts `unreal.X.Y` symbols, merges them into the `docs/CHEATSHEET.md` frequency table, and truncates the log. Details: `docs/CHEATSHEET.md`.

---

## C++ plugin extension (escape hatch)

In the following situations, add a `UFUNCTION(BlueprintCallable)` to `Plugins/RemoteExecutionBridge/` instead of working around it in Python:

- `AttributeError: module 'unreal' has no attribute …`
- You need an engine-internal `private:` member such as `FBlueprintEditorUtils` or `FAssetToolsModule`
- Two Python workarounds for the same goal have failed

For the procedure, UFUNCTION catalog, and module classification, see [`docs/DESIGN.md §6–7`](./docs/DESIGN.md).

---

## Troubleshooting

| Symptom | Cause | Fix |
|------|------|------|
| `No Unreal Editor discovered within …s` | Editor not running, or PythonScriptPlugin disabled | Launch the editor and check that the plugin is enabled |
| `editor(s) found but none match this workspace` | The editor for this workspace (`UE_PROJECT_ROOT`) isn't running | Launch that workspace's editor, or pin with `select_ue_node()` if that is intended |
| `editors discovered with no UE_PROJECT_ROOT filter or node pin — refusing to guess` | Multiple candidates, but `UE_PROJECT_ROOT` is unset and nothing is pinned | Set `UE_PROJECT_ROOT` in `.mcp.json` or use `select_ue_node()` |
| `Workspace guard failed` | The connected editor's actual project doesn't match what's expected (stale discovery) | Check the target editor and retry, or pin with `select_ue_node()` |
| `Target node '…' not found within …s` | The editor pinned with `select_ue_node` has shut down (a pin is re-pinned automatically after a restart only when it pointed at this workspace's editor) | `clear_ue_node()`, `select_ue_node()` again, or restart the target editor |
| `No matching Unreal Editor for this workspace within …s` | No editor in this workspace answered discovery | Launch this workspace's editor, or pin one with `select_ue_node()` |
| `UE connection held by another session. Retry shortly.` | Another Claude Code session held the UE connection for longer than 15 seconds | Wait a moment and retry |
| `Remote party failed to send a valid response!` | Payload exceeded the buffer | Add `Send/ReceiveBufferSizeBytes=2097152` to `DefaultEngine.ini` |
| `OSError: Address already in use` | Port conflict | Restart the editor |
| Toolbar dot stays red | The MCP server didn't start or can't find the editor | Check server status in `/mcp` → check that the editor shows up with `list_ue_nodes()` |
| No connection after a firewall warning | UDP 6766 / editor TCP port blocked | Allow on the Private profile |

---

## Related docs

- [`docs/DESIGN.md`](./docs/DESIGN.md) — protocol, architecture, ADR, UFUNCTION catalog
- [`docs/CHEATSHEET.md`](./docs/CHEATSHEET.md) — API frequency table and common snippets
- [UE Python API](https://dev.epicgames.com/documentation/en-us/unreal-engine/python-api/)
