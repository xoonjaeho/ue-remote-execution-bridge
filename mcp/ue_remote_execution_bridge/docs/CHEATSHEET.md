# UE Python API Cheatsheet

Frequently-called `unreal.*` APIs used via the `run_python` MCP tool, with signatures, examples, and usage counts.

## How this file is maintained

Incremental aggregation. The Frequency table rows are the persistent state; `usage.log` is an ephemeral delta.

1. `server.py::run_python` appends every invocation to `mcp/ue_remote_execution_bridge/usage.log` (timestamp + raw code).
2. When asked (e.g. "update CHEATSHEET from usage.log"), Claude extracts `unreal.X.Y` patterns via regex/AST and produces a delta (per-API count + last-seen).
3. Merge rules:
   - Existing row + delta → `Count` += delta count; `Last seen` ← delta date (overwrite).
   - New API in delta → append new row.
   - Row not present in delta → keep unchanged.
4. Truncate `usage.log` to zero bytes after the merge is committed. The delta is now folded into the table; the raw log is no longer needed.

Internal `start_pie` / `stop_pie` / `tail_output_log` snippets bypass logging (they call `_run` directly), so counts reflect user-driven usage only.

## Frequency table

_Last aggregated: never. Empty template — populate by using the server and asking Claude to "fold usage.log into CHEATSHEET"._

| API | Count | Last seen | Notes |
|---|---:|---|---|
| _empty_ | — | — | Run a few `run_python` calls, then ask Claude to update this table from `usage.log`. |

## Common snippets

Hand-curated, grown from observed usage. Snippets accumulate as you use the server.

### Minimal run_python example

```python
import unreal

# List all assets under a given path
assets = unreal.EditorAssetLibrary.list_assets("/Game/", recursive=True, include_folder=False)
for path in assets[:10]:
    print(path)
```
