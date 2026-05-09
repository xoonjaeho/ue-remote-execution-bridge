# ue-remote-execution-bridge

One integrated system in two cooperating halves:

- **Python MCP server** — exposes UE 5.7's PythonScriptPlugin Remote Execution as MCP tools so Claude can run Python inside the editor, control PIE, and tail the Output Log.
- **C++ UE plugin** — shows live connection state on the editor toolbar, and serves as a Python-API escape hatch: when `unreal.*` lacks a symbol (Blueprint graph manipulation, Material expression access, etc.), add a `UFUNCTION` and call it from Python.

Both halves ship together and are designed to be used together.

## What's in the box

| Path | Purpose |
|---|---|
| `Plugins/RemoteExecutionBridge/` | C++ UE plugin. Drop into your project's `Plugins/` directory. |
| `mcp/ue_remote_execution_bridge/` | Python MCP server. Drop into your project's `mcp/` directory. |
| `Config/DefaultEngine.ini.snippet` | Engine settings block to append to your project's `DefaultEngine.ini`. |
| `.mcp.json.example` | MCP registration template to copy to your project root as `.mcp.json`. |

## How it works

```
Claude Code (MCP client)
      │  stdio
      ▼
mcp/ue_remote_execution_bridge/server.py   ── Python MCP server (this repo)
      │  UDP multicast 239.0.0.1:6766 (discovery) + TCP (command, dynamic port)
      │  — PythonScriptPlugin Remote Execution
      ▼
UE Editor — PythonScriptPlugin interpreter
      │  unreal.RemoteExecutionBridgeLibrary.*  (and user-added UFUNCTIONs)
      ▼
Plugins/RemoteExecutionBridge/   ── C++ UE plugin (this repo)
      ├─ heartbeat + session metadata  →  green/red dot on the LevelEditor toolbar
      └─ editor-internal APIs exposed to Python  (Kismet, MaterialEditor, … — extensible)
```

The toolbar dot is the visible proof the two halves are talking: when the MCP server's heartbeat UFUNCTION call reaches the C++ plugin, the dot turns green. When heartbeats expire, the dot turns red and stale MCP session metadata is cleared from the tooltip.

### Naming map

Three related names refer to parts of the same system:

- Repo / MCP id: `ue-remote-execution-bridge`
- C++ modules (under `Plugins/RemoteExecutionBridge/Source/`): `RemoteExecutionBridge`, `RemoteExecutionBridgeEditor`
- Python-facing classes (called from `run_python`): `unreal.RemoteExecutionBridgeLibrary`, `unreal.BlueprintEditorUtilityLibrary`, `unreal.MaterialEditorUtilityLibrary`

## Requirements

- Unreal Engine 5.7, Win64
- Python 3.10+ on `PATH`
- Claude Code (or any MCP-compatible client that supports stdio servers)

> Windows only. The Python server uses `CreateMutexW` and raises `RuntimeError` on import elsewhere; the C++ plugin declares `SupportedTargetPlatforms: ["Win64"]`.

> `RemoteExecutionBridge.uplugin` pins `EngineVersion: 5.7.0`. Bump on engine upgrade — UE warns on patch-level mismatch.

## Security

Remote Execution has **no authentication**. Keep `RemoteExecutionMulticastTtl=0` so packets never leave the local machine. Do not use on shared machines or on a LAN. See `mcp/ue_remote_execution_bridge/docs/DESIGN.md §5` for threat model details.

> `mcp/ue_remote_execution_bridge/usage.log` stores every `run_python` payload in plaintext. Windows' default NTFS DACL grants read access to `BUILTIN\Users`, so any local user on the machine can read it. Restrict the directory's permissions or do not pass secrets through `run_python`.

## Install

Five manual steps. Complete them in order.

**Step 1 — Copy the C++ plugin**

```
cp -r Plugins/RemoteExecutionBridge  <YourProject>/Plugins/
```

**Step 2 — Copy the MCP server**

```
cp -r mcp/ue_remote_execution_bridge  <YourProject>/mcp/
```

**Step 3 — Append ini settings**

Open `<YourProject>/Config/DefaultEngine.ini` and append the entire contents of `Config/DefaultEngine.ini.snippet`. If the `[/Script/PythonScriptPlugin.PythonScriptPluginSettings]` section already exists, merge the keys into it.

**Step 4 — Enable plugins in your .uproject**

Add these entries to the `Plugins` array in `<YourProject>/<YourProject>.uproject`:

```json
{ "Name": "RemoteExecutionBridge",     "Enabled": true, "SupportedTargetPlatforms": ["Win64"] },
{ "Name": "PythonScriptPlugin",        "Enabled": true },
{ "Name": "EditorScriptingUtilities",  "Enabled": true }
```

**Step 5 — Register the MCP server**

Copy `.mcp.json.example` to `<YourProject>/.mcp.json` (or merge the `mcpServers` entry into an existing `.mcp.json`):

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

`args` is relative to the project root (where Claude Code launches). For non-mirrored layouts (`UE_PROJECT_ROOT` override) and workspace-aware editor matching, see the [MCP server README](mcp/ue_remote_execution_bridge/README.md#mcp-registration).

Finally, install the Python dependency:

```
pip install -r requirements.txt
```

## Verify

1. Right-click `<YourProject>.uproject` → **Generate Visual Studio project files**.
2. Build `<YourProject>Editor Win64 Development`.
3. Launch the editor.
4. The status dot widget appears at the right end of the LevelEditor toolbar (red initially — no MCP server has checked in yet).
5. Open Claude Code in your project root. Run `/mcp` and confirm `ue_remote_execution_bridge` is connected.
6. Within ~2 seconds, the editor Output Log shows:
   ```
   LogPython: [MCP] ue_remote_execution_bridge server connected
   ```
   The toolbar dot turns green. This means the Python MCP server's heartbeat reached the C++ plugin — the two halves are integrated and live.
7. Call the `run_python` tool with `print("hello")`. Expect `success: true` and `stdout` containing the line.
8. Cursor pagination: call `tail_output_log()`, save its `cursor`, run `run_python` with `print("pagination check")`, then call `tail_output_log(since_offset=<cursor>)`. The new line must appear and `cursor` must advance.
9. PIE (run from a clean editor): `start_pie` returns `stdout` containing `PIE start requested`, then `stop_pie` returns `PIE end requested`. Re-running the same call without flipping state returns `PIE already running; no-op` or `PIE not running; no-op`.

If any step above fails, see [MCP server README §Troubleshooting](mcp/ue_remote_execution_bridge/README.md#troubleshooting) for common errors.

## MCP Tools

> SoT — this table is referenced from `mcp/ue_remote_execution_bridge/docs/DESIGN.md §4.3`. Edit here when signatures change.

| Tool | Parameters (defaults) | Behavior |
|---|---|---|
| `run_python` | `code: str`, `mode: "exec_file"\|"exec_statement"\|"eval_statement" = "exec_file"`, `unattended: bool = True` | Execute Python inside the editor. Only `eval_statement` returns a value. |
| `start_pie` | — | `LevelEditorSubsystem.editor_request_begin_play()` |
| `stop_pie` | — | `LevelEditorSubsystem.editor_request_end_play()` |
| `tail_output_log` | `since_offset: int\|None = None`, `filter_regex: str\|None = None`, `max_lines: int = 500` | Paginate `Saved/Logs/<Project>.log` using a byte-offset cursor (max 256 KB per call). Parses timestamp, category, and verbosity. |

## Operations Manual

Day-to-day operations — registration, connection lifecycle, troubleshooting, `usage.log` maintenance — live in the [MCP server README](mcp/ue_remote_execution_bridge/README.md).

## Extending the Bridge (Python API Escape Hatch)

UE's Python API does not cover every editor-internal C++ facility (`FBlueprintEditorUtils`, `FAssetToolsModule`, various `UEditorEngine` members, etc.). When Python hits a wall — typically `AttributeError: module 'unreal' has no attribute …` or a symbol absent from the [official Python API docs](https://dev.epicgames.com/documentation/en-us/unreal-engine/python-api/?application_version=5.7) — **add a `UFUNCTION` to the C++ plugin in this repo and call it from Python**. This is the plugin's primary job, not an afterthought.

Where to go next:
- Existing UFUNCTION catalog and the 5-step "add a UFUNCTION" recipe: [mcp/ue_remote_execution_bridge/README.md §C++ Plugin Extension](mcp/ue_remote_execution_bridge/README.md#c-plugin-extension-python-api-escape-hatch).
- Source to edit: `Plugins/RemoteExecutionBridge/Source/RemoteExecutionBridge/` (runtime APIs) and `Plugins/RemoteExecutionBridge/Source/RemoteExecutionBridgeEditor/` (editor-only APIs).

## License and Attribution

MIT (`LICENSE`). Exception: `mcp/ue_remote_execution_bridge/execute.py` is a verbatim copy of Epic's `remote_execution.py`, renamed to avoid import-path collision; governed by the Unreal Engine EULA — see `NOTICE`.

## Links

- [MCP server README](mcp/ue_remote_execution_bridge/README.md) — operations, troubleshooting, UFUNCTION extension
- [Design document](mcp/ue_remote_execution_bridge/docs/DESIGN.md) — protocol details, architecture decision record
- [UE Python API cheatsheet](mcp/ue_remote_execution_bridge/docs/CHEATSHEET.md) — frequently-used `unreal.*` snippets, aggregated from `usage.log`
- [UE Python API (5.7)](https://dev.epicgames.com/documentation/en-us/unreal-engine/python-api/?application_version=5.7)
- [Issues](https://github.com/xoonjaeho/ue-remote-execution-bridge/issues)
