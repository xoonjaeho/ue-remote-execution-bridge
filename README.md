# ue-remote-execution-bridge

Drop-in template that exposes Unreal Engine 5.7's PythonScriptPlugin Remote Execution as MCP tools, with a companion C++ plugin that adds a live connection-status badge to the editor toolbar and exposes editor-internal APIs (Blueprint graph manipulation, Material expression access) to Python.

## What's in the box

| Path | Purpose |
|---|---|
| `Plugins/RemoteExecutionBridge/` | C++ UE plugin. Drop into your project's `Plugins/` directory. |
| `mcp/ue_remote_execution_bridge/` | Python MCP server. Drop into your project's `mcp/` directory. |
| `Config/DefaultEngine.ini.snippet` | Engine settings block to append to your project's `DefaultEngine.ini`. |
| `.mcp.json.example` | MCP registration template to copy to your project root as `.mcp.json`. |

**Both halves work independently.** Installing only the Python server gives you all four MCP tools (`run_python`, `start_pie`, `stop_pie`, `tail_output_log`) without a toolbar badge. Installing only the C++ plugin gives you the badge (red until a server connects) and the extended UFUNCTIONs without MCP tooling.

## Requirements

- Unreal Engine 5.7, Win64
- Python 3.10+ on `PATH`
- Claude Code (or any MCP-compatible client that supports stdio servers)

> The Python server uses a Win32 cross-process mutex (`CreateMutexW`) and is Windows-only. The C++ plugin is also declared `SupportedTargetPlatforms: ["Win64"]`.

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
{ "Name": "RemoteExecutionBridge",     "Enabled": true },
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
4. The status dot widget appears at the right end of the LevelEditor toolbar (red initially).
5. Open Claude Code in your project root. Run `/mcp` and confirm `ue_remote_execution_bridge` is connected.
6. Within ~2 seconds, the editor Output Log shows:
   ```
   LogPython: [MCP] ue_remote_execution_bridge server connected
   ```
   The toolbar dot turns green.
7. Call the `run_python` tool with `print("hello")`. Expect `success: true` and `stdout` containing the line.

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

- [MCP server README](mcp/ue_remote_execution_bridge/README.md) — tool reference, troubleshooting, usage logging
- [Design document](mcp/ue_remote_execution_bridge/docs/DESIGN.md) — protocol details, architecture decision record
- [UE Python API (5.7)](https://dev.epicgames.com/documentation/en-us/unreal-engine/python-api/?application_version=5.7)
- [Issues](https://github.com/xoonjaeho/ue-remote-execution-bridge/issues)
