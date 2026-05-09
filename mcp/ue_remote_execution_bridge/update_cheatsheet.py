"""Fold usage.log API frequencies into docs/CHEATSHEET.md.

This script updates only the CHEATSHEET frequency table. Snippet curation stays
manual because examples need human judgment.
"""
from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

# Ensure em-dashes in the dry-run table print on Windows code pages (cp949 etc.).
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


API_RE = re.compile(r"\bunreal(?:\.[A-Za-z_][A-Za-z0-9_]*)+")
ENTRY_RE = re.compile(
    r"(?ms)^---\s*\n"
    r"(?P<timestamp>\d{4}-\d{2}-\d{2}T[^\n]+)\n"
    r"(?P<code>.*?)(?=^---\s*$|\Z)"
)
TABLE_RE = re.compile(
    r"(?ms)(## Frequency table\s*\n\n)"
    r"(?P<preamble>.*?\n\n)?"
    r"\| API \| Count \| Last seen \| Notes \|\n"
    r"\|---\|---:\|---\|---\|\n"
    r"(?P<rows>.*?)(?=\n## |\Z)"
)
LAST_AGGREGATED_RE = re.compile(r"_Last aggregated: (?P<date>\d{4}-\d{2}-\d{2})\..*?_")
ROW_RE = re.compile(r"^\| (?P<api>`[^`]+`|_empty_) \| (?P<count>[^|]+) \| (?P<last>[^|]+) \| (?P<notes>.*) \|$")


@dataclass
class Row:
    count: int
    last_seen: str
    notes: str


def parse_args() -> argparse.Namespace:
    here = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--usage-log", type=Path, default=here / "usage.log")
    parser.add_argument("--cheatsheet", type=Path, default=here / "docs" / "CHEATSHEET.md")
    parser.add_argument("--dry-run", action="store_true", help="Print the new table without writing files.")
    parser.add_argument(
        "--truncate-usage",
        action="store_true",
        help="Truncate usage.log after a successful write. Never used with --dry-run.",
    )
    return parser.parse_args()


def parse_usage(path: Path, since: str | None = None) -> tuple[Counter[str], dict[str, str]]:
    """Aggregate API counts from usage.log.

    *since* is the prior `_Last aggregated` date (ISO `YYYY-MM-DD`). Entries
    dated on or before this are skipped so re-running the script without
    `--truncate-usage` is idempotent at day granularity.
    """
    counts: Counter[str] = Counter()
    last_seen: dict[str, str] = {}
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    for match in ENTRY_RE.finditer(text):
        timestamp = match.group("timestamp")
        try:
            date = datetime.fromisoformat(timestamp).date().isoformat()
        except ValueError:
            date = timestamp[:10]
        if since is not None and date <= since:
            continue
        for api in API_RE.findall(match.group("code")):
            counts[api] += 1
            last_seen[api] = max(last_seen.get(api, ""), date)
    return counts, last_seen


def parse_rows(rows_text: str) -> dict[str, Row]:
    rows: dict[str, Row] = {}
    for line in rows_text.splitlines():
        match = ROW_RE.match(line.strip())
        if not match:
            continue
        api_cell = match.group("api")
        if api_cell == "_empty_":
            continue
        api = api_cell.strip("`")
        count_text = match.group("count").strip()
        try:
            count = int(count_text)
        except ValueError:
            continue
        rows[api] = Row(
            count=count,
            last_seen=match.group("last").strip(),
            notes=match.group("notes").strip(),
        )
    return rows


def format_table(rows: dict[str, Row]) -> str:
    lines = [
        "| API | Count | Last seen | Notes |",
        "|---|---:|---|---|",
    ]
    if not rows:
        lines.append("| _empty_ | — | — | — |")
    else:
        for api, row in sorted(rows.items(), key=lambda item: (-item[1].count, item[0].lower())):
            lines.append(f"| `{api}` | {row.count} | {row.last_seen} | {row.notes} |")
    return "\n".join(lines)


def merge(cheatsheet: str, counts: Counter[str], last_seen: dict[str, str]) -> tuple[str, str]:
    match = TABLE_RE.search(cheatsheet)
    if not match:
        raise SystemExit("Could not find the CHEATSHEET frequency table.")

    rows = parse_rows(match.group("rows"))
    for api, count in counts.items():
        current = rows.get(api, Row(count=0, last_seen="", notes=""))
        # Treat placeholder dashes as empty so they never beat a real ISO date
        # under str.max (em-dash codepoint > digits).
        prior = current.last_seen if current.last_seen.startswith("20") else ""
        rows[api] = Row(
            count=current.count + count,
            # Take the later of the two so an old usage.log replay can never
            # regress an existing row's last_seen.
            last_seen=max(prior, last_seen[api]),
            notes=current.notes,
        )

    now = datetime.now(timezone.utc).date().isoformat()
    table = format_table(rows)
    has_data = bool(rows) or bool(counts)
    if has_data:
        # Once any data exists, anchor the preamble to the canonical
        # _Last aggregated marker so subsequent runs can read it back via
        # parse_last_aggregated() and skip already-counted entries.
        preamble = f"_Last aggregated: {now}. Generated by `update_cheatsheet.py`._\n\n"
    else:
        preamble = match.group("preamble") or ""
    replacement = f"{match.group(1)}{preamble}{table}\n"
    updated = cheatsheet[: match.start()] + replacement + cheatsheet[match.end() :]
    return updated, table


def parse_last_aggregated(cheatsheet: str) -> str | None:
    match = LAST_AGGREGATED_RE.search(cheatsheet)
    return match.group("date") if match else None


def main() -> int:
    args = parse_args()
    cheatsheet = args.cheatsheet.read_text(encoding="utf-8")
    since = parse_last_aggregated(cheatsheet)
    counts, last_seen = parse_usage(args.usage_log, since=since)
    updated, table = merge(cheatsheet, counts, last_seen)

    if args.dry_run:
        print(table)
        print(f"\nDelta APIs: {len(counts)}")
        print(f"Delta calls: {sum(counts.values())}")
        return 0

    args.cheatsheet.write_text(updated, encoding="utf-8", newline="\n")
    if args.truncate_usage:
        args.usage_log.write_text("", encoding="utf-8")
    print(f"Updated {args.cheatsheet}")
    print(f"Delta APIs: {len(counts)}")
    print(f"Delta calls: {sum(counts.values())}")
    if args.truncate_usage:
        print(f"Truncated {args.usage_log}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
