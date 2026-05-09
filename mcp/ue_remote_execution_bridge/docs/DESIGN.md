# External Control of UE Python API — Remote Execution and Transport Selection

## 1. Overview

This document describes how to invoke Unreal Engine 5.7's Python API from an **external process (here, Claude)**. Two goals:

- Programmatic creation and modification of `.uasset` files (materials, blueprints, levels, sequences, etc.)
- Editor runtime control — Play-In-Editor (PIE) start/stop, Output Log streaming, world and actor manipulation

There is exactly one practical channel for driving Python from outside UE: the **Remote Execution** protocol (official name: the Remote Execution feature of `PythonScriptPlugin`, corresponding to the `Enable Remote Execution` checkbox in UE Project Settings → Plugins → Python). This document explains the protocol first, then compares three external invocation approaches (Bash · MCP · commandlet), and finally describes the **MCP server** architecture this project adopts and the rationale behind it.

> In this document "Remote Execution" refers only to this PythonScriptPlugin feature — do not confuse it with the separate **Remote Control** plugin.

### 1.1 Naming map

This project ships two cooperating halves under three related names — all point at the same system. See the [repo root README §How it works](../../../README.md#how-it-works) for the end-to-end data flow.

| Name | Kind | Where |
|---|---|---|
| `ue-remote-execution-bridge` | Repo / MCP server id (`.mcp.json`) | This repo, `mcp/ue_remote_execution_bridge/server.py` |
| `RemoteExecutionBridge`, `RemoteExecutionBridgeEditor` | C++ UE modules | `Plugins/RemoteExecutionBridge/Source/` |
| `unreal.RemoteExecutionBridgeLibrary`, `unreal.BlueprintEditorUtilityLibrary`, `unreal.MaterialEditorUtilityLibrary` | Python bindings generated from the C++ UFUNCTIONs | Called from `run_python` payloads |

The C++ plugin is also the designed **Python API escape hatch** — when `unreal.*` lacks a symbol, add a UFUNCTION. See §4.7 and [mcp README §C++ Plugin Extension](../README.md#c-plugin-extension-python-api-escape-hatch).

---

## 2. Remote Execution Protocol

### 2.1 Location and Activation Conditions

This is a built-in feature provided by `PythonScriptPlugin`. The following must be satisfied:

- Editor must be **running** — commandlet/headless mode is not supported
- `PythonScriptPlugin` must be enabled
- `bRemoteExecution=True` must be set in the `[/Script/PythonScriptPlugin.PythonScriptPluginSettings]` section of `Config/DefaultEngine.ini`

Epic's distributed **reference client**:

```
<UE>/Engine/Plugins/Experimental/PythonScriptPlugin/Content/Python/remote_execution.py
```

A pure Python module with no external dependencies. Most external tools (Houdini, Maya, VSCode extensions, etc.) use this module as-is or wrap it thinly.

### 2.2 Two-Channel Architecture

```
┌─ External Process ────────────┐              ┌─ UE Editor (PythonScriptPlugin) ──┐
│                               │   UDP mcast  │                                    │
│  remote_execution.py          │──239.0.0.1:6766──▶  node broadcast (pong)        │
│    .start()                   │              │                                    │
│    .open_command_connection() │              │                                    │
│                               │   TCP cmd    │                                    │
│    .run_command(python_code) ─┼─────────────▶│  editor Python interpreter        │
│       <── JSON response ──────┼─────────────▶│  (stdout / stderr / result)       │
└───────────────────────────────┘              └────────────────────────────────────┘
```

- **UDP channel**: multicast group `239.0.0.1:6766` (default). The external client sends a `ping`; the editor replies with a `pong` containing its node ID and TCP endpoint. Multiple editor instances on the same machine can be distinguished.
- **TCP channel**: a 1:1 connection to the discovered node. Python code is sent as a JSON message and the execution result is returned.

### 2.3 Message Format and Execution Modes

Messages are UTF-8 JSON. Key fields:

| Field | Description |
|---|---|
| `version` | Protocol version (currently `1`) |
| `magic` | `"ue_py"` signature |
| `source` / `dest` | Node IDs (UUID) |
| `type` | `ping`, `pong`, `open_connection`, `close_connection`, `command`, `command_result` |
| `data` | Type-specific payload |

The execution mode is specified in the `data` of a `command` message:

- **`ExecuteFile`** — treats the given string as a file and executes it at top level. Multiple statements and function definitions are permitted; no return value.
- **`ExecuteStatement`** — executes a single statement; no return value.
- **`EvaluateStatement`** — evaluates a single expression; returns the result as a repr string.

All three modes capture stdout and stderr and include them in the response.

### 2.4 State Persistence (global scope)

Independently of `exec_mode`, the **execution context** can be selected:

- `ExecuteInGlobalScope=True` — all calls share the same `__main__` namespace. Variables and functions created in previous calls persist. Beneficial for interactive/session-style workflows.
- `ExecuteInGlobalScope=False` — each call runs in an isolated scope. Guarantees isolation with fewer side effects.

**Current implementation note**: Epic's distributed `remote_execution.py` (copied into this repo as `execute.py`) does not include the `ExecuteInGlobalScope` field in the protocol message → UE uses its default (global scope). To expose this as a tool parameter, `execute.py::_RemoteExecutionCommandConnection.run_command`'s `data` dictionary would need to be extended, or an upstream Epic update would be needed. Currently unexposed.

### 2.5 Constraints and Caveats

- **Editor required.** Without a running editor, the bridge simply does not exist.
- **PIE world UObject access.** On PIE start, the editor world is duplicated into a separate PIE world. The default `get_editor_world()` cannot reach actors in the PIE world. Iterate worlds with `unreal.ObjectIterator(unreal.World)` and select the PIE world (`is_play_in_editor() == True`). Properties, function calls, actor spawn/destroy in that world are all accessible from Python.
- **Multicast-blocked environments.** Strict VPNs, containers, or isolated virtual NICs may block UDP multicast. A workaround fork (fixing the node address via an environment variable inside the MCP server) may be needed in such cases.
- **No authentication.** Anyone in multicast range can connect and execute arbitrary Python → restrict to localhost only (TTL=0 strongly recommended).
- **Buffer sizes.** Returning large results (e.g., a full level dump) requires `RemoteExecutionSend/ReceiveBufferSizeBytes` to be set to 2 MiB or more to avoid message truncation.

### 2.6 Active Configuration Example

`.uproject` Plugins entries are listed in the [root README §Install Step 4](../../../README.md#install).

`Config/DefaultEngine.ini`:

```ini
[/Script/PythonScriptPlugin.PythonScriptPluginSettings]
bDeveloperMode=True
bRemoteExecution=True
RemoteExecutionMulticastGroupEndpoint=239.0.0.1:6766
RemoteExecutionMulticastBindAddress=127.0.0.1
RemoteExecutionSendBufferSizeBytes=2097152
RemoteExecutionReceiveBufferSizeBytes=2097152
RemoteExecutionMulticastTtl=0
```

- `bDeveloperMode=True` enables Python stub generation and hot-reload convenience features.
- `RemoteExecutionMulticastTtl=0` **prevents packets from leaving the local machine** — the secure default.
- `RemoteExecutionMulticastBindAddress=127.0.0.1` also restricts to localhost (`0.0.0.0` binds all NICs — makes the editor reachable from any multicast-capable segment).

---

## 3. Comparison of External Invocation Methods

Three external invocation methods can sit on top of the Remote Execution protocol.

### 3.1 Bash + Python Client (stateless)

```
Claude ─(Bash tool)─▶ python ue_run.py "<python code>"
                            │
                            └─(UDP discover + TCP send)─▶ UE Editor
```

Each call executes a single Python script via Bash. The script uses `remote_execution.py` to discover → send command → print result → exit.

- **Advantages**
  - Minimal implementation. A single `ue_run.py` file is enough to start.
  - No MCP server installation or process management needed.
  - Direct stdout output makes debugging easy.
- **Disadvantages**
  - UDP discovery cost on every call (hundreds of milliseconds).
  - Claude does not recognize it as a "tool" — no schema or validation.
  - Difficult to maintain session state (process dies every call; protocol options exist but are cumbersome).
  - Long results are dumped raw into Claude's context.

### 3.2 MCP Server (adopted)

```
Claude ─(MCP protocol, stdio)─▶ ue_remote_execution_bridge server (resident) ─(TCP persistent)─▶ UE Editor
                   ▲
                   │ tool: run_python, start_pie, stop_pie, tail_output_log
```

A resident MCP server process **maintains** the TCP session with UE and exposes named tools to Claude.

- **Advantages**
  - Claude recognizes tool schemas → parameter validation, auto-suggestions, consistent calling convention.
  - Per-call connect latency is ~100–200 ms (UDP discovery + TCP handshake); connection reuse is a planned future optimization.
  - Results can be processed server-side — long log tails, error formatting, context savings.
  - State is preserved (Python global scope, recent error cache, subscriptable log streams).
  - Features requiring a persistent connection (e.g., real-time PIE log tailing) are natural to implement.
- **Disadvantages**
  - One additional process to manage (Claude Code `.mcp.json` registration, reconnect logic on restart).
  - Requires a decision: adopt existing open source or build in-house.
  - Bugs in the server itself add a debugging layer.

### 3.3 Commandlet Mode (reference)

```
Claude ─(Bash)─▶ UnrealEditor-Cmd.exe <uproject> -run=pythonscript -script=foo.py
```

Runs a Python script as a commandlet process without launching the editor.

- **Advantages**: works without a running editor. Suitable for CI and batch workloads.
- **Disadvantages**
  - Full editor subsystem boot on every call → tens of seconds or more.
  - **PIE is not supported** — play sessions cannot run in commandlet mode.
  - A separate code path unrelated to Remote Execution — no state persistence.

→ **Not suitable** for interactive, iterative sessions with Claude. Consider only for long-running batch import or build tasks.

### 3.4 Scenario Summary

| Scenario | Bash | MCP | commandlet |
|---|---|---|---|
| One-off "change material parameter and save" | ◯ | ◯ | △ (slow boot) |
| Bulk asset rename requiring dozens of calls | △ | ◎ | △ |
| Start PIE and stream live logs | △ | ◎ | ✕ |
| Manual one-off test | ◎ | △ | ✕ |
| Delegate ongoing UE control to Claude | △ | ◎ | ✕ |
| Nightly CI content import automation | △ | △ | ◎ |

---

## 4. Adopted Architecture — MCP Server

### 4.1 Rationale

The primary use case is Claude **iteratively** controlling UE. The benefits of context savings, state persistence, and tool schema exposure lead to choosing MCP.

### 4.2 Structure

```
┌─ Claude Code ─────────┐  stdio  ┌─ ue_remote_execution_bridge ───┐  TCP  ┌─ UE Editor ─────────────────────────┐
│  tool_use calls       │◀──────▶ │  MCP SDK                       │◀─────▶│  PythonScript Plugin                │
│                       │         │  execute.py                    │       │  unreal.* Python API                │
│                       │         │                                │       │    ^ UFUNCTION reflection            │
│                       │         │                                │       │  Plugins/RemoteExecutionBridge/     │
│                       │         │                                │       │  |- RemoteExecutionBridge (runtime) │
│                       │         │                                │       │  \- RemoteExecutionBridgeEditor     │
└───────────────────────┘         └────────────────────────────────┘       └─────────────────────────────────────┘
```

- Claude Code launches the `ue_remote_execution_bridge` server registered in `.mcp.json` via stdio.
- Each tool call opens a fresh UDP discovery + TCP handshake and closes the connection when the call completes. Connection reuse is deferred to a later revision.
- If the editor restarts, the server re-discovers and reconnects transparently.
- UFUNCTIONs called via `unreal.*` inside `run_python` are registered by `Plugins/RemoteExecutionBridge/` — when the Python API reaches its limits, extend this plugin (→ 4.7).

### 4.3 Currently Implemented Tools

For the exposed tool list and signatures, see the table in `../../../README.md §MCP Tools` (SoT).

A `global_scope` parameter was planned early in the design but is unexposed due to the protocol constraint described in §2.4.

#### Future Extension Candidates (not yet implemented)

The following **are not implemented**. The same operations are achievable via `run_python`; if a repeated call pattern emerges, promotion to a dedicated tool is worth considering. Signatures are drafts and may change at implementation time.

| Tool | Signature (draft) | Purpose |
|---|---|---|
| `create_asset` | `(package_path, asset_name, class_name, factory?, params?)` | Wrap `unreal.AssetTools.create_asset` |
| `save_assets` | `(paths: list[str])` / `save_all_dirty()` | Save modified assets |
| `list_assets` | `(path, recursive=True, class_filter?)` | Asset registry query |
| `get_selection` / `set_selection` | `(paths: list[str])` | Content browser / viewport selection |
| `list_actors` | `(level?, class_filter?)` | Enumerate world actors |

### 4.4 Server Implementation Choice — Option A vs Option B

#### Option A: Adopt an Existing Open-Source Server

Key candidates as of the survey (2026-04 — star/commit figures are volatile; re-check before adoption):

| Repository | Language | License | Tools | UE requirement | Notes |
|---|---|---|---|---|---|
| `runreal/unreal-mcp` | TS + Python hybrid | MIT | ~18 | Built-in PythonScriptPlugin (no extra plugin needed) | Mature tool schemas (`editor_list_assets`, `editor_run_python`, `editor_create_object`, screenshot/camera control, etc.). First release 2025-06, 96 stars |
| `radial-hks/MCP-Unreal-Server` | Python | Apache-2.0 | Few (`run_python`-centric) | Built-in Python | ~7 commits, ~6 stars. Thin wrapper around multicast discovery |
| `chongdashu/unreal-mcp` | Python | — | — | **Separate UE plugin required** | Explicitly EXPERIMENTAL. API may change significantly |
| `kvick-games/UnrealMCP` | — | — | — | Custom plugin | Gameplay-oriented |

- **Advantages**
  - Immediately runnable (npx/uv/pip once)
  - Tool schemas already designed — skip the "what to expose" question
  - Active repos (`runreal`) have bug report/patch cycles
- **Disadvantages**
  - **External dependency and supply-chain risk** — when upstream stalls, you inherit the fork
  - Cost of adapting tool schemas and error formats to this project's workflow
  - Some (`chongdashu`, `kvick-games`) require a custom UE plugin → build pipeline impact
  - Security model may differ from our requirements (localhost-only, TTL=0) — audit needed
  - Most have few stars and contributors, no release tags → long-term maintenance uncertain

#### Option B: Minimal In-House Implementation

```
mcp/ue_remote_execution_bridge/
├─ server.py     # MCP SDK, stdio transport, tool definitions
├─ execute.py    # Epic's remote_execution.py (renamed to avoid import-path collision)
├─ README.md     # Quick-start
├─ usage.log     # run_python invocation log (source for manual CHEATSHEET updates, gitignored)
└─ docs/
   ├─ DESIGN.md       # This document — protocol and ADR
   └─ CHEATSHEET.md   # Frequently-used UE Python API reference (manually aggregated from usage.log)
```

Initial target: 4 tools (`run_python`, `start_pie`, `stop_pie`, `tail_output_log`). The server body was projected at 300–500 lines; `server.py` settled at approximately 300 lines.

- **Advantages**
  - Full ownership and understanding of the entire codebase — error format, log filtering, state cache are all customizable
  - Shallow dependency tree (MCP SDK only). UE uses only the built-in `remote_execution.py`; no additional UE plugin installation
  - Security policy (localhost, TTL=0) can be embedded as needed. `run_python` is unconstrained — there is no code-pattern allowlist; trust is inherited from the MCP client.
  - Natural fit with the project's `mcp/` conventions and Python standards
- **Disadvantages**
  - Initial implementation and testing time (half a day to a day)
  - Each additional tool requires in-house work (Option A has many already)
  - Responsible for tracking MCP SDK updates and UE protocol changes

#### Decision Criteria

| Question | Favors A | Favors B |
|---|---|---|
| Need something running today? | ◎ | △ day's work |
| More than 10 tools needed? | ◎ | △ implementation burden |
| Error format / log filtering customization important? | △ upstream PR/fork | ◎ own code |
| Sensitive to external dependencies / supply-chain security? | △ third-party audit | ◎ minimal deps |
| Plan to ship this bridge in production long-term? | △ upstream lock-in | ◎ full ownership |
| Resistance to extra UE plugin installation? | `runreal` OK, some ✕ | ◎ built-in only |

#### Decision: Option B (in-house implementation)

Chose **Option B** to minimize external dependencies and supply-chain risk, maintain full control over error formatting and log filtering, and align naturally with the project's `mcp/` conventions.

Implementation scope:
- Dependencies: `mcp` Python SDK + Epic's `remote_execution.py` (included as `execute.py`). One external dependency.
- Initial 4 tools: `run_python`, `start_pie`, `stop_pie`, `tail_output_log`
- Promote `create_asset` · `save_assets` · `list_assets` etc. to dedicated tools as repeated call patterns emerge
- `runreal/unreal-mcp` used as **reference** for tool schema and response format design (MIT license → partial attribution if excerpted)

> **Amendment (2026-04):** Subsequent Python API limitations (Slate notifications, Blueprint editor internal APIs, etc.) led to adding the in-repo C++ plugin `Plugins/RemoteExecutionBridge/`. Option B's premise of "minimal external dependencies, full ownership" is preserved — the plugin is versioned together with the repo. §4.6 describes the on-screen notification limitation that triggered the plugin. For extension procedures, see `../README.md §C++ Plugin Extension`.

### 4.5 Claude Code Registration

Registration is covered in the [root README §Install Step 5](../../../README.md#install). Operational notes (`UE_PROJECT_ROOT` override, `/mcp` reload) are in the [MCP README §MCP Registration](../README.md#mcp-registration).

### 4.6 Notification Design Rationale

Operational behavior (eager/per-call connect timings, Output Log line, reconnect semantics) is documented in the [MCP README §Connection Lifecycle](../README.md#connection-lifecycle). This section records *why* the chosen channels were chosen.

**Viewport on-screen toasts were not adopted.** `unreal.SystemLibrary.print_string(..., print_to_screen=True)` goes through GEngine's `AddOnScreenDebugMessage` path, which only renders during PIE/Standalone world rendering — invisible when the editor is idle. Slate-native notifications (`FNotificationInfo` bottom-right toast) are not exposed in the UE 5.7 Python API — this limitation was one of the triggers for introducing `Plugins/RemoteExecutionBridge/`.

The current approach combines an editor toolbar badge (`SRemoteExecutionStatusBadge`) with a single Output Log line on first connect (suppressed thereafter via the module-level `_session_announced` flag — resets only on MCP server restart, not on UE editor restart). If a toast becomes necessary in the future, a `FNotificationInfo` UFUNCTION can be added to the `RemoteExecutionBridgeEditor` module. Extension procedure: `../README.md §C++ Plugin Extension`.

### 4.7 C++ Plugin Surface (RemoteExecutionBridge)

An in-repo C++ plugin that exposes engine-internal APIs unreachable by the Python API. Source: `Plugins/RemoteExecutionBridge/`.

**Two-module split**

| Module | Type | Loading phase | Purpose |
|---|---|---|---|
| `RemoteExecutionBridge` | UncookedOnly | Default | MCP session state (heartbeat, metadata, toolbar badge data) |
| `RemoteExecutionBridgeEditor` | Editor | PostEngineInit | Blueprint graph manipulation and other editor-only engine APIs |

`UncookedOnly` keeps the module out of cooked Shipping/Test targets by UBT definition — heartbeat UFUNCTIONs and session-metadata strings cannot leak into a packaged game binary, even without per-`.uproject` platform guards.

Module selection for new UFUNCTIONs: runtime UE APIs → `RemoteExecutionBridge`; editor-only APIs (`FBlueprintEditorUtils`, `GEditor`, Slate, etc.) → `RemoteExecutionBridgeEditor`.

**Current UFUNCTION catalog**

See the table in `../README.md §Existing UFUNCTION catalog` (SoT). Extension procedure: `../README.md §C++ Plugin Extension`.

---

## 5. Security and Operational Notes

- **Remote Execution has no authentication.** Any client in multicast range can connect and execute arbitrary Python. Set `RemoteExecutionMulticastTtl=0` to prevent packets from leaving the local machine.
- **Firewall**: Windows Defender will prompt to allow UDP 6766 and the editor's TCP listen port at least once. Allow only on the Private network profile.
- **Do not share on shared machines**: leaving this configuration on a public machine or conference room PC exposes the editor to anyone on the same LAN segment. Always keep TTL=0.
- **Editor restart resilience**: on TCP disconnect, the next call retries once + re-discovers. See §4.2 and the [MCP README §Connection Lifecycle](../README.md#connection-lifecycle) for details.
- **PIE re-entry**: world references change immediately after PIE starts — any cached world handles in MCP tool state must be invalidated.
- **Log pagination**: `tail_output_log` paginates the editor's Output Log file using a byte-offset cursor (max 256 KB per call, line cap `max_lines`). Long spans can be fetched incrementally using the returned `cursor` — the full log is never dumped into Claude's context.

---

## 6. References

- Unreal Python API (5.7): https://dev.epicgames.com/documentation/en-us/unreal-engine/python-api/?application_version=5.7
- Scripting the Unreal Editor Using Python: https://dev.epicgames.com/documentation/en-us/unreal-engine/scripting-the-unreal-editor-using-python
- Remote Control for Unreal Engine: https://dev.epicgames.com/documentation/en-us/unreal-engine/remote-control-for-unreal-engine
- Reference client (in installed engine): `<UE>/Engine/Plugins/Experimental/PythonScriptPlugin/Content/Python/remote_execution.py`
