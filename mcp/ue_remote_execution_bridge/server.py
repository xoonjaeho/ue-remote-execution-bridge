"""MCP server exposing UE PythonScriptPlugin Remote Execution to Claude.

Tools: run_python, start_pie, stop_pie, tail_output_log.
"""
from __future__ import annotations

import ctypes
import json
import logging
import os
import re
import sys
import time
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Generator

_log = logging.getLogger(__name__)

from mcp.server.fastmcp import FastMCP

sys.path.insert(0, str(Path(__file__).parent))
from execute import (
    RemoteExecution,
    RemoteExecutionConfig,
    MODE_EXEC_FILE,
    MODE_EXEC_STATEMENT,
    MODE_EVAL_STATEMENT,
)
from matching import decide_node, parse_project_path


def _resolve_log_path() -> Path:
    """Locate the UE editor's Output Log file.

    Resolution order (first match wins):
    1. UE_PROJECT_ROOT env var — a directory containing a *.uproject file.
    2. Walk upward from this file (up to 7 levels) looking for a *.uproject.
    3. Fallback to parents[2] for the mirrored in-repo layout
       (<UEProject>/mcp/ue_remote_execution_bridge/server.py → <UEProject>).
    """
    def _build(root: Path) -> Path:
        uprojects = sorted(root.glob("*.uproject"))
        project_name = uprojects[0].stem if uprojects else root.name
        return root / "Saved" / "Logs" / f"{project_name}.log"

    env = os.environ.get("UE_PROJECT_ROOT")
    if env:
        root = Path(env).resolve()
        if root.is_dir() and any(root.glob("*.uproject")):
            return _build(root)
        _log.warning("UE_PROJECT_ROOT=%s is not a UE project directory; falling back to auto-detect.", env)

    # Prefer CWD (where Claude Code launched = the UE project root), checking the
    # directory itself before its parents. Robust under junctioned mcp/ layouts
    # where Path(__file__).resolve() escapes into the canonical plugin repo.
    cwd = Path(os.getcwd()).resolve()
    for d in (cwd, *list(cwd.parents)[:7]):
        if any(d.glob("*.uproject")):
            return _build(d)

    here = Path(__file__).resolve()
    for parent in list(here.parents)[:7]:
        if any(parent.glob("*.uproject")):
            return _build(parent)

    fallback = here.parents[2]
    _log.warning("No *.uproject found walking upward from %s; using fallback %s.", here, fallback)
    return _build(fallback)


def _resolve_project_stem() -> str | None:
    """Derive the .uproject stem (project name) from CWD or UE_PROJECT_ROOT.

    Resolution order:
    1. UE_PROJECT_ROOT env var — if it points to a valid project dir, use its stem.
    2. Walk from CWD (the directory itself, then its parents) for a *.uproject.
    3. None — workspace unresolved. Auto-connect refuses rather than attaching to an
       arbitrary editor (a coincidental cwd basename must not masquerade as a project).
    """
    env = os.environ.get("UE_PROJECT_ROOT")
    if env:
        root = Path(env).resolve()
        uprojects = sorted(root.glob("*.uproject"))
        if root.is_dir() and uprojects:
            return uprojects[0].stem
        _log.warning("UE_PROJECT_ROOT=%s is not a valid UE project directory.", env)

    cwd = Path(_CWD).resolve()
    for d in (cwd, *list(cwd.parents)[:7]):
        uprojects = sorted(d.glob("*.uproject"))
        if uprojects:
            return uprojects[0].stem

    _log.warning("No *.uproject resolved from cwd=%s; workspace UNRESOLVED "
                 "(auto-connect will refuse). Set UE_PROJECT_ROOT to pin.", cwd)
    return None


LOG_PATH = _resolve_log_path()
USAGE_LOG_PATH = Path(__file__).parent / "usage.log"
DISCOVERY_TIMEOUT = 5.0
EAGER_DISCOVERY_TIMEOUT = 2.0
# After the first editor appears, wait briefly so near-simultaneous editors' pongs all
# land before reading the discovered set (the set keys the cache AND drives probing) —
# otherwise whichever editor pongs first wins, non-deterministically.
_DISCOVERY_SETTLE = 0.25
MAX_TAIL_BYTES = 256 * 1024

_INSTRUCTIONS = """\
Runs Python inside a live Unreal Editor (developed against 5.7) via PythonScriptPlugin Remote Execution.

- The editor must be running. On `No Unreal Editor discovered within 5s`, stop and ask the user to launch it — do not retry in a loop.
- `run_python` code is appended to `usage.log` in plaintext. Never pass credentials through `code`.
- After any asset mutation, call `tail_output_log` with the previous `next_offset` to confirm side-effects. Silent success is not success.

When `unreal.*` lacks a symbol (typical sign: `AttributeError: module 'unreal' has no attribute …`), the companion C++ plugin `Plugins/RemoteExecutionBridge/` shipped with this MCP is the intended extension path — add a `UFUNCTION`, rebuild, call as `unreal.<Class>.<method>(...)`. Don't spend more than two pure-Python workarounds on the same goal. Catalog + recipe: `mcp/ue_remote_execution_bridge/README.md §C++ Plugin Extension`.

Deeper reference: `mcp/ue_remote_execution_bridge/README.md`, `docs/DESIGN.md`, `docs/CHEATSHEET.md`.
"""

mcp = FastMCP("ue_remote_execution_bridge", instructions=_INSTRUCTIONS)

_PID = os.getpid()
_PPID = os.getppid()
_CWD = os.getcwd()
_PROJECT_STEM = _resolve_project_stem()
# Opt-out escape hatch: restore legacy "attach to the first discovered editor"
# behavior when no editor matches the workspace. Default off (strict refuse).
_ALLOW_ANY = os.environ.get("UE_BRIDGE_ALLOW_ANY") == "1"
_START_TIME = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

_usage_lock = threading.Lock()

# ---------------------------------------------------------------------------
# Cross-process mutex — serializes UE command-socket ownership.
# UE Remote Execution supports exactly one TCP command connection at a time
# (all instances bind DEFAULT_COMMAND_ENDPOINT = 127.0.0.1:6776).
# "Local\" scope covers all processes in the same Windows user session
# without requiring SeCreateGlobalPrivilege.
# argtypes/restype are mandatory: HANDLE is 8 bytes on x64 and ctypes
# defaults to c_int, which truncates the pointer.
# ---------------------------------------------------------------------------
if sys.platform != "win32":
    raise RuntimeError(
        "ue_remote_execution_bridge currently supports Windows only. "
        "The server uses Win32 named mutexes (CreateMutexW) and the UE plugin "
        'declares SupportedTargetPlatforms: ["Win64"].'
    )

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_kernel32.CreateMutexW.restype = ctypes.c_void_p
_kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
_kernel32.WaitForSingleObject.restype = ctypes.c_uint32
_kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
_kernel32.ReleaseMutex.restype = ctypes.c_bool
_kernel32.ReleaseMutex.argtypes = [ctypes.c_void_p]
_kernel32.OpenProcess.restype = ctypes.c_void_p
_kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_bool, ctypes.c_uint32]
_kernel32.CloseHandle.restype = ctypes.c_bool
_kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
_kernel32.QueryFullProcessImageNameW.restype = ctypes.c_bool
_kernel32.QueryFullProcessImageNameW.argtypes = [
    ctypes.c_void_p, ctypes.c_uint32, ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_uint32)
]

_WAIT_OBJECT_0 = 0x00000000
_WAIT_ABANDONED = 0x00000080
_INFINITE = 0xFFFFFFFF
_UE_MUTEX_NAME = "Local\\UE_RemoteExecution_Bridge"

_ue_mutex = _kernel32.CreateMutexW(None, False, _UE_MUTEX_NAME)
if not _ue_mutex:
    raise OSError(f"CreateMutexW failed: {ctypes.get_last_error()}")


def _query_process_name(pid: int) -> str:
    # PROCESS_QUERY_LIMITED_INFORMATION works for most processes without elevation.
    # Buffer sized for Windows long paths (up to 32767 WCHARs). CPython's
    # create_unicode_buffer(n) allocates n+1 wide chars internally (extra slot for
    # the null terminator), so passing len(buf) as the API's size parameter is safe.
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    h = _kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(32768)
        size = ctypes.c_uint32(len(buf))
        if _kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return Path(buf.value).name
        return ""
    finally:
        _kernel32.CloseHandle(h)


_PARENT_NAME = _query_process_name(_PPID)


def _acquire_ue_mutex(timeout_ms: int = _INFINITE) -> bool:
    result = _kernel32.WaitForSingleObject(_ue_mutex, timeout_ms)
    return result in (_WAIT_OBJECT_0, _WAIT_ABANDONED)


def _release_ue_mutex() -> None:
    _kernel32.ReleaseMutex(_ue_mutex)


_HEARTBEAT_INTERVAL = 2.0

# ---------------------------------------------------------------------------
# Session lock files — one file per live server.py process.
# Heartbeat counts alive PIDs to push to UE as "active sessions".
# ---------------------------------------------------------------------------
_SESSIONS_DIR = Path(__file__).parent / ".sessions"
_SESSIONS_DIR.mkdir(exist_ok=True)
_SESSION_LOCK_FILE = _SESSIONS_DIR / f"{_PID}.lock"

import atexit as _atexit

@_atexit.register
def _remove_session_lock() -> None:
    try:
        _SESSION_LOCK_FILE.unlink(missing_ok=True)
    except Exception:
        pass


def _pid_alive(pid: int) -> bool:
    SYNCHRONIZE = 0x00100000
    h = _kernel32.OpenProcess(SYNCHRONIZE, False, pid)
    if h:
        _kernel32.CloseHandle(h)
        return True
    return False


def _reap_dead_locks() -> None:
    # Remove lock files whose owning process is gone — handles the case where
    # a prior server was killed before atexit could run (force-kill, crash, reboot).
    for lock_file in list(_SESSIONS_DIR.glob("*.lock")):
        try:
            pid = int(lock_file.stem)
        except ValueError:
            continue
        if not _pid_alive(pid):
            try:
                lock_file.unlink(missing_ok=True)
            except Exception:
                pass


def _count_active_sessions() -> int:
    _reap_dead_locks()
    count = sum(
        1 for lock_file in _SESSIONS_DIR.glob("*.lock")
        if lock_file.stem.isdigit() and _pid_alive(int(lock_file.stem))
    )
    return max(count, 1)


# Reap stale locks from prior crashed/killed servers before registering our own —
# this runs on every startup, independent of UE editor availability.
_reap_dead_locks()
_SESSION_LOCK_FILE.write_text(str(_PID), encoding="utf-8")

_session_announced = False  # written True after the first successful heartbeat


# Connection decision cache, keyed on the discovered node-id SET. Steady-state
# heartbeats/tool-calls with an unchanged editor set skip re-probing. UUID node ids
# are per editor session, so an unchanged set means the same editors are up and the
# prior decision still holds (an editor restart yields a new id → set changes →
# cache invalidates). Accessed only under the UE mutex (serialized), so no extra lock.
_conn_decision: dict[str, Any] | None = None


def _probe_project_path(remote: RemoteExecution) -> str | None:
    """Eval the *currently connected* editor's .uproject path.

    Returns the path string, or None when identity can't be read (eval failed,
    editor mid-boot, empty result). None is *inconclusive*, NOT "wrong project".
    """
    try:
        result = remote.run_command(
            "unreal.Paths.get_project_file_path()",
            unattended=True,
            exec_mode=MODE_EVAL_STATEMENT,
            raise_on_failure=False,
        )
    except Exception:
        return None
    if not result or not result.get("success"):
        return None
    return parse_project_path(result.get("result"))


def _open_remote(timeout: float) -> tuple[RemoteExecution, str] | str | None:
    """Discover UE, verify project identity, connect to the MATCHING editor only.

    Returns:
        (remote, node_id) — connected to the editor whose project matches the workspace
        str               — a refusal reason (actionable; surfaced to tool callers)
        None              — no editor discovered at all
    """
    global _conn_decision
    remote = RemoteExecution(RemoteExecutionConfig())
    try:
        remote.start()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and not remote.remote_nodes:
            time.sleep(0.1)
        if not remote.remote_nodes:
            remote.stop()
            return None

        # Let near-simultaneous editors' pongs all land before reading the set, so both the
        # cache key and the probe see the FULL editor set — not whichever editor happened to
        # pong first. Keyed on this settled set, the cache hits reliably in steady state.
        settle = min(_DISCOVERY_SETTLE, deadline - time.monotonic())
        if settle > 0:
            time.sleep(settle)
        node_ids = [n["node_id"] for n in remote.remote_nodes]
        current = frozenset(node_ids)
        project_stem = _PROJECT_STEM

        def _refuse_no_match(n: int) -> str:
            return (
                f"No UE editor for workspace {project_stem!r} "
                f"({n} other-project editor(s) ignored). "
                "Open this project's editor, or set UE_PROJECT_ROOT to pin."
            )

        def _connect(node_id: str) -> tuple[RemoteExecution, str] | str:
            """Open the command connection to the chosen editor. A transient connect-back
            failure becomes a retryable refusal, never a false 'no editor discovered'."""
            try:
                remote.open_command_connection(node_id)
            except Exception:
                remote.stop()
                return "Matched the workspace editor but the command connection dropped; retry shortly."
            return remote, node_id

        # Decision cache (matches only, keyed on the settled editor-id set) — a matched
        # editor still present skips re-probe. No-match is intentionally NOT cached: the
        # discovered set can lag a slow editor's first pong, so we always re-probe a
        # non-match rather than freeze a premature refusal.
        if _conn_decision is not None and _conn_decision["node_set"] == current:
            outcome = _conn_decision["outcome"]
            if outcome in current:
                return _connect(outcome)

        if project_stem is None:
            remote.stop()
            return (
                f"Workspace has no resolvable .uproject (cwd={_CWD}); refusing to attach "
                "to an arbitrary editor. Launch from the UE project root or set UE_PROJECT_ROOT."
            )

        # Probe each discovered editor's project identity. An open / connect-back failure is
        # INCONCLUSIVE, not fatal — one slow or flaky editor must not discard a valid match.
        def _probe(node_id: str) -> str | None:
            try:
                remote.open_command_connection(node_id)
                return _probe_project_path(remote)
            except Exception:
                return None
            finally:
                try:
                    remote.close_command_connection()
                except Exception:
                    pass

        matched, status = decide_node(node_ids, project_stem, _probe)
        _log.info("workspace=%r, %d editor(s) discovered, decision=%s",
                  project_stem, len(node_ids), status)

        if status == "match":
            assert matched is not None
            _conn_decision = {"node_set": current, "outcome": matched}
            return _connect(matched)

        if status == "ambiguous":
            remote.stop()
            return (
                f"Multiple UE editors report project {project_stem!r}. "
                "Set UE_PROJECT_ROOT to disambiguate."
            )

        if status == "inconclusive":
            # An editor is present but its project identity isn't readable yet
            # (mid-boot / Python not ready). Don't cache; let the caller retry.
            remote.stop()
            return (
                "UE editor(s) discovered but project identity is not readable yet "
                "(editor still initializing?). Retry shortly."
            )

        # no_match
        if _ALLOW_ANY:
            _log.warning(
                "UE_BRIDGE_ALLOW_ANY=1: connecting to first of %d non-matching editor(s).",
                len(node_ids),
            )
            return _connect(node_ids[0])
        remote.stop()
        return _refuse_no_match(len(node_ids))
    except Exception:
        try:
            remote.stop()
        except Exception:
            pass
        return None


def _try_heartbeat() -> None:
    """Update UE-side heartbeat/node-ID state. Skips silently if mutex unavailable."""
    global _session_announced, _PARENT_NAME
    if not _acquire_ue_mutex(timeout_ms=100):
        return
    try:
        # Lazy retry: the parent process may have been unreachable at module-load
        # (rare race window). Re-query here so the tooltip eventually shows a name.
        if not _PARENT_NAME:
            _PARENT_NAME = _query_process_name(_PPID)
        result = _open_remote(EAGER_DISCOVERY_TIMEOUT)
        if not isinstance(result, tuple):
            return  # None (no editor) or str (refused workspace) — skip heartbeat
        remote, node_id = result
        try:
            active = _count_active_sessions()
            session_lines = (
                f"    unreal.RemoteExecutionBridgeLibrary.set_connected_pid({_PID})\n"
                f"    unreal.RemoteExecutionBridgeLibrary.set_connected_ppid({_PPID})\n"
                f"    unreal.RemoteExecutionBridgeLibrary.set_connected_cwd({json.dumps(_CWD)})\n"
                f'    unreal.RemoteExecutionBridgeLibrary.set_connected_start_time("{_START_TIME}")\n'
                f"    unreal.RemoteExecutionBridgeLibrary.set_connected_parent_name({json.dumps(_PARENT_NAME)})\n"
            ) if active == 1 else ""
            announce_line = (
                'print("[MCP] ue_remote_execution_bridge server connected")\n'
                if not _session_announced else ""
            )
            code = (
                "import unreal\n"
                "try:\n"
                "    unreal.RemoteExecutionBridgeLibrary.heartbeat()\n"
                f"    unreal.RemoteExecutionBridgeLibrary.set_connected_node_id({json.dumps(node_id)})\n"
                f"    unreal.RemoteExecutionBridgeLibrary.set_active_sessions({active})\n"
                + session_lines
                + "except AttributeError:\n"
                "    pass\n"
                + announce_line
            )
            cmd_result = remote.run_command(code, unattended=True, exec_mode=MODE_EXEC_FILE, raise_on_failure=False)
            if not _session_announced and cmd_result.get("success"):
                _session_announced = True
        finally:
            try:
                remote.stop()
            except Exception:
                pass
    except Exception:
        pass
    finally:
        _release_ue_mutex()


def _heartbeat_loop() -> None:
    while True:
        time.sleep(_HEARTBEAT_INTERVAL)
        _try_heartbeat()


def _start_heartbeat() -> None:
    """Start the background heartbeat thread. Called from __main__ only, so that
    importing this module (e.g. in tests) does not spawn a thread that mutates the
    connection-decision cache underneath the test."""
    threading.Thread(target=_heartbeat_loop, daemon=True).start()


_MUTEX_ACQUIRE_TIMEOUT_MS = 15000


@contextmanager
def _ue_connection() -> Generator[RemoteExecution, None, None]:
    """Acquire the cross-process mutex and open a fresh UE command connection.

    Raises TimeoutError if another session holds the mutex for longer than
    _MUTEX_ACQUIRE_TIMEOUT_MS. The connection is closed and the mutex
    released when the context exits.
    """
    if not _acquire_ue_mutex(_MUTEX_ACQUIRE_TIMEOUT_MS):
        raise TimeoutError(
            "UE connection is held by another session. Retry shortly."
        )
    try:
        result = _open_remote(DISCOVERY_TIMEOUT)
        if result is None:
            raise RuntimeError(
                f"No Unreal Editor discovered within {DISCOVERY_TIMEOUT}s. "
                "Is the editor running with PythonScriptPlugin + "
                "bRemoteExecution=True, and multicast 239.0.0.1:6766 reachable?"
            )
        if isinstance(result, str):
            raise RuntimeError(result)  # refused: wrong workspace / unresolved / ambiguous
        remote, node_id = result
        try:
            remote.run_command(
                "import unreal\n"
                "try:\n"
                f"    unreal.RemoteExecutionBridgeLibrary.set_connected_node_id({json.dumps(node_id)})\n"
                f"    unreal.RemoteExecutionBridgeLibrary.set_connected_pid({_PID})\n"
                f"    unreal.RemoteExecutionBridgeLibrary.set_connected_ppid({_PPID})\n"
                f"    unreal.RemoteExecutionBridgeLibrary.set_connected_cwd({json.dumps(_CWD)})\n"
                f'    unreal.RemoteExecutionBridgeLibrary.set_connected_start_time("{_START_TIME}")\n'
                f"    unreal.RemoteExecutionBridgeLibrary.set_connected_parent_name({json.dumps(_PARENT_NAME)})\n"
                "except AttributeError:\n"
                "    pass\n",
                unattended=True,
                exec_mode=MODE_EXEC_FILE,
                raise_on_failure=False,
            )
            yield remote
        finally:
            try:
                remote.stop()
            except Exception:
                pass
    finally:
        _release_ue_mutex()


def _log_usage(code: str) -> None:
    """Append user-supplied run_python code to usage.log for later CHEATSHEET aggregation.

    Best-effort — never raises. Internal PIE/tail snippets bypass this by calling
    `_run` directly.
    """
    try:
        with _usage_lock, open(USAGE_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"---\n{datetime.now(timezone.utc).isoformat()}\n{code}\n")
    except Exception:
        pass


def _run(code: str, mode: str, unattended: bool) -> dict[str, Any]:
    try:
        with _ue_connection() as remote:
            result = remote.run_command(
                code, unattended=unattended, exec_mode=mode, raise_on_failure=False
            )
    except (TimeoutError, RuntimeError, OSError) as e:
        return {"success": False, "result": None, "stdout": "", "stderr": str(e), "raw_output": []}
    output_entries = result.get("output") or []
    stdout = "".join(e.get("output", "") for e in output_entries if e.get("type") == "Info")
    stderr = "".join(
        e.get("output", "")
        for e in output_entries
        if e.get("type") in ("Error", "Warning")
    )
    return {
        "success": bool(result.get("success")),
        "result": result.get("result"),
        "stdout": stdout,
        "stderr": stderr,
        "raw_output": output_entries,
    }


@mcp.tool()
def run_python(
    code: str,
    mode: str = "exec_file",
    unattended: bool = True,
) -> dict[str, Any]:
    """Execute arbitrary Python inside the running Unreal Editor.

    Args:
        code: Python source. Multi-statement allowed for exec_file; single
            statement/expression for exec_statement/eval_statement.
        mode: One of 'exec_file' (default, multi-line), 'exec_statement',
            'eval_statement' (returns the evaluated repr).
        unattended: True suppresses modal dialogs in the editor. Set False to
            allow user-facing prompts.

    Returns a dict with success, result (eval only), stdout, stderr, and the
    raw output entries from the editor.
    """
    mode_map = {
        "exec_file": MODE_EXEC_FILE,
        "exec_statement": MODE_EXEC_STATEMENT,
        "eval_statement": MODE_EVAL_STATEMENT,
    }
    if mode not in mode_map:
        raise ValueError(f"mode must be one of {list(mode_map)}, got {mode!r}")
    _log_usage(code)
    return _run(code, mode_map[mode], unattended)


_START_PIE_CODE = """
import unreal
les = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
if les.is_in_play_in_editor():
    print("PIE already running; no-op")
else:
    les.editor_request_begin_play()
    print("PIE start requested")
""".strip()

_STOP_PIE_CODE = """
import unreal
les = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
if not les.is_in_play_in_editor():
    print("PIE not running; no-op")
else:
    les.editor_request_end_play()
    print("PIE end requested")
""".strip()


@mcp.tool()
def start_pie() -> dict[str, Any]:
    """Start a Play-In-Editor session in the running editor."""
    return _run(_START_PIE_CODE, MODE_EXEC_FILE, unattended=True)


@mcp.tool()
def stop_pie() -> dict[str, Any]:
    """End the current PIE session."""
    return _run(_STOP_PIE_CODE, MODE_EXEC_FILE, unattended=True)


_LOG_LINE_RE = re.compile(
    r"^\[(?P<ts>[^\]]+)\]\[\s*\d+\](?P<category>[^:]+):\s*(?:(?P<verb>Warning|Error|Fatal|Display|Verbose|VeryVerbose):\s*)?(?P<msg>.*)$"
)

_FILTER_REGEX_MAX_LEN = 256


def _validate_filter_regex(pattern: str) -> re.Pattern[str]:
    # Length cap blocks the cheapest catastrophic-backtracking payloads
    # (long alternations / nested quantifiers). Compilation surfaces obvious
    # syntax errors as a clean ValueError instead of crashing inside re.search.
    if len(pattern) > _FILTER_REGEX_MAX_LEN:
        raise ValueError(
            f"filter_regex too long ({len(pattern)} > {_FILTER_REGEX_MAX_LEN})."
        )
    return re.compile(pattern)


@mcp.tool()
def tail_output_log(
    since_offset: int | None = None,
    filter_regex: str | None = None,
    max_lines: int = 500,
) -> dict[str, Any]:
    """Read recent lines from the editor's Output Log file.

    Args:
        since_offset: Byte offset returned from a prior call. Reads only new
            bytes past this point. Omit to read the tail of the file.
        filter_regex: Optional regex applied to each raw line; non-matching
            lines are dropped.
        max_lines: Cap on returned lines (most recent kept when truncating).

    Returns a dict with lines (parsed when possible), cursor (next offset),
    truncated (bool), and log_path. Each call reads at most MAX_TAIL_BYTES;
    follow the cursor to paginate longer spans.
    """
    try:
        f = open(LOG_PATH, "rb")
    except FileNotFoundError:
        return {
            "lines": [],
            "cursor": 0,
            "truncated": False,
            "log_path": str(LOG_PATH),
            "note": "Log file does not exist yet — editor has not flushed.",
        }

    with f:
        f.seek(0, 2)
        file_size = f.tell()
        if since_offset is not None and since_offset > file_size:
            # Log was rotated/truncated under us; the old cursor points past EOF.
            # Reset to the start of the new file rather than clamp to EOF (which
            # would silently skip the entire rotated file).
            start = 0
        elif since_offset is not None:
            start = max(0, since_offset)
        else:
            start = max(0, file_size - MAX_TAIL_BYTES)
        f.seek(start)
        raw = f.read(MAX_TAIL_BYTES)

    # Trim a partial trailing line so we never emit a half-decoded record and
    # never re-emit its remainder on the next call. If no newline was read,
    # fall through with the raw buffer as-is (single very long line case).
    last_newline = raw.rfind(b"\n")
    if last_newline != -1 and last_newline + 1 < len(raw):
        raw = raw[: last_newline + 1]

    next_cursor = start + len(raw)
    bytes_truncated = next_cursor < file_size

    text = raw.decode("utf-8", errors="replace")
    lines = text.splitlines()
    if filter_regex:
        pat = _validate_filter_regex(filter_regex)
        lines = [ln for ln in lines if pat.search(ln)]

    lines_truncated = len(lines) > max_lines
    if lines_truncated:
        lines = lines[-max_lines:]

    parsed: list[dict[str, Any]] = []
    for ln in lines:
        m = _LOG_LINE_RE.match(ln)
        if m:
            parsed.append(
                {
                    "ts": m.group("ts"),
                    "category": m.group("category").strip(),
                    "verbosity": m.group("verb") or "Info",
                    "msg": m.group("msg"),
                    "raw": ln,
                }
            )
        else:
            parsed.append({"raw": ln})

    return {
        "lines": parsed,
        "cursor": next_cursor,
        "truncated": lines_truncated or bytes_truncated,
        "log_path": str(LOG_PATH),
    }


if __name__ == "__main__":
    _start_heartbeat()
    mcp.run()
