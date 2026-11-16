# ue-remote-execution-bridge (Claude Code · Unreal Editor)

An MCP server that lets an AI (Claude Code) **run arbitrary Python directly** inside a running Unreal Editor, plus the companion C++ plugin.

Starting with UE 5.8, Epic ships an official MCP (unreal-mcp), but the official toolsets can only call a fixed set of features. This bridge covers what the official toolsets can't do.

| | Official unreal-mcp | This bridge |
|---|---|---|
| What it can do | Only prebuilt tools (asset search, start PIE, log queries, etc.) | The whole editor Python API — the AI writes and runs code on the spot |
| When you hit a wall | Wait, or build a new tool | For engine features missing from Python, add one function to the C++ plugin and call it right away |
| The more you use it | Stays the same | Code the AI has run piles up and is distilled into a cheatsheet of frequently used APIs and pitfalls, so the AI in the next session doesn't repeat the same mistakes |
| PIE control | Start / stop | Injects button clicks, drags, and key input straight into PIE, so it can step through screens to verify |

Use the two MCPs together: the official toolsets for the fixed jobs, this bridge for everything else.

## Install

Just tell Claude Code:

```
Read this folder's README.md and install it into my UE project.
```

When it's done, build the editor, restart it, and open a new Claude Code session.

## Examples

Ask in plain language and the AI writes Python and runs it inside the editor.

- "Find the StaticMeshActors in the level that are missing a material."
- "List the widget blueprints in the UI folder that have unused variables."
- "Start PIE, press the 'Start' button in the lobby, and check that it moves on to the next screen."
- "Replace this widget blueprint's Custom Events with overrides of the parent C++ class's BlueprintImplementableEvents."

You can see the connection state from the dot at the right end of the editor toolbar. **Green** means the AI is attached; **red** means it is disconnected. Hover over the dot to see which Claude Code session (path, branch, PID) is attached.

## What's in the box

| Path | Purpose |
|---|---|
| `mcp/ue_remote_execution_bridge/` | Python MCP server. Finds the editor, attaches to it, and runs code |
| `Plugins/RemoteExecutionBridge/` | C++ editor plugin. Toolbar connection indicator, virtual PIE input, and engine features that Python lacks |
| `Config/DefaultEngine.ini.snippet` | Settings that turn on Python remote execution in the editor |
| `.mcp.json.example` | Example for registering the MCP server with Claude Code |

For how it works in detail, see [`mcp/ue_remote_execution_bridge/README.md`](mcp/ue_remote_execution_bridge/README.md) (operations manual) and [`docs/DESIGN.md`](mcp/ue_remote_execution_bridge/docs/DESIGN.md) (design).

## Caveats

- **Windows only** (verified with UE 5.8, Win64).
- The editor's Python remote execution has **no authentication**. Do not change `RemoteExecutionMulticastTtl=0` or `RemoteExecutionMulticastBindAddress=127.0.0.1` in the settings. Do not use it on shared PCs.
- Code the AI runs is kept in plaintext in `mcp/ue_remote_execution_bridge/src/usage.log`. Do not put passwords or tokens in code.

## Turning it off

Tell the AI "Turn off ue-remote-execution-bridge." It removes the registration from `.mcp.json` and disables the plugin in the `.uproject`.

---

## Install procedure for the AI

The AI doing the install follows this section. `BRIDGE` is the folder that contains this README; `PROJECT` is the folder that contains the user's `.uproject`. Back up existing config files before editing them, and merge into existing entries instead of overwriting. When finished, tell the user which files were changed and what to do next (build and restart the editor, open a new Claude Code session).

Requirements: Windows, Unreal Engine 5.8 (an environment that can build C++ — Visual Studio), Python 3.10 or later, Claude Code.

1. **Copy the C++ plugin** — Copy `BRIDGE/Plugins/RemoteExecutionBridge` to `PROJECT/Plugins/RemoteExecutionBridge`. If the project is Blueprint-only, tell the user it must be converted to a C++ project by adding one C++ class before it will build.
2. **Copy the MCP server** — Copy `BRIDGE/mcp/ue_remote_execution_bridge` to `mcp/ue_remote_execution_bridge` under the folder that will hold `.mcp.json` (usually the repository root).
3. **ini settings** — Append the contents of `BRIDGE/Config/DefaultEngine.ini.snippet` to `PROJECT/Config/DefaultEngine.ini`. If the `[/Script/PythonScriptPlugin.PythonScriptPluginSettings]` section already exists, merge only the keys.
4. **Enable the plugins** — Add the following to the `Plugins` array in `PROJECT/<name>.uproject` (if they already exist, just check `Enabled`).
   ```json
   { "Name": "PythonScriptPlugin", "Enabled": true },
   { "Name": "RemoteExecutionBridge", "Enabled": true }
   ```
5. **Register the MCP** — Add the following to `mcpServers` in `.mcp.json`. `UE_PROJECT_ROOT` is the path of `PROJECT` relative to the `.mcp.json` folder (`"."` if they are the same folder).
   ```json
   "ue-remote-execution-bridge": {
     "command": "python",
     "args": ["mcp/ue_remote_execution_bridge/src/server.py"],
     "env": { "UE_PROJECT_ROOT": "." }
   }
   ```
6. **Python dependencies** — `pip install -r mcp/ue_remote_execution_bridge/requirements.txt`
7. **Verify** — After the user builds and launches the editor and opens a new Claude Code session:
   - The dot at the right end of the toolbar changes from red to green, and `[MCP] ue-remote-execution-bridge server connected` appears in the Output Log.
   - Run `print("hello")` with `run_python` and check that `success: true` and the output `hello` come back.
   - If it fails, see Troubleshooting in `mcp/ue_remote_execution_bridge/README.md`.

When asked to turn it off, revert step 5 and the `RemoteExecutionBridge` entry from step 4. Ask the user whether to delete the ini settings and the copied files.

## License

MIT (`LICENSE`). Exception: `mcp/ue_remote_execution_bridge/src/execute.py` is a verbatim copy of Epic's `remote_execution.py` and is governed by the Unreal Engine EULA — see `NOTICE`.
