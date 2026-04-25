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

Each half is installable alone (Python server only → four MCP tools, no badge; C++ plugin only → badge + UFUNCTIONs, no MCP), but installing both is the default and intended setup.

## How it works

```
Claude Code (MCP client)
      │  stdio
      ▼
mcp/ue_remote_execution_bridge/server.py   ── Python MCP server (this repo)
      │  UDP 6766 discovery  +  TCP command  (PythonScriptPlugin Remote Execution)
      ▼
UE Editor — PythonScriptPlugin interpreter
      │  unreal.RemoteExecutionBridgeLibrary.*  (and user-added UFUNCTIONs)
      ▼
Plugins/RemoteExecutionBridge/   ── C++ UE plugin (this repo)
      ├─ heartbeat + session metadata  →  green/red dot on the LevelEditor toolbar
      └─ editor-internal APIs exposed to Python  (Kismet, MaterialEditor, … — extensible)
```

The toolbar dot is the visible proof the two halves are talking: when the MCP server's heartbeat UFUNCTION call reaches the C++ plugin, the dot turns green.

### Naming map

The project uses three related names. All refer to parts of the same system.

| Name | Kind | Where |
|---|---|---|
| `ue-remote-execution-bridge` | Repo / MCP server id (`.mcp.json`) | This repo, `mcp/ue_remote_execution_bridge/server.py` |
| `RemoteExecutionBridge`, `RemoteExecutionBridgeEditor` | C++ UE modules | `Plugins/RemoteExecutionBridge/Source/` |
| `unreal.RemoteExecutionBridgeLibrary` (and sibling `…EditorUtilityLibrary` classes) | Python bindings generated from the C++ UFUNCTIONs | Called from `run_python` payloads |

## Requirements

- Unreal Engine 5.7, Win64
- Python 3.10+ on `PATH`
- Claude Code (or any MCP-compatible client that supports stdio servers)

> The Python server uses a Win32 cross-process mutex (`CreateMutexW`) and is Windows-only. The C++ plugin is also declared `SupportedTargetPlatforms: ["Win64"]`.

## Security

Remote Execution has **no authentication**. Keep `RemoteExecutionMulticastTtl=0` so packets never leave the local machine. Do not use on shared machines or on a LAN. See `mcp/ue_remote_execution_bridge/docs/DESIGN.md §5` for threat model details.

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

The path in `args` is relative to the directory Claude Code is launched from (your project root). If you place the `mcp/` folder elsewhere, set the `UE_PROJECT_ROOT` environment variable to your project root and adjust `args` accordingly.

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

If any step above fails, see [MCP server README §Troubleshooting](mcp/ue_remote_execution_bridge/README.md#troubleshooting) for common errors.

## MCP Tools

> SoT — this table is referenced from `mcp/ue_remote_execution_bridge/docs/DESIGN.md §4.3`. Edit here when signatures change.

| Tool | Parameters (defaults) | Behavior |
|---|---|---|
| `run_python` | `code: str`, `mode: "exec_file"\|"exec_statement"\|"eval_statement" = "exec_file"`, `unattended: bool = True` | Execute Python inside the editor. Only `eval_statement` returns a value. |
| `start_pie` | — | `LevelEditorSubsystem.editor_request_begin_play()` |
| `stop_pie` | — | `LevelEditorSubsystem.editor_request_end_play()` |
| `tail_output_log` | `since_offset: int\|None = None`, `filter_regex: str\|None = None`, `max_lines: int = 500` | Paginate `Saved/Logs/<Project>.log` using a byte-offset cursor (max 256 KB per call). Parses timestamp, category, and verbosity. |

## Runtime artifacts

Two artifacts accumulate alongside the server once the bridge is in use:

- `mcp/ue_remote_execution_bridge/usage.log` — raw `run_python` code appended on every call (plaintext; never pass credentials through `code`).
- `mcp/ue_remote_execution_bridge/docs/CHEATSHEET.md` — curated `unreal.*` snippet index, incrementally folded from `usage.log`.

Workflow: ask Claude to "scan usage.log and update CHEATSHEET", then truncate the log. Full rules: [MCP server README §Usage Logging](mcp/ue_remote_execution_bridge/README.md#usage-logging).

## Extending the Bridge (Python API Escape Hatch)

UE's Python API does not cover every editor-internal C++ facility (`FBlueprintEditorUtils`, `FAssetToolsModule`, various `UEditorEngine` members, etc.). When Python hits a wall — typically `AttributeError: module 'unreal' has no attribute …` or a symbol absent from the [official Python API docs](https://dev.epicgames.com/documentation/en-us/unreal-engine/python-api/?application_version=5.7) — **add a `UFUNCTION` to the C++ plugin in this repo and call it from Python**. This is the plugin's primary job, not an afterthought.

Where to go next:
- Existing UFUNCTION catalog and the 5-step "add a UFUNCTION" recipe: [mcp/ue_remote_execution_bridge/README.md §C++ Plugin Extension](mcp/ue_remote_execution_bridge/README.md#c-plugin-extension-python-api-escape-hatch).
- Source to edit: `Plugins/RemoteExecutionBridge/Source/RemoteExecutionBridge/` (runtime APIs) and `Plugins/RemoteExecutionBridge/Source/RemoteExecutionBridgeEditor/` (editor-only APIs).

## Optional: C++ Plugin as a Git Submodule

If you want to track the plugin separately from the MCP server:

```bash
git submodule add https://github.com/xoonjaeho/ue-remote-execution-bridge Plugins/RemoteExecutionBridge
```

This only works for the C++ plugin half. The Python server is better managed as a plain directory copy because its `.mcp.json` registration lives at your project root (outside any submodule boundary).

## License and Attribution

Original code (`server.py`, all C++ source, all documentation) is released under the **MIT License**. See `LICENSE`.

`mcp/ue_remote_execution_bridge/execute.py` is a verbatim copy of Epic's `remote_execution.py` ("Copyright Epic Games, Inc. All Rights Reserved."), renamed to avoid an import-path collision. Use of this file is governed by the Unreal Engine EULA. See `NOTICE`.

## Links

- [MCP server README](mcp/ue_remote_execution_bridge/README.md) — operations, troubleshooting, UFUNCTION extension
- [Design document](mcp/ue_remote_execution_bridge/docs/DESIGN.md) — protocol details, architecture decision record
- [UE Python API cheatsheet](mcp/ue_remote_execution_bridge/docs/CHEATSHEET.md) — frequently-used `unreal.*` snippets, aggregated from `usage.log`
- [UE Python API (5.7)](https://dev.epicgames.com/documentation/en-us/unreal-engine/python-api/?application_version=5.7)
- [Issues](https://github.com/xoonjaeho/ue-remote-execution-bridge/issues)
