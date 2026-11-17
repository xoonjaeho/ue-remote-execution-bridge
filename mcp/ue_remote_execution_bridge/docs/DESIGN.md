# ue-remote-execution-bridge Design Document

Protocol details and architecture decision records (ADR).

---

## 1. Protocol — UDP discovery + TCP command

UE's PythonScriptPlugin Remote Execution works over two channels.

### UDP multicast discovery (port 6766)

```
Python(MCP) ──── UDP broadcast ────► UE editor (239.0.0.1:6766)
               "open_connection" message
```

- Requires `bRemoteExecution=True` and `RemoteExecutionMulticastGroupEndpoint=239.0.0.1:6766` in `Config/DefaultEngine.ini`
- `RemoteExecutionMulticastBindAddress=127.0.0.1`, `RemoteExecutionMulticastTtl=0` — local loopback only
- Buffer size: `RemoteExecutionSendBufferSizeBytes=2097152`, `RemoteExecutionReceiveBufferSizeBytes=2097152` (2 MiB; prevents the `Remote party failed to send a valid response!` error when a payload exceeds the default)

### TCP command channel (inverted model)

```
Python(MCP) ◄─── reverse TCP connection ──── UE editor
  (TCP server, listen)
```

- Python listens on a TCP socket → UE receives the UDP message and connects back over TCP
- Once connected, Python sends a `run_command` message → UE runs the Python code → returns the result
- The client is Epic's official `remote_execution.py`, loaded at startup from the engine named by `UE_ENGINE_DIR` instead of being shipped (the file is covered by the Unreal Engine EULA). `server.py` replaces its accept step: 2 attempts of 5 s instead of 6, and a 120 s timeout on the command socket instead of blocking, so a node that never connects back or a hung editor cannot hold the cross-process mutex for long

---

## 2. Connection lifecycle (ADR-01)

**Decision: per-call fresh connection (UDP discovery + TCP handshake + close on every call)**

| Option | Pros | Cons |
|---|---|---|
| **per-call fresh** (chosen) | No stale node_id after an editor restart; simple code | 100–200 ms extra latency per call |
| Reuse the connection | Lower latency | Stale node_id cache; complex reconnect logic |

**Why**: The mutex already serializes concurrent sessions, so the benefit of caching shrinks. Against UE Python execution time (hundreds of ms to several seconds), 100–200 ms of overhead is acceptable.

---

## 3. Serializing concurrent sessions

### Win32 named mutex

```python
# Local\UE_RemoteExecution_Bridge
_mutex = ctypes.windll.kernel32.CreateMutexW(None, False, "Local\\UE_RemoteExecution_Bridge")
ctypes.windll.kernel32.WaitForSingleObject(_mutex, 15_000)  # 15 s timeout
# ... run the command ...
ctypes.windll.kernel32.ReleaseMutex(_mutex)
```

- A `run_python` call from a second Claude Code session waits until the first session's TCP command finishes
- `argtypes` / `restype` must be set explicitly to avoid corrupting the x64 HANDLE (8 bytes)

### Session lock files

- `src/.sessions/<PID>.lock` files track live server processes
- Deleted automatically by an `atexit` handler when the server exits
- Leftover files from force-killed (kill) processes are cleaned up on the next startup by reaping dead PIDs

---

## 4. Tool catalog (SoT)

> When a signature changes, edit this table first.

PIE start/stop, Output Log queries, asset search/save/move, and DataTable queries are not in this server — use the toolsets of Epic's official unreal-mcp (the ModelContextProtocol plugin) built into UE 5.8 and later. This server handles what the official toolsets can't do: arbitrary Python, and PIE input at screen coordinates or through the game viewport (the official SlateInspector toolset only targets Slate widgets by reference).

| Tool | Parameters (defaults) | Description |
|---|---|---|
| `run_python` | `code: str`, `mode: "exec_file"\|"exec_statement"\|"eval_statement" = "exec_file"`, `unattended: bool = True` | Run Python in the editor. Only `eval_statement` returns a value |
| `list_scripts` | — | List the script files registered in `scripts/` |
| `list_ue_nodes` | `discovery_timeout: float = 15.0` | List running UE editor instances and show the current target |
| `select_ue_node` | `node_id: str` | Pin the session to a specific UE editor instance (kept until restart) |
| `clear_ue_node` | — | Clear the node pin and return to env UE_NODE_ID or workspace-filter auto-selection |
| `update_cheatsheet` | `dry_run: bool = False` | Merge the `src/usage.log` delta into the `docs/CHEATSHEET.md` frequency table, then truncate (§8) |
| `simulate_pointer_input` | `action: "click"\|"drag"\|"long_press"`, `position: [x,y]`, `backend: "slate"\|"viewport" = "slate"`, `button: str = "LeftMouseButton"`, `to_position: [x,y]\|None = None`, `hold_seconds: float = 1.0`, `steps: int = 8` | Inject a PIE mouse pointer (Backend A/B). long_press is composed from a Down script → this server process's own sleep(hold_seconds) → an Up script (so the UE game thread never blocks). Coordinates are viewport-local pixels, the same for both backends. Coordinate-based mouse is not supported by Backend C (AutomationDriver) (§9) — use backend='viewport' if needed. JSON click/drag `{success, handled, message}`, long_press `{success, down, up}` |
| `simulate_key_input` | `key: str`, `action: "down"\|"up"\|"char"\|"focus_viewport"`, `backend: "slate"\|"viewport" = "slate"` | Inject PIE keyboard input (Backend A/B). backend='viewport' does not support action='char' (raw keys only). action='focus_viewport' moves focus to the PIE viewport before injecting gameplay keys (do not call it when typing into a text field — it steals focus). JSON `{success, handled, message}` |
| `run_driver_sequence` | `steps: list[dict]`, `timeout_seconds: float = 10.0` | Asynchronously kicks off a Backend C (AutomationDriver) keyboard sequence, then waits for completion with a non-blocking poll loop (host-side sleep interval) before returning — to the caller it looks like a synchronous tool. steps actions: press/release/press_chord/type/wait. enable_automation_driver is required beforehand (fails explicitly when disabled, no crash). JSON `{success, handle, done, sequence_success, message}` |
| `enable_automation_driver` | `auto_disable_seconds: float = 30.0` | Enable Backend C (reference-counted). Blocks real HW input while enabled. When the timeout elapses, only this Enable's share is released automatically (other sessions are unaffected). JSON `{success, generation, message}` — remember the generation to release exactly this share on disable |
| `disable_automation_driver` | `generation: int = 0` | Release one Backend C reference (it is actually disabled only when the count reaches 0). With generation, releases exactly that Enable call's share; when omitted (0), releases one arbitrary live generation (legacy compatibility). JSON `{success, message}` |

---

## 5. Security

Remote Execution has **no authentication**. Always keep the following settings.

- `RemoteExecutionMulticastTtl=0` — multicast packets never leave the local machine
- `RemoteExecutionMulticastBindAddress=127.0.0.1` — restricted to the loopback interface
- Do not use on shared PCs or LANs. Disable `bRemoteExecution=True` in any environment where it can't be guaranteed to stay local-only

`run_python` code is stored in plaintext in `src/usage.log`. Do not put secrets or credentials in code.

---

## 6. C++ plugin — two-module structure

`Plugins/RemoteExecutionBridge/`

| Module | Load phase | Role |
|---|---|---|
| `RemoteExecutionBridge` | Default | Heartbeat reception and session metadata (`URemoteExecutionBridgeLibrary`); virtual PIE input — Backend A/Slate (`URemoteExecutionBridgeSlateInputLibrary`), Backend B/GameViewportClient (`URemoteExecutionBridgeViewportInputLibrary`), Backend C/AutomationDriver keyboard only (`URemoteExecutionBridgeAutomationDriverLibrary`) |
| `RemoteExecutionBridgeEditor` | PostEngineInit | LevelEditor toolbar status badge (`SRemoteExecutionStatusBadge`, `FRemoteExecutionStatusMonitor`) |

### Toolbar badge

- A ● dot widget is shown on the right side of the LevelEditor toolbar
- Green if an MCP server heartbeat arrived within the last 12 seconds, red otherwise
- The MCP server's heartbeat thread calls the `URemoteExecutionBridgeLibrary::Heartbeat()` UFUNCTION every 2 seconds to update the state

**Tooltip format** (shown on hover):

```
Remote Execution Bridge
Status: Connected / Disconnected
Sessions: N
Heartbeat: N.Ns ago
Endpoint: 239.0.0.1:6766
TTL: 0 (local only)
UE
PID: {editor PID}
Node: {node_id}
Path: {project .uproject path}
MCP
PID: {MCP server PID} ({process name})
PPID: {parent PID} ({parent process name})
Path: {MCP server CWD}
Started: {start time}
Project: {last segment of CWD}
Branch: {git branch}
```

- `UE.PID`: `FPlatformProcess::GetCurrentProcessId()` — the editor's own PID (read directly in C++)
- `UE.Node`: the connected node_id passed in from MCP (`unknown` when not connected)
- `UE.Path`: the editor's project file path (`FPaths::GetProjectFilePath`) — always shown regardless of connection. Separate from the MCP path (below)
- `MCP.PID`/`MCP.PPID`: the connected session's PID and parent PID. The image name is resolved on the editor side with `FPlatformProcess::GetApplicationName(pid)` (no MCP protocol change). The same binary (claude.exe) has the same name, so tell sessions apart by Branch, Path, and PID
- `MCP.Path`: the MCP server CWD (`GetConnectedCwd` — pushed at connection time)
- Connection-dependent fields (Node, MCP.*, Started, Project, Branch) are updated only when there is exactly one active session — this prevents flapping from sessions overwriting each other (`unknown` when not updated)

### Workspace routing isolation (multi-editor)

When UE editors from several workspaces run at the same time, **every discovery path (heartbeat + per-call execution)** is scoped by `project_root` so commands don't go to the wrong workspace.

**Target selection priority** (`_discover_node`, when `preferred_node_id is None`):

1. A `select_ue_node()` session pin or env `UE_NODE_ID` → connect straight to that node_id (bypasses the filter and guard — the user picked that editor on purpose)
2. No pin → route to the editor whose pong response `project_root` matches `UE_PROJECT_ROOT` (resolved to an absolute path)
3. **No guessing without a discriminator**: if there are two or more nodes and no match (or pin), it does not pick `remote_nodes[0]`; it lists the candidates and **fails (raises)**. This closes the path that silently leaks into another workspace

- `UE_PROJECT_ROOT` is resolved to an absolute path relative to the `.mcp.json` directory (independent of cwd changes), and compared with the Windows-safe `os.path.normcase + normpath` (ignores case, trailing slashes, and separators). `resolve()` is not used so that nonexistent paths (other drives) can still be compared
- **Second guard** (`_assert_workspace`): right after connecting, if the editor's live `unreal.Paths.project_dir()` differs from `UE_PROJECT_ROOT`, any command is aborted before it runs. This is the last line of defense even when the discovery pong is stale or false (skipped when an explicit pin is used)
- **Re-pinning**: when a pinned editor restarts, it gets a new node_id. If a pin and `UE_PROJECT_ROOT` are both present and the pin isn't found, it searches again with the same workspace filter and updates the pin to the new id
- With several editors up at once, the pongs sometimes don't all return within 5 seconds, so the default discovery timeout is 15 seconds
- `list_ue_nodes`'s `selection_source`/`is_current_routing_target` reflect the same rules: `session`/`env`/`workspace-filter`/`single`/`unresolved`

### Procedure for adding a new UFUNCTION

1. **Pick a module**: runtime UE API → `RemoteExecutionBridge`; editor-only engine API → `RemoteExecutionBridgeEditor`
2. Edit the header and source: add `UFUNCTION(BlueprintCallable, Category="RemoteExecutionBridge")`
3. Add Build.cs dependencies (e.g. `FBlueprintEditorUtils` → `"Kismet"`, `UEditorEngine` → `"UnrealEd"`)
4. Build: Live Coding (`Ctrl+Alt+F11`) when only a function body changed; full build + editor restart when a new declaration is added
5. Verify + update the §7 catalog

---

## 7. UFUNCTION catalog (SoT)

> When something changes, edit this table first.

| Module | Class | Python call | Purpose |
|---|---|---|---|
| `RemoteExecutionBridge` | `URemoteExecutionBridgeSlateInputLibrary` | `unreal.RemoteExecutionBridgeSlateInputLibrary.viewport_to_absolute_position(world_context, viewport_position) -> Vector2D` | PIE viewport pixel coordinates → desktop absolute coordinates (converts a ProjectWorldToScreen result into click coordinates) |
| `RemoteExecutionBridge` | `URemoteExecutionBridgeSlateInputLibrary` | `unreal.RemoteExecutionBridgeSlateInputLibrary.simulate_pointer_down/up(world_context, viewport_position, button) -> bool` | Inject a pointer button Down/Up separately at viewport-local coordinates (FSlateApplication::ProcessMouseButtonDownEvent/UpEvent). Down registers a C++ watchdog (15 s one-shot) in case the matching Up is lost; it is cancelled automatically when the Up arrives |
| `RemoteExecutionBridge` | `URemoteExecutionBridgeSlateInputLibrary` | `unreal.RemoteExecutionBridgeSlateInputLibrary.simulate_click(world_context, viewport_position, button) -> bool` | Synchronous Down+Up composed click (completes within the same call — no watchdog needed) |
| `RemoteExecutionBridge` | `URemoteExecutionBridgeSlateInputLibrary` | `unreal.RemoteExecutionBridgeSlateInputLibrary.simulate_drag(world_context, from_position, to_position, button, steps=8) -> bool` | From Down → Steps interpolated MoveEvents → To Up |
| `RemoteExecutionBridge` | `URemoteExecutionBridgeSlateInputLibrary` | `unreal.RemoteExecutionBridgeSlateInputLibrary.simulate_click_on_widget(widget) -> bool` | Composed click at the widget center's desktop absolute coordinates — passes through the whole Slate hit test and routing |
| `RemoteExecutionBridge` | `URemoteExecutionBridgeSlateInputLibrary` | `unreal.RemoteExecutionBridgeSlateInputLibrary.simulate_click_at_absolute_position(absolute_position) -> bool` | Inject a click at desktop absolute coordinates — for 3D hits (buildings, etc.) and arbitrary coordinates |
| `RemoteExecutionBridge` | `URemoteExecutionBridgeSlateInputLibrary` | `unreal.RemoteExecutionBridgeSlateInputLibrary.inject_key_down/up(key) -> bool` | Inject a key Down/Up (ProcessKeyDownEvent/UpEvent). Routed to the widget (or viewport) that currently has keyboard focus — no automatic focus change. Return value = whether Slate actually consumed it (handled) |
| `RemoteExecutionBridge` | `URemoteExecutionBridgeSlateInputLibrary` | `unreal.RemoteExecutionBridgeSlateInputLibrary.inject_key_char(text) -> bool` | Inject a string one character at a time as KeyChar events (EditableText, etc.). The target field must be given focus before the call, e.g. with simulate_click |
| `RemoteExecutionBridge` | `URemoteExecutionBridgeSlateInputLibrary` | `unreal.RemoteExecutionBridgeSlateInputLibrary.focus_game_viewport() -> bool` | Explicitly move keyboard focus to the PIE game viewport (call before injecting gameplay keys). Do not call while typing into a text field (it steals focus) |
| `RemoteExecutionBridge` | `URemoteExecutionBridgeViewportInputLibrary` | `unreal.RemoteExecutionBridgeViewportInputLibrary.set_pointer_position(world_context, viewport_position) -> bool` | Move the cursor with `FViewport::SetMouse`. Must be called before `inject_viewport_key` (SetMouse updates the cursor state synchronously) |
| `RemoteExecutionBridge` | `URemoteExecutionBridgeViewportInputLibrary` | `unreal.RemoteExecutionBridgeViewportInputLibrary.inject_viewport_key(world_context, key, b_pressed) -> bool` | Shared Down/Up for mouse buttons and keyboard keys (`FInputKeyEventArgs::CreateSimulated`+`UGameViewportClient::InputKey`). Return value = actual handled. bPressed=true registers a watchdog in case the matching Up is lost |
| `RemoteExecutionBridge` | `URemoteExecutionBridgeViewportInputLibrary` | `unreal.RemoteExecutionBridgeViewportInputLibrary.simulate_viewport_click(world_context, viewport_position, button) -> bool` | SetPointerPosition + synchronous Down+Up composed click |
| `RemoteExecutionBridge` | `URemoteExecutionBridgeViewportInputLibrary` | `unreal.RemoteExecutionBridgeViewportInputLibrary.simulate_viewport_drag(world_context, from_position, to_position, button, steps=8) -> bool` | From Down → Steps interpolated SetPointerPosition moves → To Up |
| `RemoteExecutionBridge` | `URemoteExecutionBridgeAutomationDriverLibrary` | `unreal.RemoteExecutionBridgeAutomationDriverLibrary.enable_automation_driver(auto_disable_seconds=30.0) -> int64` | Enable Backend C (generation-based reference count — one session's timeout/Disable can't turn off another session's Enable). Blocks real HW input while enabled. The return value is this call's generation — pass it unchanged to disable and exactly this call's share is released |
| `RemoteExecutionBridge` | `URemoteExecutionBridgeAutomationDriverLibrary` | `unreal.RemoteExecutionBridgeAutomationDriverLibrary.disable_automation_driver(generation=0) -> bool` | Release one reference — `IAutomationDriverModule::Disable()` is actually called only when the count reaches 0. With a generation, releases exactly that generation (false if already released or nonexistent); with 0 (the default), releases one arbitrary live generation |
| `RemoteExecutionBridge` | `URemoteExecutionBridgeAutomationDriverLibrary` | `unreal.RemoteExecutionBridgeAutomationDriverLibrary.run_driver_sequence(steps_json) -> str` | Asynchronously kicks off a keyboard-only sequence (JSON: press/release/press_chord/type/wait) and returns a GUID handle immediately. Returns an empty string when disabled (§9 crash pitfall) |
| `RemoteExecutionBridge` | `URemoteExecutionBridgeAutomationDriverLibrary` | `unreal.RemoteExecutionBridgeAutomationDriverLibrary.poll_driver_sequence(handle) -> (bool, bool)\|None` | Non-blocking completion check. Python return: a `(bOutDone, bOutSuccess)` tuple when the handle is valid, `None` when invalid/exhausted (the bool return value itself is not included in the tuple — confirmed by measurement). Removed from the internal map immediately on completion |

BP graph editing (adding nodes, connecting pins, compiling) was left out of this plugin because the official unreal-mcp Blueprint toolset covers it. If you need an engine API that the official toolsets lack, add a UFUNCTION following the procedure above.

---

## 8. Usage logging + CHEATSHEET aggregation (ADR-02)

### Decision: a simple delimiter format + the `update_cheatsheet` MCP tool

Initially the server pre-extracted `api_symbols` and stored them in JSONL, but putting the extraction logic in the server increased complexity (it depended on an external aggregation script).

The new format stores only the raw code in `src/usage.log`:
```
---
2026-04-20T01:42:41.390241+00:00
import unreal
print(unreal.SystemLibrary.get_engine_version())
---
```

Aggregation is handled by the MCP tool `update_cheatsheet` — it extracts symbols with the regex `unreal\.[A-Za-z0-9_.]+`, merges them into the `docs/CHEATSHEET.md` frequency table, and truncates `src/usage.log`.

---

## 9. PIE virtual input: three backends (ADR-03)

A feature that lets Claude inject mouse/keyboard input into PIE to verify interactions automatically.

### The three backends

| Backend | C++ class | Path | Coverage |
|---|---|---|---|
| A: Slate | `URemoteExecutionBridgeSlateInputLibrary` | `FSlateApplication::ProcessMouseButtonDownEvent/UpEvent/ProcessMouseMoveEvent/ProcessKeyDownEvent/UpEvent/ProcessKeyCharEvent` | Mouse (Down/Up/Click/Drag/widget click) + keyboard (Down/Up/Char text typing) |
| B: GameViewportClient | `URemoteExecutionBridgeViewportInputLibrary` | `FViewport::SetMouse` + `UGameViewportClient::InputKey` (`FInputKeyEventArgs::CreateSimulated`) | Mouse (Click/Drag) + keyboard (raw keys only, no text typing) — reaches gameplay input (PlayerController, etc.) |
| C: AutomationDriver | `URemoteExecutionBridgeAutomationDriverLibrary` | `IAsyncAutomationDriver` (async kickoff + poll) | **Keyboard only** (press/release/press_chord/type/wait) — coordinate-based mouse is not supported |

**Coordinate contract**: the public A/B functions all take viewport-local pixel coordinates (so the caller's coordinate math doesn't change when switching backends).

**Backend C scope reduction**: measurement showed that the coordinate space `IAsyncActionSequence::MoveByOffset` moves the cursor in is completely separate from the real cursor state that `FViewport::SetMouse` (Backend B) produces. If you need coordinate-based mouse click/drag, use Backend B (`simulate_viewport_click`/`simulate_viewport_drag`).

### Core rules

- **No blocking**: Python remote execution runs synchronously inside a game-thread CoreTicker callback. A blocking wait such as `Future.Get()` inside a UFUNCTION is a self-deadlock — Backend C must only use an async kickoff (`RunDriverSequence` returns just a GUID handle immediately) plus a non-blocking poll (`PollDriverSequence`). long_press (holding the mouse button) spaces Down and Up apart with a `time.sleep` in the MCP server (`server.py`) process itself — no sleeping inside the engine.
- **Stuck-button/stuck-key watchdog**: Where Down/Up are split into separate calls (`SimulatePointerDown/Up`, `InjectViewportKey`), a 15-second one-shot `FTSTicker` watchdog is also set on the C++ side in case a host (`server.py`) crash loses the matching Up. A generation counter is issued at Down time and registered in a map, and cancelled immediately when the normal Up arrives. If the watchdog fires later but the generation has already been replaced/removed, it is a no-op — this prevents wrongly releasing a reused button.
- **Backend C handle cleanup**: When `PollDriverSequence` sees completion (`bOutDone=true`), it removes that entry from the internal GUID→Future map immediately. Without this it accumulates without bound per session.
- **Backend C reference count**: `EnableAutomationDriver`/`DisableAutomationDriver` manage the reference count with a generation-based `TSet<uint64>` — so one session's Disable or safety timeout can't turn off an Enable registered by another session (the module is a process-global singleton, so conflicts are possible when multiple MCP sessions coexist). `EnableAutomationDriver` returns its own generation, and passing that value to `DisableAutomationDriver(Generation)` releases exactly that generation — when unspecified (0), it falls back to the legacy path that releases an arbitrary live generation.

### ⚠️ AutomationDriver crash pitfall

`IAutomationDriverModule::CreateAsyncDriver()` internally calls `AutomatedApplication.ToSharedRef()`. `AutomatedApplication` is valid (non-null) only while `Enable()`d and is `Reset()` on `Disable()` — **calling `CreateAsyncDriver()`/`CreateDriver()` while disabled (or auto-disabled by the safety timeout) hard-crashes the editor immediately on the `IsValid()` assert in `TSharedPtr::ToSharedRef()`.**

To reproduce: call `EnableAutomationDriver(N seconds)`, wait until the safety timer auto-disables it after N seconds, then call `RunDriverSequence` (the normal delay between calls is enough for this to happen). So `RunDriverSequence` checks `IAutomationDriverModule::Get().IsEnabled()` at the start and returns an empty string (explicit failure) if it is disabled. Any new code touching Backend C must always keep this up-front check.

### Creating an FKey in Python (Python binding gotcha)

Both `unreal.Key("LeftMouseButton")` and `unreal.Key(key_name="LeftMouseButton")` fail with `TypeError: call() takes at most 0 arguments`. The correct pattern:

```python
key = unreal.Key()
key.set_editor_property('key_name', 'LeftMouseButton')
```

### PollDriverSequence return shape (Python binding gotcha)

For a UFUNCTION with a bool return plus two `bool&` out-params, like `bool PollDriverSequence(FString const& Handle, bool& bOutDone, bool& bOutSuccess)`, measurement confirmed that in Python:
- If the C++ return value is `true` (the handle exists) → a 2-tuple `(bOutDone, bOutSuccess)` (the C++ return bool itself is not included in the tuple)
- If the return value is `false` (the handle doesn't exist / is already exhausted) → `None`

The caller must branch with `isinstance(poll, tuple)` (see `scripts/driver_sequence_poll.py`).

### Measured results

- **Backend A/B × Click/RightClick/Drag/LongPress**: `handled=true` confirmed for every combination. Backend C is keyboard-only by design, so it is excluded from this matrix.
- **Hit test with the editor window minimized**: with the editor window fully minimized, both Backend A and B clicks were confirmed to be processed normally with `handled=true`. `ProcessMouseButtonDownEvent`/`InputKey` are synthetic events that call the Slate/viewport APIs directly inside the process without going through the OS message pump, so they only perform a hit test based on the Slate widget tree and cached geometry, and don't consult OS-level focus, Z-order, or minimized state. Because OS `SendInput` is not used, clicks never leak into another process's window.
- **AutomationDriver blocking real HW input**: `Enable()` really intercepts the physical mouse/keyboard, so this is a manual test that a human performs. Procedure:
  1. Call `enable_automation_driver(auto_disable_seconds=15)`
  2. For ~10 seconds right after Enable, try physical mouse clicks/keyboard input — there should be no response anywhere in PIE or the editor
  3. After 15 seconds, check that physical input works normally again (confirming the safety-timeout auto-Disable)

### Backend A click reliability: the 3 preconditions for `SButton`

For non-touch (mouse) events, `SButton` (and derivatives such as CommonUI's `SCommonButton`) `OnMouseButtonUp` decides whether a click happened solely by `bEventOverButton = IsHovered()` — unlike touch events, there is no geometry fallback. So all 3 of the following must hold at the time of the Down injection for the click to actually fire. If any one is missing, `FSlateApplication::ProcessMouseButtonDownEvent`/`ProcessMouseButtonUpEvent` return `handled=true`, but the widget's `OnClicked` is never called.

1. **Mouse capture**: a real `TSharedPtr<FGenericWindow>` must be passed to `ProcessMouseButtonDownEvent(platformWindow, event)` for `SButton::OnMouseButtonDown`'s `CaptureMouse` request to be established. If you pass `nullptr`, `HasMouseCapture()==false` at Up time.
2. **Hover**: a `ProcessMouseMoveEvent` with a real position delta must come in just before Down for `IsHovered()==true` to hold. If only Down/Up are injected without a Move, there is no Hover even when capture is taken, and the click is ignored (identical Last/New positions are treated as a zero delta and ignored, so an offset that registers as real movement is needed).
3. **App active state**: because of the `if (bHandleDeviceInputWhenApplicationNotActive || bAppIsActive || bIsVirtualInteraction)` gate in `FSlateApplication::ProcessReply`, mouse capture is never taken at all unless the editor is the OS foreground window.

`InjectPointerButtonEvent` (`RemoteExecutionBridgeSlateInputLibrary.cpp`) handles all 3 internally on Down injection — it obtains the native window with the `ResolveNativeWindow*` helpers and passes it in, injects a Move just before Down, and calls `::SetForegroundWindow(hwnd)` via `ForceWindowForeground()`. **`ForceWindowForeground()` takes OS focus to the editor window on every Down** — if you were working in another window, this has the side effect of stealing its focus. An alternative that doesn't steal the foreground is to keep `FSlateApplication::SetHandleDeviceInputWhenApplicationNotActive(true)` (the engine's official exception to the gate above) turned on only while injecting; this has not yet been applied to this plugin.

If you use the public Backend A functions such as `simulate_click`/`simulate_click_on_widget` as they are, the 3 conditions above are guaranteed automatically, so the caller doesn't need to handle them separately. However, **verify click success by an actual state change, not the return value (`handled`)** — `widget.is_pressed()`/`is_hovered()` transitions, or a before/after screen capture comparison. `handled=true` only means the event was routed; it is not evidence that `OnClicked` ran.

Using virtual input alone, we chained clicks through a multi-screen click-through to gameplay and confirmed with screenshots that the actual gameplay screen was entered.
