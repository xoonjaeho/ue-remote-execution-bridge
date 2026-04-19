# TODO

## Rules

1. **Start** — Change Status to `in-progress` in the Tasks table and add a row to the **In Progress** section.
2. **Complete** — Remove the row from In Progress and move it to the **Completed** section with the completion date (YYYY-MM-DD). Change Status to `done` in the Tasks table.
3. **Hold** — Remove the row from In Progress or Tasks and move it to the **On Hold** section with a reason. Change Status to `hold` in the Tasks table.
4. **ID (#)** — Assign a unique number per task. IDs are never changed after completion or hold.
5. **WIP Limit** — If In Progress exceeds 3 items, re-evaluate priorities and move Low items to On Hold.

---

## Classification Rules

### Priority
| Level | Label | Criteria |
|-------|-------|----------|
| 1 | `🔴 Critical` | Service outage, data loss, security vulnerability — handle immediately |
| 2 | `🟠 High` | Core feature broken, release blocker — handle same or next day |
| 3 | `🟡 Medium` | Feature improvement, non-critical bug — handle within this week |
| 4 | `🟢 Low` | Cosmetic, refactoring, documentation — handle when available |

### Difficulty
| Level | Label | Criteria |
|-------|-------|----------|
| 1 | ⭐ Easy | Clear solution, completable within 1–2 hours |
| 2 | ⭐⭐ Medium | Some investigation needed, half a day to 1 day |
| 3 | ⭐⭐⭐ Hard | Complex design or analysis required, more than 1 day |

### Type
| Tag | Description |
|-----|-------------|
| `new` | New feature |
| `bug` | Bug fix |
| `refactor` | Code improvement (no behavior change) |
| `perf` | Performance optimization |
| `test` | Add or update tests |
| `docs` | Documentation |
| `chore` | Build, config, dependencies, etc. |

---

## Tasks

| # | Title | Type | Priority | Difficulty | Status | Notes |
|---|-------|------|----------|------------|--------|-------|
| - | | | | | | |

> **Status values**: `todo` / `in-progress` / `done` / `hold`

---

## In Progress

| # | Title | Type | Priority | Difficulty | Started | Notes |
|---|-------|------|----------|------------|---------|-------|
| - | | | | | | |

---

## Completed

| # | Title | Type | Priority | Difficulty | Completed | Notes |
|---|-------|------|----------|------------|-----------|-------|
| - | | | | | | |

---

## On Hold

| # | Title | Type | Priority | Difficulty | Reason |
|---|-------|------|----------|------------|--------|
| - | | | | | |
