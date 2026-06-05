"""Pure workspace-to-editor matching policy.

No I/O and no import side effects, so the selection rules are unit-testable in
isolation (server.py owns the discovery/command-connection plumbing and injects a
*probe* callback). See docs/DESIGN.md for the connection-selection ADR.
"""
from __future__ import annotations

import ast
from pathlib import Path
from typing import Callable


def uproject_stem(uproject_path: str) -> str:
    """The .uproject filename stem, e.g. '.../uereb/uereb.uproject' -> 'uereb'."""
    return Path(uproject_path).stem


def stem_eq(a: str | None, b: str | None) -> bool:
    """Case-insensitive project-stem comparison (Windows project names)."""
    return bool(a) and bool(b) and a.casefold() == b.casefold()


def parse_project_path(raw: object) -> str | None:
    """Normalize the EvaluateStatement result of `unreal.Paths.get_project_file_path()`.

    Remote Execution returns the *repr* of the evaluated expression (a quoted
    string). Returns the bare path, or None when the result is empty/unreadable —
    an *inconclusive* probe, which the caller must NOT treat as "wrong project".
    """
    if not raw:
        return None
    value = str(raw).strip()
    try:
        parsed = ast.literal_eval(value)
        if isinstance(parsed, str):
            value = parsed
    except (ValueError, SyntaxError):
        pass
    value = value.strip()
    return value or None


def decide_node(
    node_ids: list[str],
    project_stem: str,
    probe: Callable[[str], str | None],
) -> tuple[str | None, str]:
    """Pick the node whose project matches *project_stem*.

    *probe* maps a node_id to that editor's .uproject path, or None when the probe
    is inconclusive (eval failed / editor not ready). A None probe is never counted
    as a mismatch — it must not convert a still-initializing correct editor into a
    refusal. Returns (matched_id_or_None, status), status one of:
    "match", "ambiguous", "inconclusive", "no_match".
    """
    matches: list[str] = []
    inconclusive = False
    for node_id in node_ids:
        try:
            path = probe(node_id)
        except Exception:
            path = None  # a raising probe (e.g. connect-back failure) is inconclusive
        if path is None:
            inconclusive = True
            continue
        if stem_eq(uproject_stem(path), project_stem):
            matches.append(node_id)
    if len(matches) == 1:
        return matches[0], "match"
    if len(matches) >= 2:
        return None, "ambiguous"
    if inconclusive:
        return None, "inconclusive"
    return None, "no_match"
