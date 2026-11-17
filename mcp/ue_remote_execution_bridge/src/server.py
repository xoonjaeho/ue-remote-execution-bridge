"""Remote Execution MCP Server.

Bridge architecture: cross-process mutex, per-call fresh connection,
background heartbeat, session tracking. Base tool run_python extended with
gated automation-input tools. PIE
start/stop, output-log tailing, and asset/DataTable operations are left to
Epic's official unreal-mcp toolsets (UE 5.8+) — see README.md.
"""
from __future__ import annotations

import base64
import ctypes
import importlib.util
import json
import logging
import os
import re
import socket
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Generator

try:
	from mcp.server.mcpserver import MCPServer as FastMCP  # mcp 2.x
except ImportError:
	from mcp.server.fastmcp import FastMCP  # mcp 1.x

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Epic's remote_execution.py — loaded from the engine, not shipped here: the file
# is covered by the Unreal Engine EULA.
# ---------------------------------------------------------------------------

_REMOTE_EXECUTION_SUBPATH = Path("Plugins/Experimental/PythonScriptPlugin/Content/Python/remote_execution.py")
# Epic's accept loop makes 6 attempts of 5 s, so a node that never connects back
# holds the cross-process mutex for 30 s.
_ACCEPT_ATTEMPTS = 2
# Epic leaves the command socket blocking; a hung editor would hold the mutex forever.
_COMMAND_SOCKET_TIMEOUT_S = 120


def _load_remote_execution() -> Any:
	env = os.environ.get("UE_ENGINE_DIR")
	if not env:
		raise RuntimeError(
			"UE_ENGINE_DIR is not set. Set it in .mcp.json to the engine install folder "
			"(the one that contains Engine/, e.g. C:/Program Files/Epic Games/UE_5.8)."
		)
	candidates = [Path(env) / "Engine" / _REMOTE_EXECUTION_SUBPATH, Path(env) / _REMOTE_EXECUTION_SUBPATH]
	path = next((p for p in candidates if p.is_file()), None)
	if path is None:
		raise RuntimeError(f"remote_execution.py not found under UE_ENGINE_DIR: {candidates[0]}")
	spec = importlib.util.spec_from_file_location("ue_remote_execution", path)
	module = importlib.util.module_from_spec(spec)
	spec.loader.exec_module(module)
	return module


def _try_accept(self: Any, broadcast_connection: Any) -> None:
	for _ in range(_ACCEPT_ATTEMPTS):
		broadcast_connection.broadcast_open_connection(self._remote_node_id)
		try:
			channel, _address = self._command_listen_socket.accept()
		except socket.timeout:
			continue
		channel.settimeout(_COMMAND_SOCKET_TIMEOUT_S)
		self._command_channel_socket = channel
		return
	raise RuntimeError("Remote party failed to attempt the command socket connection!")


def _is_complete_json(data: bytes) -> bool:
	# A message is always a JSON object - skip parsing fragments that do not end in '}', or re-parsing on
	# every recv adds up on large responses
	if not data.rstrip().endswith(b"}"):
		return False
	try:
		json.loads(data.decode("utf-8"))
	except (json.JSONDecodeError, UnicodeDecodeError):
		return False
	return True


def _receive_message(self: Any, expected_type: str) -> Any:
	# Epic's version treats a recv shorter than the buffer as the end - a response TCP splits into several
	# segments gets cut, and one whose length is a multiple of the buffer blocks on the next recv
	data = b""
	while True:
		part = self._command_channel_socket.recv(_remote_execution.DEFAULT_RECEIVE_BUFFER_SIZE)
		if not part:
			break
		data += part
		if _is_complete_json(data):
			break
	if data:
		message = _remote_execution._RemoteExecutionMessage(None, None)
		if message.from_json_bytes(data) and message.passes_receive_filter(self._node_id) and message.type_ == expected_type:
			return message
	raise RuntimeError("Remote party failed to send a valid response!")


_remote_execution = _load_remote_execution()
_remote_execution._RemoteExecutionCommandConnection._try_accept = _try_accept
_remote_execution._RemoteExecutionCommandConnection._receive_message = _receive_message
RemoteExecution = _remote_execution.RemoteExecution
RemoteExecutionConfig = _remote_execution.RemoteExecutionConfig
MODE_EXEC_FILE = _remote_execution.MODE_EXEC_FILE
MODE_EXEC_STATEMENT = _remote_execution.MODE_EXEC_STATEMENT
MODE_EVAL_STATEMENT = _remote_execution.MODE_EVAL_STATEMENT

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

SCRIPTS_DIR = Path(__file__).parent.parent / "scripts"
USAGE_LOG_PATH = Path(__file__).parent / "usage.log"
CHEATSHEET_PATH = Path(__file__).parent.parent / "docs" / "CHEATSHEET.md"

# ---------------------------------------------------------------------------
# Cross-process mutex — serializes UE command-socket ownership.
# UE Remote Execution supports exactly one TCP command connection at a time.
# argtypes/restype are mandatory: HANDLE is 8 bytes on x64.
# ---------------------------------------------------------------------------

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

_WAIT_OBJECT_0 = 0x00000000
_WAIT_ABANDONED = 0x00000080
_INFINITE = 0xFFFFFFFF
_UE_MUTEX_NAME = "Local\\UE_RemoteExecution_Bridge"

_ue_mutex = _kernel32.CreateMutexW(None, False, _UE_MUTEX_NAME)
if not _ue_mutex:
	raise OSError(f"CreateMutexW failed: {ctypes.get_last_error()}")


def _acquire_ue_mutex(timeout_ms: int = _INFINITE) -> bool:
	result = _kernel32.WaitForSingleObject(_ue_mutex, timeout_ms)
	return result in (_WAIT_OBJECT_0, _WAIT_ABANDONED)


def _release_ue_mutex() -> None:
	_kernel32.ReleaseMutex(_ue_mutex)


# ---------------------------------------------------------------------------
# Session lock files — one file per live server.py process.
# Heartbeat counts alive PIDs to push to UE as "active sessions".
# ---------------------------------------------------------------------------

_PID = os.getpid()
_PPID = os.getppid()
_CWD = os.getcwd()
_START_TIME = datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _get_git_branch() -> str:
	"""Read branch name by parsing the HEAD file directly — no git in PATH required.
	Walks upward from server.py location, so CWD/env vars don't affect resolution."""
	try:
		for directory in Path(__file__).resolve().parents:
			git_path = directory / ".git"
			if git_path.is_file():
				# In a worktree or submodule .git is a "gitdir: <path>" file; a relative path is relative to that file's directory.
				git_path = directory / git_path.read_text(encoding="utf-8").strip().removeprefix("gitdir:").strip()
			head_file = git_path / "HEAD"
			if head_file.exists():
				content = head_file.read_text(encoding="utf-8").strip()
				if content.startswith("ref: refs/heads/"):
					return content[len("ref: refs/heads/"):]
				return content[:8]  # detached HEAD — short SHA
	except Exception:
		pass
	return ""


_GIT_BRANCH: str = _get_git_branch()

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


_reap_dead_locks()
_SESSION_LOCK_FILE.write_text(str(_PID), encoding="utf-8")

# ---------------------------------------------------------------------------
# Heartbeat — background thread; updates UE toolbar badge
# ---------------------------------------------------------------------------

_HEARTBEAT_INTERVAL = 2.0
_HEARTBEAT_MAX_INTERVAL = 30.0
_EAGER_DISCOVERY_TIMEOUT = 2.0
# With editors open in several workspaces at once, not every PONG returns within 5 s,
# so a live editor was sometimes reported as absent (measured: not found at 5 s, found at 12 s).
_DISCOVERY_TIMEOUT = 15.0
_MUTEX_ACQUIRE_TIMEOUT_MS = 15000
# Longest stretch a poll holds one connection (= the mutex). Other sessions fail after _MUTEX_ACQUIRE_TIMEOUT_MS
# and the editor badge turns disconnected after 12 s without a heartbeat (RemoteExecutionStatusMonitor
# BridgeTimeoutSeconds), so this must stay well below both.
_POLL_SEGMENT_SECONDS = 5.0

_session_announced = False
_usage_lock = threading.Lock()

# Node selection state — priority: session > env UE_NODE_ID > fallback (remote_nodes[0])
_ENV_DEFAULT_NODE_ID: str | None = os.environ.get("UE_NODE_ID")
_SELECTED_NODE_ID: str | None = None
# Normalized project_root the pinned node advertised at select_ue_node time (None if it was not visible then).
_SELECTED_NODE_ROOT: str | None = None
_SELECTED_NODE_LOCK = threading.Lock()


def _resolve_target_node_id() -> str | None:
	"""Return preferred node_id: session selection > env UE_NODE_ID > None (→ remote_nodes[0])."""
	with _SELECTED_NODE_LOCK:
		if _SELECTED_NODE_ID is not None:
			return _SELECTED_NODE_ID
	return _ENV_DEFAULT_NODE_ID


def _norm_path(p: str) -> str:
	"""Windows-safe path key for comparing self-reported PONG paths against the filter.
	normcase+normpath only — no resolve()/abspath: must work for paths on other drives
	or not present on this machine (case-insensitive, trailing-slash/separator agnostic)."""
	if not p:
		return ""
	return os.path.normcase(os.path.normpath(p))


def _resolve_project_root_filter() -> str | None:
	"""Absolute, normalized project_root this server is scoped to (env UE_PROJECT_ROOT).
	Relative values resolve against the .mcp.json directory — NOT cwd — so a changed
	working dir can't silently widen the scope. None if UE_PROJECT_ROOT unset."""
	env = os.environ.get("UE_PROJECT_ROOT")
	if not env:
		return None
	root = Path(env)
	if not root.is_absolute():
		base = next((p for p in Path(__file__).resolve().parents if (p / ".mcp.json").exists()), None)
		root = (base / env) if base is not None else (Path(os.getcwd()) / env)
	return _norm_path(str(root))


# Workspace scope — computed once. All command routing is constrained to the editor
# whose advertised project_root matches this (unless an explicit node pin overrides).
_PROJECT_ROOT_FILTER: str | None = _resolve_project_root_filter()


def _discover_node(
	remote: RemoteExecution,
	timeout: float,
	preferred_node_id: str | None,
	strict: bool,
	project_root_filter: str | None,
) -> str | None:
	# Discovery (UDP) does not use the UE command socket, so it is kept separate from connection (_connect_node).
	# Both callers (_try_heartbeat, _ue_connection) still hold the mutex around discovery + connect so the pair is atomic;
	# only list_ue_nodes / select_ue_node run their own UDP discovery without it.
	remote.start()
	deadline = time.monotonic() + timeout
	if preferred_node_id is None:
		# With a filter, wait until a matching node shows up - when a PONG from another workspace's editor arrives first,
		# stopping at the first node ended in "none match"
		want = _norm_path(project_root_filter) if project_root_filter else ""
		while time.monotonic() < deadline and not (
				any(_norm_path(n.get("project_root", "")) == want for n in remote.remote_nodes) if want
				else remote.remote_nodes):
			time.sleep(0.1)
		nodes = list(remote.remote_nodes)
		if not nodes:
			remote.stop()
			if strict:
				raise RuntimeError(
					f"No Unreal Editor discovered within {timeout}s. "
					"Is the editor running with PythonScriptPlugin + bRemoteExecution=True, "
					"and multicast 239.0.0.1:6766 reachable?"
				)
			return None
		if project_root_filter:
			candidates = [n for n in nodes if _norm_path(n.get("project_root", "")) == want]
			if not candidates:
				discovered = [f'{n["node_id"][:8]}={n.get("project_root", "?")}' for n in nodes]
				remote.stop()
				if strict:
					raise RuntimeError(
						f"{len(nodes)} editor(s) found but none match this workspace "
						f"(UE_PROJECT_ROOT -> {project_root_filter}). Discovered: {discovered}. "
						"Start this workspace's editor, or pin another with select_ue_node() if intended."
					)
				return None
			if len(candidates) > 1:
				logging.warning("Multiple editors share project_root %s; using first.", project_root_filter)
			return candidates[0]["node_id"]
		if len(nodes) == 1:
			return nodes[0]["node_id"]
		# >1 editor and no workspace filter / pin: refuse to guess (would corrupt wrong workspace).
		discovered = [f'{n["node_id"][:8]}={n.get("project_root", "?")}' for n in nodes]
		remote.stop()
		if strict:
			raise RuntimeError(
				f"{len(nodes)} editors discovered with no UE_PROJECT_ROOT filter or node pin "
				f"— refusing to guess. Discovered: {discovered}. "
				"Set UE_PROJECT_ROOT in .mcp.json or call select_ue_node()."
			)
		return None
	found = None
	while time.monotonic() < deadline:
		found = next(
			(n for n in remote.remote_nodes if n["node_id"] == preferred_node_id),
			None,
		)
		if found is not None:
			break
		time.sleep(0.1)
	if found is None:
		current_ids = [n["node_id"] for n in remote.remote_nodes]
		remote.stop()
		if strict:
			raise RuntimeError(
				f"Target node '{preferred_node_id}' not found within {timeout}s. "
				f"Discovered nodes: {current_ids or '(none)'}. "
				"Use clear_ue_node() to reset or select_ue_node() to pick another."
			)
		return None
	return preferred_node_id


def _connect_node(remote: RemoteExecution, node_id: str) -> None:
	try:
		remote.open_command_connection(node_id)
	except Exception:
		# The discovery-failure path calls stop() before raising, but the connect-failure path did not, leaving the UDP socket and discovery thread behind
		try:
			remote.stop()
		except Exception:
			pass
		raise


def _open_remote(
	timeout: float,
	preferred_node_id: str | None = None,
	strict: bool = True,
	project_root_filter: str | None = None,
) -> tuple[RemoteExecution, str] | None:
	remote = RemoteExecution(RemoteExecutionConfig())
	try:
		node_id = _discover_node(remote, timeout, preferred_node_id, strict, project_root_filter)
		if node_id is None:
			return None
		_connect_node(remote, node_id)
		return remote, node_id
	except RuntimeError:
		raise
	except Exception as exc:
		logging.warning("UE discovery/connect failed: %r", exc)
		try:
			remote.stop()
		except Exception:
			pass
		if strict:
			raise RuntimeError(f"UE discovery/connect failed: {exc}") from exc
		return None


def _try_heartbeat() -> bool:
	"""Returns whether the editor was discovered (a discovery error counts as not found).
	A round that yields the mutex to another connection (not free within 100ms) still counts
	as discovered, so _heartbeat_loop retries soon instead of backing off."""
	global _session_announced
	preferred = _resolve_target_node_id()
	remote = None
	try:
		remote = RemoteExecution(RemoteExecutionConfig())
		# Discover outside the mutex - holding it through up to 2 s of discovery blocks other sessions' connections
		node_id = _discover_node(
			remote,
			_EAGER_DISCOVERY_TIMEOUT,
			preferred_node_id=preferred,
			strict=False,
			project_root_filter=None if preferred else _PROJECT_ROOT_FILTER,
		)
	except Exception as exc:
		logging.debug("Heartbeat discovery failed: %r", exc)
		if remote is not None:
			try:
				remote.stop()
			except Exception:
				pass
		return False
	if node_id is None:
		if preferred is not None:
			logging.debug("Heartbeat skipped: target node '%s' not currently visible.", preferred)
		return False
	if not _acquire_ue_mutex(timeout_ms=100):
		try:
			remote.stop()
		except Exception:
			pass
		return True
	try:
		_connect_node(remote, node_id)
		try:
			active = _count_active_sessions()
			session_lines = _session_lines() if active == 1 else ""
			announce_line = (
				'print("[MCP] ue-remote-execution-bridge server connected")\n'
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
			cmd_result = remote.run_command(
				code, unattended=True, exec_mode=MODE_EXEC_FILE, raise_on_failure=False
			)
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
	return True


def _heartbeat_loop() -> None:
	# Back off to 2/4/8/16/30 s while no editor is found, instead of running UDP discovery every 2 s
	delay = _HEARTBEAT_INTERVAL
	missed = False
	while True:
		time.sleep(delay)
		if _try_heartbeat():
			delay, missed = _HEARTBEAT_INTERVAL, False
		elif missed:
			delay = min(delay * 2, _HEARTBEAT_MAX_INTERVAL)
		else:
			missed = True


threading.Thread(target=_heartbeat_loop, daemon=True).start()

# ---------------------------------------------------------------------------
# Connection context manager — per-call fresh discovery
# ---------------------------------------------------------------------------


def _assert_workspace(remote: RemoteExecution, then_code: str) -> None:
	"""Abort if the connected editor's live project_dir != this workspace.
	Last line of defense — the discovery filter trusts the PONG; this re-checks
	against the editor's actual unreal.Paths so a stale/wrong PONG can't slip through.
	then_code rides in the same command and runs only after the check passes (one round trip)."""
	guard = (
		"import unreal, os\n"
		"_p = unreal.Paths.convert_relative_path_to_full(unreal.Paths.project_dir())\n"
		f"assert os.path.normcase(os.path.normpath(_p)) == {json.dumps(_PROJECT_ROOT_FILTER)}, "
		"'WORKSPACE_MISMATCH:' + _p\n"
	)
	res = remote.run_command(guard + then_code, unattended=True, exec_mode=MODE_EXEC_FILE, raise_on_failure=False)
	if not res.get("success"):
		raise RuntimeError(
			f"Workspace guard failed — connected editor is not this workspace "
			f"(expected {_PROJECT_ROOT_FILTER}). {res.get('result') or ''}"
		)


def _session_lines() -> str:
	return (
		f"    unreal.RemoteExecutionBridgeLibrary.set_connected_pid({_PID})\n"
		f"    unreal.RemoteExecutionBridgeLibrary.set_connected_ppid({_PPID})\n"
		f"    unreal.RemoteExecutionBridgeLibrary.set_connected_cwd({json.dumps(_CWD)})\n"
		f'    unreal.RemoteExecutionBridgeLibrary.set_connected_start_time("{_START_TIME}")\n'
		f"    unreal.RemoteExecutionBridgeLibrary.set_connected_branch_name({json.dumps(_GIT_BRANCH)})\n"
	)


def _set_connected_code(node_id: str) -> str:
	return (
		"import unreal\n"
		"try:\n"
		f"    unreal.RemoteExecutionBridgeLibrary.set_connected_node_id({json.dumps(node_id)})\n"
		+ (_session_lines() if _count_active_sessions() == 1 else "")
		# A poll releases the mutex for only 0.2 s per segment, so the heartbeat (which waits 100 ms) rarely
		# gets in - refresh the badge timestamp on every connection
		+ "    unreal.RemoteExecutionBridgeLibrary.heartbeat()\n"
		# This rides in the same command as the workspace guard; a display-update failure must not surface as
		# a guard failure, so ignore it as when it was sent on its own
		+ "except Exception:\n"
		"    pass\n"
	)


@contextmanager
def _ue_connection() -> Generator[RemoteExecution, None, None]:
	"""Acquire cross-process mutex and open a fresh UE command connection.

	Raises TimeoutError if another session holds the mutex longer than
	_MUTEX_ACQUIRE_TIMEOUT_MS. Connection is closed when the context exits.
	"""
	global _SELECTED_NODE_ID
	if not _acquire_ue_mutex(_MUTEX_ACQUIRE_TIMEOUT_MS):
		raise TimeoutError("UE connection held by another session. Retry shortly.")
	try:
		preferred = _resolve_target_node_id()
		# No explicit pin → constrain discovery to this workspace's editor (project_root match).
		# A vanished pin may be re-pinned (editor restart issues a new node_id) only if the pinned editor
		# was advertising this workspace's project_root; a deliberate cross-workspace pin stays strict.
		with _SELECTED_NODE_LOCK:
			pin_root = _SELECTED_NODE_ROOT if _SELECTED_NODE_ID is not None else None
		can_repin = preferred is not None and _PROJECT_ROOT_FILTER is not None and pin_root == _PROJECT_ROOT_FILTER
		# When re-pinning is allowed use non-strict - the pin lookup must be able to return None so the fallback below runs
		result = _open_remote(
			_DISCOVERY_TIMEOUT,
			preferred_node_id=preferred,
			strict=not can_repin,
			project_root_filter=None if preferred else _PROJECT_ROOT_FILTER,
		)
		if result is None and can_repin:
			# The pinned node is gone. Look again among this workspace's editors only
			# (keep the filter so we do not leak into another workspace) and update the pin to the new id.
			result = _open_remote(
				_DISCOVERY_TIMEOUT,
				preferred_node_id=None,
				strict=True,
				project_root_filter=_PROJECT_ROOT_FILTER,
			)
			if result is not None:
				with _SELECTED_NODE_LOCK:
					if _SELECTED_NODE_ID is not None:
						_SELECTED_NODE_ID = result[1]
				logging.warning("Pinned node %s vanished (editor restart?) — re-pinned to %s", preferred, result[1])
				# Treat the pin state as cleared so _assert_workspace below still runs.
				preferred = None
		if result is None:
			raise RuntimeError(
				f"No matching Unreal Editor for this workspace within {_DISCOVERY_TIMEOUT}s. "
				"Verify the editor is running (PythonScriptPlugin + bRemoteExecution=True) and "
				"multicast 239.0.0.1:6766 reachable, or pin one with select_ue_node()."
			)
		remote, node_id = result
		try:
			# Defense-in-depth: confirm the connected editor's live project really is this
			# workspace before any command runs — survives a stale/incorrect discovery PONG.
			# Skipped when an explicit pin is set (user deliberately chose that editor).
			if _PROJECT_ROOT_FILTER and preferred is None:
				_assert_workspace(remote, _set_connected_code(node_id))
			else:
				remote.run_command(
					_set_connected_code(node_id),
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

# ---------------------------------------------------------------------------
# Core run helper
# ---------------------------------------------------------------------------


def _run(code: str, mode: str, unattended: bool) -> dict[str, Any]:
	try:
		with _ue_connection() as remote:
			result = remote.run_command(
				code, unattended=unattended, exec_mode=mode, raise_on_failure=False
			)
	except (TimeoutError, RuntimeError, OSError) as e:
		return {"success": False, "result": None, "stdout": "", "stderr": str(e), "raw_output": []}
	return _command_result(result)


def _command_result(result: dict[str, Any]) -> dict[str, Any]:
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

# ---------------------------------------------------------------------------
# Usage logging
# ---------------------------------------------------------------------------


def _log_usage(code: str) -> None:
	try:
		with _usage_lock, open(USAGE_LOG_PATH, "a", encoding="utf-8") as f:
			f.write(f"---\n{datetime.now(timezone.utc).isoformat()}\n{code}\n")
	except Exception:
		pass


_API_RE = re.compile(r"unreal\.([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)")
# An entry starts with a `---` line immediately followed by an ISO timestamp line, so a `---` line inside logged code does not split entries.
_ENTRY_SEP_RE = re.compile(r"^---[ \t]*\n(?=\d{4}-\d{2}-\d{2}T\d)", re.MULTILINE)
_ISO_DATE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})T")
_ISO_DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_PERIOD_LINE_RE = re.compile(r"^(\S+(?:[ \t]+\S+)*): (\S+) ~ \S+ \((\d+) entries\)$", re.MULTILINE)


def _parse_usage_log(text: str) -> tuple[int, dict[str, dict[str, Any]], str, str]:
	"""Parse usage.log text → (total_entries, {api: {'count': int, 'last_seen': 'YYYY-MM-DD'}}, min_date, max_date)."""
	counts: dict[str, int] = {}
	last_seen: dict[str, str] = {}
	total = 0
	min_date = ""
	max_date = ""
	text = text.replace("\r\n", "\n")
	for entry in _ENTRY_SEP_RE.split(text):
		entry = entry.strip()
		if not entry:
			continue
		head, _, body = entry.partition("\n")
		date_match = _ISO_DATE_RE.match(head)
		if not date_match:
			continue
		date = date_match.group(1)
		total += 1
		if not min_date or date < min_date:
			min_date = date
		if date > max_date:
			max_date = date
		seen: set[str] = set()
		for m in _API_RE.finditer(body):
			seen.add("unreal." + m.group(1))
		for api in seen:
			counts[api] = counts.get(api, 0) + 1
			if api not in last_seen or last_seen[api] < date:
				last_seen[api] = date
	delta = {api: {"count": c, "last_seen": last_seen[api]} for api, c in counts.items()}
	return total, delta, min_date, max_date


def _parse_cheatsheet_table(text: str) -> tuple[list[str], list[str], list[dict[str, Any]], list[str]]:
	"""Split CHEATSHEET.md into (pre_lines, header_lines, rows, post_lines).

	header_lines is the original `| API | ... |` header + `|---|...|` separator
	(2 lines). rows[i] keys: api_key, api_md, count, last_seen, note.
	"""
	lines = text.split("\n")
	start: int | None = None
	for i, l in enumerate(lines):
		if l.startswith("| API "):
			start = i
			break
	if start is None:
		raise ValueError("CHEATSHEET.md table header (| API ...) not found.")
	header_lines = lines[start:start + 2]
	end = start + 2
	rows: list[dict[str, Any]] = []
	while end < len(lines):
		l = lines[end].strip()
		if not l.startswith("|"):
			break
		parts = [p.strip() for p in l.strip("|").split("|")]
		if len(parts) < 4:
			break
		api_md, count_str, last_seen, note = parts[0], parts[1], parts[2], parts[3]
		api_key = api_md.strip("`")
		rows.append({
			"api_md": api_md,
			"api_key": api_key,
			"count": int(count_str) if count_str else 0,
			"last_seen": last_seen,
			"note": note,
		})
		end += 1
	return lines[:start], header_lines, rows, lines[end:]


def _format_table(header_lines: list[str], rows: list[dict[str, Any]]) -> list[str]:
	out = list(header_lines)
	for r in rows:
		cnt = str(r["count"]) if r["count"] > 0 else ""
		out.append(f"| {r['api_md']} | {cnt} | {r['last_seen']} | {r['note']} |")
	return out


def _update_cheatsheet(dry_run: bool = False) -> dict[str, Any]:
	"""Merge usage.log delta into CHEATSHEET.md and truncate the log.

	When dry_run=True, returns the planned delta without modifying any files.
	"""
	if not USAGE_LOG_PATH.exists():
		return {"status": "no-op", "message": "usage.log does not exist."}
	with _usage_lock:
		log_text = USAGE_LOG_PATH.read_text(encoding="utf-8")
	total, delta, min_log_date, max_date = _parse_usage_log(log_text)
	if total == 0:
		return {"status": "no-op", "message": "usage.log is empty.", "entries_processed": 0}

	# read_bytes (not read_text) so the file's EOL style can be detected and restored on write.
	cheat_raw = CHEATSHEET_PATH.read_bytes().decode("utf-8")
	eol = "\r\n" if "\r\n" in cheat_raw else "\n"
	cheat_text = cheat_raw.replace("\r\n", "\n")
	pre, header_lines, rows, post = _parse_cheatsheet_table(cheat_text)
	existing = {r["api_key"]: r for r in rows}

	updated_count = 0
	added_count = 0
	for api, info in delta.items():
		if api in existing:
			r = existing[api]
			r["count"] += info["count"]
			r["last_seen"] = info["last_seen"]
			updated_count += 1
		else:
			rows.append({
				"api_md": f"`{api}`",
				"api_key": api,
				"count": info["count"],
				"last_seen": info["last_seen"],
				"note": "",
			})
			added_count += 1

	rows.sort(key=lambda r: (-r["count"], r["api_key"]))

	# Update period line in `pre`. The label is preserved verbatim, so the file
	# can stay in any language while server.py contains no localized literal.
	new_total = total
	for i, l in enumerate(pre):
		m = _PERIOD_LINE_RE.search(l)
		if m:
			label, min_date, prev_total_str = m.group(1), m.group(2), m.group(3)
			# Initial state: the line holds a "-" placeholder instead of dates.
			if not _ISO_DAY_RE.match(min_date):
				min_date = min_log_date
			new_total = int(prev_total_str) + total
			pre[i] = _PERIOD_LINE_RE.sub(
				f"{label}: {min_date} ~ {max_date} ({new_total} entries)", l
			)
			break

	new_text = "\n".join(pre + _format_table(header_lines, rows) + post).replace("\n", eol)

	result: dict[str, Any] = {
		"status": "ok",
		"entries_processed": total,
		"apis_updated": updated_count,
		"apis_added": added_count,
		"total_rows": len(rows),
		"max_date": max_date,
		"cumulative_entries": new_total,
	}

	if dry_run:
		result["status"] = "dry-run"
		return result

	CHEATSHEET_PATH.write_bytes(new_text.encode("utf-8"))
	with _usage_lock:
		USAGE_LOG_PATH.write_text("", encoding="utf-8")
	return result

# ---------------------------------------------------------------------------
# Sentinel extraction — used by analysis scripts (print-based JSON output)
# ---------------------------------------------------------------------------

_JSON_BEGIN = "__MCP_JSON_BEGIN__"
_JSON_END = "__MCP_JSON_END__"


def _extract_json(output: str) -> str:
	begin = output.rfind(_JSON_BEGIN)
	end = output.rfind(_JSON_END)
	if begin == -1 or end == -1:
		raise ValueError(
			f"JSON sentinels not found in output. "
			f"Output (first 500 chars): {output[:500]}"
		)
	if end < begin:
		raise ValueError(f"END sentinel appears before BEGIN. Output: {output[:500]}")
	return output[begin + len(_JSON_BEGIN):end].strip()


def _strip_leading_comments(code: str) -> str:
	"""Remove leading comment/blank lines so UE ExecuteFile doesn't treat them as a file path."""
	lines = code.splitlines()
	for i, line in enumerate(lines):
		stripped = line.strip()
		if stripped and not stripped.startswith("#"):
			return "\n".join(lines[i:])
	return ""  # all lines are comments/blank


# Match the quotes too - templates are read from disk on every call, so editing them would break the script
# tools of sessions still running an older server.py
_PLACEHOLDER_RE = re.compile(r"(['\"]?)__([A-Z0-9_]+)__\1")


def _placeholder_literal(match: re.Match[str], params: dict[str, Any]) -> str:
	key = match[2]
	if key not in params:
		return match[0]
	value = params[key]
	# A quoted slot takes a whole string literal, so values with quotes or backslashes arrive unchanged
	if match[1]:
		return repr(value if isinstance(value, str) else str(value))
	return repr(value) if isinstance(value, str) else str(value)


def _render_script(script_name: str, **params: Any) -> str:
	# Build the code only, without connecting - lets a caller that sends over an already-open connection avoid _run_script_with_params reconnecting
	script_path = SCRIPTS_DIR / script_name
	if not script_path.exists():
		raise FileNotFoundError(f"Script not found: {script_path}")
	code = script_path.read_text(encoding="utf-8")
	# Substitute in one pass so a __X__ inside an earlier value is not replaced again by a later key
	code = _PLACEHOLDER_RE.sub(lambda m: _placeholder_literal(m, params), code)
	return _strip_leading_comments(code)


def _run_script_with_params(script_name: str, **params: Any) -> str:
	"""Load script, substitute __KEY__ placeholders, execute, return sentinel JSON."""
	code = _render_script(script_name, **params)
	return _script_json(_run(code, MODE_EXEC_FILE, unattended=True))


def _script_json(result: dict[str, Any]) -> str:
	if not result["success"]:
		raise RuntimeError(f"Script failed: {result['stderr'] or result['result']}")
	json_str = _extract_json(result["stdout"])
	json.loads(json_str)  # validate
	return json_str

# ---------------------------------------------------------------------------
# MCP Server
# ---------------------------------------------------------------------------

mcp = FastMCP("ue-remote-execution-bridge")

# --- Bridge base tools ---


@mcp.tool()
def run_python(
	code: str,
	mode: str = "exec_file",
	unattended: bool = True,
) -> dict[str, Any]:
	"""Execute arbitrary Python inside the running Unreal Editor.

	Before writing code that mutates widget trees, Blueprints or assets, see the 'Common snippets' and
	'Properties not accessible from Python' sections of docs/CHEATSHEET.md - they cover known ensure crashes and inaccessible-attribute workarounds.

	Args:
		code: Python source. Multi-statement for exec_file; single statement/expression
			for exec_statement/eval_statement.
		mode: 'exec_file' (default, multi-line), 'exec_statement', or 'eval_statement'
			(returns the evaluated repr).
		unattended: True suppresses modal dialogs.
	Returns dict with success, result (eval only), stdout, stderr, raw_output.
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


# --- Script-backed tools (scripts/*.py) ---


@mcp.tool()
def list_scripts() -> str:
	"""Return the list of available analysis scripts."""
	scripts = sorted(f.name for f in SCRIPTS_DIR.glob("*.py"))
	return json.dumps(scripts, ensure_ascii=False)


def _b64(payload: Any) -> str:
	return base64.b64encode(json.dumps(payload).encode("utf-8")).decode("ascii")


def _validate_token(value: str, label: str) -> None:
	"""Reject quotes, newlines and backslashes in token-shaped inputs
	(FKey names, action/backend enums) - none are valid in those values."""
	if any(c in value for c in ("'", '"', "\n", "\r", "\\")):
		raise ValueError(f"{label} contains unsafe characters: {value!r}")


# --- Virtual input (mouse/keyboard) tools ---


@mcp.tool()
def simulate_pointer_input(
	action: str,
	position: list[float],
	backend: str = "slate",
	button: str = "LeftMouseButton",
	to_position: list[float] | None = None,
	hold_seconds: float = 1.0,
	steps: int = 8,
) -> str:
	"""Inject mouse pointer input into PIE - Backend A (Slate) or B (Viewport) selectable.

	long_press is composed of a Down script call -> time.sleep(hold_seconds) in this server process itself ->
	an Up script call, so the UE game thread is never blocked. Coordinates are viewport-local pixels and
	both backends use the same coordinate system (callers need not change their coordinate math when switching).

	Args:
		action: 'click' | 'drag' | 'long_press'.
		position: [x, y] viewport-local pixel coordinates. Start point for drag.
		backend: 'slate' (default) | 'viewport'. Backend C (AutomationDriver) does not support
			coordinate-based mouse (measured in a spike) - use 'viewport' if needed.
		button: FKey name (default 'LeftMouseButton').
		to_position: drag destination [x, y] - required for action='drag'.
		hold_seconds: hold duration for long_press (seconds).
		steps: number of interpolation steps for drag.
	Returns:
		click/drag: JSON {"success", "handled", "message"}.
		long_press: JSON {"success", "down": {...}, "up": {...}}.
	"""
	if action not in ("click", "drag", "long_press"):
		raise ValueError(f"action must be one of click/drag/long_press, got: {action!r}")
	if backend not in ("slate", "viewport"):
		raise ValueError(f"backend must be 'slate' or 'viewport', got: {backend!r}")
	_validate_token(button, "button")

	if action == "click":
		return _run_script_with_params(
			"simulate_pointer.py",
			ACTION="click",
			BACKEND=backend,
			POSITION_B64=_b64(position),
			TO_POSITION_B64=_b64([0, 0]),
			BUTTON=button,
			STEPS=steps,
		)

	if action == "drag":
		if to_position is None:
			raise ValueError("to_position is required for action='drag'")
		return _run_script_with_params(
			"simulate_pointer.py",
			ACTION="drag",
			BACKEND=backend,
			POSITION_B64=_b64(position),
			TO_POSITION_B64=_b64(to_position),
			BUTTON=button,
			STEPS=steps,
		)

	# long_press: Down -> host sleep(hold_seconds) -> Up.
	down_json = _run_script_with_params(
		"simulate_pointer.py",
		ACTION="down",
		BACKEND=backend,
		POSITION_B64=_b64(position),
		TO_POSITION_B64=_b64([0, 0]),
		BUTTON=button,
		STEPS=steps,
	)
	time.sleep(hold_seconds)
	up_json = _run_script_with_params(
		"simulate_pointer.py",
		ACTION="up",
		BACKEND=backend,
		POSITION_B64=_b64(position),
		TO_POSITION_B64=_b64([0, 0]),
		BUTTON=button,
		STEPS=steps,
	)
	down = json.loads(down_json)
	up = json.loads(up_json)
	return json.dumps(
		{"success": bool(down.get("success")) and bool(up.get("success")), "down": down, "up": up},
		ensure_ascii=False,
	)


@mcp.tool()
def simulate_key_input(key: str, action: str, backend: str = "slate") -> str:
	"""Inject keyboard input into PIE - Backend A (Slate) or B (Viewport) selectable.

	backend='viewport' does not support action='char' (GameViewportClient::InputKey only handles raw key
	events - text typing is backend='slate' only). action='focus_viewport' explicitly moves keyboard focus to the PIE game viewport
	before injecting gameplay keys (movement, console toggle, etc.) - do not call it when
	typing into a text field (focus moves to the viewport and the field loses focus).

	Args:
		key: FKey name (e.g. 'W', 'LeftShift', 'Tilde'). Only for action='down'|'up' - for 'char', key is treated as the text string to type,
			not an FKey name (passed via base64, so arbitrary characters are allowed), and several
			characters can be sent at once (C++ InjectKeyChar iterates the whole string and calls
			ProcessKeyCharEvent per character). Ignored entirely for 'focus_viewport'.
		action: 'down' | 'up' | 'char' | 'focus_viewport'.
		backend: 'slate' (default) | 'viewport'.
	Returns:
		JSON {"success", "handled", "message"}.
	"""
	if action not in ("down", "up", "char", "focus_viewport"):
		raise ValueError(f"action must be one of down/up/char/focus_viewport, got: {action!r}")
	if backend not in ("slate", "viewport"):
		raise ValueError(f"backend must be 'slate' or 'viewport', got: {backend!r}")
	if action in ("down", "up"):
		_validate_token(key, "key")

	return _run_script_with_params(
		"simulate_key.py",
		ACTION=action,
		BACKEND=backend,
		KEY=key if action in ("down", "up") else "",
		TEXT_B64=_b64_str(key if action == "char" else ""),
	)


@mcp.tool()
def run_driver_sequence(steps: list[dict[str, Any]], timeout_seconds: float = 10.0) -> str:
	"""Run a Backend C (AutomationDriver) keyboard sequence asynchronously and wait for it to finish.

	Waits for completion with an async kickoff plus a non-blocking poll loop (host sleep interval), so the UE game thread is
	never blocked - to the caller it looks like any other synchronous tool. Must be enabled beforehand with
	enable_automation_driver() (when inactive, the kickoff returns an explicit failure -
	it does not crash). Coordinate-based mouse is not supported - use simulate_pointer_input's
	backend='viewport' instead.

	Args:
		steps: [{"action": "press", "key": "A"}, {"action": "release", "key": "A"}, ...].
			Supported actions: press({"key"}) / release({"key"}) / press_chord({"keys": [2-3 keys]}) /
			type({"text"}) / wait({"seconds"}).
		timeout_seconds: maximum wait for completion (seconds). On timeout returns done=false (the handle is still valid - can retry).
	Returns:
		JSON {"success", "handle", "done", "sequence_success", "message"}.
	"""
	if not isinstance(steps, list) or len(steps) == 0:
		raise ValueError(f"steps must be a non-empty list, got: {steps!r}")

	kickoff_json = _run_script_with_params("driver_sequence_run.py", STEPS_B64=_b64(steps))
	kickoff = json.loads(kickoff_json)
	if not kickoff.get("success") or not kickoff.get("handle"):
		return json.dumps(
			{
				"success": False,
				"handle": "",
				"done": False,
				"sequence_success": False,
				"message": kickoff.get("message") or "kickoff failed",
			},
			ensure_ascii=False,
		)

	handle = kickoff["handle"]
	poll_interval = 0.2
	poll_code = _render_script("driver_sequence_poll.py", HANDLE=handle)
	deadline = time.monotonic() + timeout_seconds
	while time.monotonic() < deadline:
		# Connecting per tick repeats discovery, connect and guard every tick; one connection to the end holds the mutex too long
		segment_end = min(time.monotonic() + _POLL_SEGMENT_SECONDS, deadline)
		with _ue_connection() as remote:
			while True:
				poll = json.loads(_script_json(_command_result(remote.run_command(
					poll_code, unattended=True, exec_mode=MODE_EXEC_FILE, raise_on_failure=False
				))))
				if poll.get("done"):
					return json.dumps(
						{
							"success": True,
							"handle": handle,
							"done": True,
							"sequence_success": bool(poll.get("done_success")),
							"message": poll.get("message", ""),
						},
						ensure_ascii=False,
					)
				if time.monotonic() + poll_interval >= segment_end:
					break
				time.sleep(poll_interval)
		# Wait between segments outside the mutex - the heartbeat, which waits only 100 ms, can take it in this gap
		time.sleep(poll_interval)

	return json.dumps(
		{
			"success": True,
			"handle": handle,
			"done": False,
			"sequence_success": False,
			"message": f"timeout after {timeout_seconds}s — handle still pollable",
		},
		ensure_ascii=False,
	)


@mcp.tool()
def enable_automation_driver(auto_disable_seconds: float = 30.0) -> str:
	"""Enable Backend C (AutomationDriver) - reference-counted; real HW input is blocked while enabled.

	After auto_disable_seconds elapses, only this Enable's share is returned automatically - Enables registered by other sessions are unaffected.

	Args:
		auto_disable_seconds: safety timeout (seconds, default 30).
	Returns:
		JSON {"success", "generation", "message"}. Remember the generation and pass it to disable_automation_driver
		(generation=...) to return exactly this Enable call's share (prevents sessions sharing the driver
		from returning each other's shares).
	"""
	return _run_script_with_params(
		"automation_driver_toggle.py", ACTION="enable", AUTO_DISABLE_SECONDS=auto_disable_seconds, GENERATION=0
	)


@mcp.tool()
def disable_automation_driver(generation: int = 0) -> str:
	"""Release one Backend C (AutomationDriver) reference - it is only really disabled once the count reaches 0.

	Args:
		generation: the generation value returned by enable_automation_driver(). If given, returns exactly that Enable call's
			share - if omitted (0), returns one arbitrary live generation (legacy compatibility; the
			reference count itself is always exact either way).
	Returns:
		JSON {"success", "message"}. success=False means there is no matching Enable, or the given generation was already
		returned or does not exist.
	"""
	return _run_script_with_params(
		"automation_driver_toggle.py", ACTION="disable", AUTO_DISABLE_SECONDS=0.0, GENERATION=generation
	)


def _b64_str(s: str) -> str:
	"""Base64-encode a plain UTF-8 string (for safe placeholder injection)."""
	return base64.b64encode(s.encode("utf-8")).decode("ascii")



@mcp.tool()
def update_cheatsheet(dry_run: bool = False) -> dict[str, Any]:
	"""Merge src/usage.log delta into docs/CHEATSHEET.md frequency table and truncate the log.

	Merge rules (matches the procedure documented in CHEATSHEET.md):
	  - Existing row + delta -> Count accumulates, Last seen overwritten
	  - New API -> new row appended (Notes left blank for later enrichment)
	  - Existing row not in delta -> unchanged
	  - Sort: Count desc, API alpha
	  - Period line: min date preserved, max date / cumulative entries refreshed

	Args:
		dry_run: When True, files are not modified — returns the planned result only.
	Returns:
		{status, entries_processed, apis_updated, apis_added, total_rows,
		 max_date, cumulative_entries}.
	"""
	return _update_cheatsheet(dry_run=dry_run)


# ---------------------------------------------------------------------------
# Node selection tools
# ---------------------------------------------------------------------------


@mcp.tool()
def list_ue_nodes(discovery_timeout: float = 15.0) -> dict[str, Any]:
	"""Discover all running Unreal Editor instances and show which one this server is targeting.

	Returns server_status (pid, ppid, selection_source, resolved_node_id) and a list of
	discovered nodes with connection metadata. Useful for diagnosing multi-editor setups
	and for the natural-language switching workflow:
	  1. Call list_ue_nodes to see available editors.
	  2. Call select_ue_node(node_id) to pin one.
	  3. All subsequent tool calls route to that editor.

	Args:
		discovery_timeout: Seconds to listen for UDP pong replies (default 15.0).
	"""
	resolved = _resolve_target_node_id()

	remote = RemoteExecution(RemoteExecutionConfig())
	nodes_raw: list[dict[str, Any]] = []
	try:
		remote.start()
		deadline = time.monotonic() + discovery_timeout
		# Early-exit when node count has been stable for 1 second
		# (cuts the 15 s wait to ~1 s for the single-editor case).
		stable_since: float | None = None
		last_count = -1
		while time.monotonic() < deadline:
			current_count = len(remote.remote_nodes)
			if current_count != last_count:
				last_count = current_count
				stable_since = time.monotonic()
			elif stable_since is not None and time.monotonic() - stable_since >= 1.0:
				break
			time.sleep(0.1)
		nodes_raw = list(remote.remote_nodes)
	except Exception:
		pass
	finally:
		try:
			remote.stop()
		except Exception:
			pass

	# Mirror _discover_node routing exactly so the reported target matches what tool calls hit.
	if resolved is not None:
		effective_resolved = resolved
		with _SELECTED_NODE_LOCK:
			selection_source = "session" if _SELECTED_NODE_ID is not None else "env"
	elif _PROJECT_ROOT_FILTER:
		want = _PROJECT_ROOT_FILTER
		matches = [n for n in nodes_raw if _norm_path(n.get("project_root", "")) == want]
		if len(matches) == 1:
			effective_resolved = matches[0]["node_id"]
			selection_source = "workspace-filter"
		elif len(matches) > 1:
			effective_resolved = matches[0]["node_id"]
			selection_source = "workspace-filter (ambiguous — multiple match)"
		else:
			effective_resolved = None
			selection_source = "unresolved (no editor matches this workspace)"
	elif len(nodes_raw) == 1:
		effective_resolved = nodes_raw[0]["node_id"]
		selection_source = "single"
	else:
		# 0 or >1 nodes, no filter/pin: no safe auto-target (tool calls would refuse).
		effective_resolved = None
		selection_source = "unresolved (set UE_PROJECT_ROOT or select_ue_node)"

	nodes = []
	for n in nodes_raw:
		nid = n["node_id"]
		nodes.append({
			"node_id": nid,
			"short_id": nid[:8],
			"user": n.get("user", ""),
			"machine": n.get("machine", ""),
			"project_name": n.get("project_name", ""),
			"project_root": n.get("project_root", ""),
			"engine_version": n.get("engine_version", ""),
			"engine_root": n.get("engine_root", ""),
			"selected_by_this_server": nid == resolved,
			"is_current_routing_target": nid == effective_resolved,
		})

	return {
		"server_status": {
			"server_pid": _PID,
			"server_ppid": _PPID,
			"server_start_time": _START_TIME,
			"git_branch": _GIT_BRANCH,
			"selection_source": selection_source,
			"selected_node_id": resolved,
			"resolved_node_id": effective_resolved,
		},
		"nodes": nodes,
	}


@mcp.tool()
def select_ue_node(node_id: str) -> dict[str, Any]:
	"""Pin this MCP server session to a specific Unreal Editor instance.

	All subsequent tool calls (run_python, etc.) will route to
	the editor with this node_id. The selection is in-memory and resets when the
	MCP server process restarts. Use list_ue_nodes to find available node_ids.

	Args:
		node_id: Full node_id string from list_ue_nodes (e.g. the UUID-like string).
	"""
	global _SELECTED_NODE_ID, _SELECTED_NODE_ROOT
	node_id = node_id.strip()
	if not node_id:
		raise ValueError("node_id must not be empty. Use list_ue_nodes to find valid IDs.")

	with _SELECTED_NODE_LOCK:
		_SELECTED_NODE_ID = node_id
		_SELECTED_NODE_ROOT = None

	# Check if the node is currently visible (warning only — allow deferred binding)
	remote = RemoteExecution(RemoteExecutionConfig())
	found_meta: dict[str, Any] | None = None
	try:
		remote.start()
		deadline = time.monotonic() + 3.0
		while time.monotonic() < deadline:
			match = next((n for n in remote.remote_nodes if n["node_id"] == node_id), None)
			if match is not None:
				found_meta = match
				break
			time.sleep(0.1)
	except Exception:
		pass
	finally:
		try:
			remote.stop()
		except Exception:
			pass

	if found_meta is not None:
		with _SELECTED_NODE_LOCK:
			if _SELECTED_NODE_ID == node_id:
				_SELECTED_NODE_ROOT = _norm_path(found_meta.get("project_root", "")) or None
		return {
			"selected": True,
			"node_id": node_id,
			"short_id": node_id[:8],
			"project_name": found_meta.get("project_name", ""),
			"project_root": found_meta.get("project_root", ""),
			"machine": found_meta.get("machine", ""),
			"message": f"Pinned to node '{node_id[:8]}…'. All UE tool calls now route to this editor.",
		}
	return {
		"selected": True,
		"node_id": node_id,
		"short_id": node_id[:8],
		"warning": "Node not currently visible — saved anyway (deferred binding). "
			"Verify with list_ue_nodes once the editor is running.",
		"message": f"Pinned to node '{node_id[:8]}…'. All UE tool calls now route to this editor.",
	}


@mcp.tool()
def clear_ue_node() -> dict[str, Any]:
	"""Clear the session node selection, reverting to env-var or workspace-filter routing.

	After this call:
	  - If UE_NODE_ID env var is set: routes to that node.
	  - Else if UE_PROJECT_ROOT is set: routes to the editor whose project_root matches
	    this workspace (refuses if none match / >1 ambiguous — no silent cross-workspace).
	  - Otherwise (no filter): routes to the single editor, or refuses if >1 are visible.
	"""
	global _SELECTED_NODE_ID, _SELECTED_NODE_ROOT
	with _SELECTED_NODE_LOCK:
		_SELECTED_NODE_ID = None
		_SELECTED_NODE_ROOT = None

	if _ENV_DEFAULT_NODE_ID is not None:
		return {
			"cleared": True,
			"selection_source": "env",
			"resolved_node_id": _ENV_DEFAULT_NODE_ID,
			"message": f"Session selection cleared. Routing via UE_NODE_ID env var: '{_ENV_DEFAULT_NODE_ID[:8]}…'.",
		}
	return {
		"cleared": True,
		"selection_source": "workspace-filter" if _PROJECT_ROOT_FILTER else "single-or-refuse",
		"resolved_node_id": None,
		"message": (
			"Session selection cleared. Routing scoped to this workspace "
			f"(UE_PROJECT_ROOT -> {_PROJECT_ROOT_FILTER})."
			if _PROJECT_ROOT_FILTER else
			"Session selection cleared. No UE_PROJECT_ROOT — routes to the single editor, "
			"or refuses if multiple are visible."
		),
	}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
	mcp.run()
