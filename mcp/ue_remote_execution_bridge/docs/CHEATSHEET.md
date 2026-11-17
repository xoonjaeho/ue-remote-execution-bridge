# UE Python API Cheatsheet

A frequency table of the `unreal.*` APIs called most often, plus common snippets. Aggregated from the `run_python` call history.

## How this file is maintained

Incremental aggregation. The frequency table rows are the accumulated state, and `src/usage.log` is the temporary delta.

1. `server.py::_log_usage` appends every `run_python` call to `src/usage.log` in the format `---\n<ISO ts>\n<code>\n`.
2. On an "aggregate usage.log" request, call the MCP tool `update_cheatsheet` — it automatically does the following:
   - Extracts `unreal.X.Y` patterns with a regex → produces a delta (per-API count + date)
   - Merges into the frequency table (existing rows: accumulate `Count`, update `Last seen` / new API: add a new row with an empty note / existing rows absent from the delta: unchanged)
   - Sorts by Count descending and updates the `Period` line (max date / cumulative entries)
   - Before merging, renames `src/usage.log` to `usage.log.<pid>.<ns>.snap.log` and deletes that snapshot once the sheet is replaced. A snapshot left by a failure is merged by the next call
3. Fill in the `Notes` column of newly added rows afterwards. If you add a meaningful one-line description, it is preserved from the next run on.
4. Calling with `dry_run=True` returns the result without modifying any file (for verification).

Pitfalls the AI found through trial and error go in a section of "Common snippets" below. The AI in the next session reads this file before writing `run_python` code so it doesn't repeat the same mistakes.

---

## Frequency table

> **Scope**: This table is the automatically aggregated history of glue APIs used in `run_python` scripts (it is not a curated or recommended list). For simple asset/data manipulation, prefer the official unreal-mcp (`AssetTools`, `DataTableTools`, etc.); use this table as a reference when writing `run_python` for work the official toolsets don't cover. The rows below are initial values, keeping only general-purpose APIs from real usage history — running `update_cheatsheet` fills in the Count.

Period: - ~ - (0 entries)

| API | Count | Last seen | Notes |
|---|---:|---|---|
| `unreal.load_asset` |  |  | Load an asset by path (most common entry) |
| `unreal.get_editor_subsystem` |  |  | Get an editor subsystem |
| `unreal.UnrealEditorSubsystem` |  |  | Editor subsystem — editor/game(PIE) world access |
| `unreal.Paths.project_dir` |  |  | Project directory path |
| `unreal.AssetRegistryHelpers.get_asset_registry` |  |  | Asset registry query |
| `unreal.load_class` |  |  | Load a UClass |
| `unreal.SystemLibrary.execute_console_command` |  |  | Execute a console command |
| `unreal.load_object` |  |  | Load an object with an explicit class |
| `unreal.Paths.convert_relative_path_to_full` |  |  | Resolve a relative path to absolute |
| `unreal.ObjectIterator` |  |  | Iterate live instances of a class in memory |
| `unreal.EditorAssetLibrary.load_asset` |  |  | Load an asset via EditorAssetLibrary |
| `unreal.UserWidget` |  |  | UMG user widget base type |
| `unreal.find_object` |  |  | Direct object ref within a package (template/CDO) |
| `unreal.GameplayStatics.get_player_controller` |  |  | Get the player controller (needs PIE world context) |
| `unreal.EditorAssetLibrary.save_asset` |  |  | Save an asset by path |
| `unreal.get_default_object` |  |  | Get a class CDO |
| `unreal.Widget` |  |  | UMG widget base type |
| `unreal.PanelWidget` |  |  | UMG panel widget (container base) |
| `unreal.BlueprintEditorLibrary.compile_blueprint` |  |  | Compile a blueprint |
| `unreal.Vector2D` |  |  | 2D vector struct |
| `unreal.WidgetLibrary.get_all_widgets_of_class` |  |  | Find live UMG widget instances by class |
| `unreal.TopLevelAssetPath` |  |  | UE5.1+ class path name (/Script/Module.Class) |
| `unreal.EditorAssetLibrary.does_asset_exist` |  |  | Check whether an asset exists |
| `unreal.AssetRegistryDependencyOptions` |  |  | Asset dependency query options struct |
| `unreal.AssetEditorSubsystem` |  |  | Asset editor subsystem (open/close asset editors) |

---

## Common snippets

> Only idioms whose call signature or return shape is non-obvious are kept. For simple APIs, the Notes column of the frequency table is enough.

### Viewport camera snapshot before PIE automation (required)

A PIE start/stop round trip alone can reset the editor viewport camera (to the origin, etc.). If you haven't saved the original coordinates, you can't recover them (and if the level is dirty, reopening it to recover is not possible either).

```python
es = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem)
camLoc, camRot = es.get_level_viewport_camera_info()   # snapshot before PIE starts
# ... (official unreal-mcp) StartPIE → work → StopPIE ...
es.set_level_viewport_camera_info(camLoc, camRot)       # restore after it ends
```

⚠ If you **save the snapshot to a file/JSON and restore it later**, watch the argument order of `unreal.Rotator`. The constructor is
`unreal.Rotator(roll, pitch, yaw)`, but the order people usually write is (pitch, yaw, roll), so passing it as-is shifts the values over by one slot.
The safest approach is to hold the `camRot` object and pass it back as it is; if serialization is needed, after restoring **always check with `get_level_viewport_camera_info()` that the values equal the original**.

### Writing WidgetBlueprint properties

The CDO (`WB_Foo_C:WidgetTree.X`) is reset on compile, so fetch the **template** (`WB_Foo:WidgetTree.X`) object with `find_object` and modify it directly.

### Editing the WidgetBlueprint tree — risky from Python alone

`WidgetTree.construct_widget` and `UWidgetBlueprintLibrary` are not exposed to Python. Calling `widget_tree.remove_widget` or `widget.rename` directly knocks `WidgetVariableNameToGuidMap` out of sync, and at compile the missing/orphan ensure in `WidgetBlueprintCompiler.cpp` fires. For tree edits, use the official unreal-mcp Widget toolset, or add a C++ UFUNCTION to the plugin that also cleans up the GUID map and call it (DESIGN.md §6).

### Deleting a WBP

Calling `delete_asset` straight on a WBP that is open or in PIE triggers the unload-failure ensure in `ObjectTools.cpp`. Safe order: end PIE → close the asset editor → GC → delete.

Limitation: for an asset that has had compiles and in-memory edits repeated within the same session, live refs such as `REINST_` classes are not reclaimed by GC and the deletion may be refused. In that case, restart the editor or delete it manually in the Content Browser.

### Writing a DataTable (caution)

Use the official unreal-mcp DataTable toolset for reads. Writes bypass the source data (CSV/spreadsheet) pipeline, so use them only for temporary patches:

```python
dt = unreal.load_asset('/Game/Data/DT_Foo')
# CSV write: the first column of the row-name header must be '---' (not a real column name). An empty return == success
problems = unreal.DataTableFunctionLibrary.fill_data_table_from_csv_string(dt, csv_str)
unreal.EditorAssetLibrary.save_asset('/Game/Data/DT_Foo')
```

### Creating a WBP instance to verify BIE / graph bodies (without PIE)

A static asset render (a tool that takes only an asset path) **can't call events** — to see whether a BIE or graph body actually runs, create an instance, call the event, and read back the resulting properties (the verdict is also more reliable than pixels).

```python
cls = unreal.EditorAssetLibrary.load_blueprint_class('/Game/UI/WB_Foo')
world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
# There is no unreal.WidgetBlueprintLibrary. UWidgetBlueprintLibrary is exposed as unreal.WidgetLibrary,
# but Create doesn't appear as a bound method, so call it via the CDO's call_method. new_object alone doesn't build the
# widget tree, so every BindWidget member is None (Initialize doesn't run).
inst = unreal.WidgetLibrary.get_default_object().call_method('Create', (world, cls, None))
img = inst.get_editor_property('Image_Active')

inst.call_method('SetDisabled', (True,))              # ← a call that runs the BP implementation
print(img.get_editor_property('color_and_opacity'))   # check the tint
```

⚠️ **The snake_case binding (`inst.set_disabled(True)`) can be a no-op** — no error, and no property changes at all (presumably it goes to the native stub rather than the BP implementation). For BIE/custom event calls, **always use `call_method('<original UFunction name>', (args,))`**. Measured: on the same instance, the snake_case call left the tint unchanged and `call_method` changed it to the expected value.

### Moving assets + cleaning up redirectors

- `EditorAssetLibrary.rename_asset` (and `AssetTools.rename_assets`) re-points *loaded* referencers inline, and leaves a redirector stub at the old path only when *unloaded* referencers remain.
- C++ `fixup_referencers` is **not exposed** to Python → to re-point a stub's remaining referencers, use `EditorAssetLibrary.consolidate_assets(new_obj, [redirector])` (refs → new_obj).

### PIE virtual input (mouse/keyboard) — three backends

For the detailed design, see [DESIGN.md §9](./DESIGN.md#9-pie-virtual-input-three-backends-adr-03). MCP tools: `simulate_pointer_input`/`simulate_key_input` (Backend A·B) and `run_driver_sequence`+`enable_automation_driver`+`disable_automation_driver` (Backend C).

**An FKey can't be created from a string argument** — both `unreal.Key("LeftMouseButton")` and `unreal.Key(key_name=...)` raise `TypeError`. Always use these 2 steps:

```python
key = unreal.Key()
key.set_editor_property('key_name', 'LeftMouseButton')
```

A WorldContextObject can be obtained with plain `run_python` alone:

```python
es = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem)
world = es.get_game_world()
pc = unreal.GameplayStatics.get_player_controller(world, 0)   # use as the world_context argument of Backend A/B functions

# Backend A (Slate) — viewport-local pixel coordinates
lib = unreal.RemoteExecutionBridgeSlateInputLibrary
lib.simulate_click(pc, unreal.Vector2D(400, 300), key)

# Backend C (AutomationDriver) — keyboard only, Enable first
generation = unreal.RemoteExecutionBridgeAutomationDriverLibrary.enable_automation_driver(30.0)
handle = unreal.RemoteExecutionBridgeAutomationDriverLibrary.run_driver_sequence(
    '[{"action":"press","key":"A"},{"action":"release","key":"A"}]')
poll = unreal.RemoteExecutionBridgeAutomationDriverLibrary.poll_driver_sequence(handle)
# poll: (bOutDone, bOutSuccess) tuple when the handle is valid / None when invalid or exhausted (the return bool is not in the tuple — measured)
unreal.RemoteExecutionBridgeAutomationDriverLibrary.disable_automation_driver(generation)
# Passing generation releases exactly this Enable call's share — omitting it (0) releases an arbitrary live generation (may release someone else's share when multiple sessions coexist)
```

⚠️ **Before calling `RunDriverSequence`, check that `enable_automation_driver` is still alive** — if you reuse it after the safety timeout (default 30 s) has elapsed, call `enable_automation_driver` again. Calling it while disabled returns an explicit failure (an empty handle).

⚠️ **Verify a click by an actual state change, not the `handled` return value** (details: [DESIGN.md §9 Backend A click reliability](./DESIGN.md#backend-a-click-reliability-the-3-preconditions-for-sbutton)). `SButton`-family widgets decide whether a click happened solely by `IsHovered()` on non-touch events, with no geometry fallback — the 3 conditions of mouse capture, Hover, and OS foreground are guaranteed automatically inside `simulate_click`/`simulate_click_on_widget`, but `handled=true` only means event routing succeeded, not that `OnClicked` ran. After a click, confirm the actual reaction through `widget.is_pressed()`/`is_hovered()` transitions or a screen capture.

⚠️ **Capture coordinates and click coordinates are different.** An editor window capture PNG is in desktop pixels, while `simulate_pointer_input` takes viewport-local pixels. Passing pixels read from a screenshot as they are will click the wrong place. Convert two points with `viewport_to_absolute_position` and solve `abs = offset + local × scale` (scale is usually the DPI scale) to work it backward — it depends on window placement and DPI, so re-measure each session.

#### AutomationDriver blocking real HW input — manual test procedure (never run automatically)

`enable_automation_driver()` intercepts the real physical mouse/keyboard — if Claude ran it automatically it would disrupt the user's real input session, so a **human performs** the procedure below:

1. Call `enable_automation_driver(auto_disable_seconds=15)`
2. Within ~10 seconds right after Enable, try physical mouse clicks/keyboard input yourself → there should be no response anywhere in PIE or the editor
3. After 15 seconds, check that physical input works normally again (confirming the safety-timeout auto-Disable)

---

## Properties not accessible from Python

Properties that can't be accessed with `get_editor_property()`. They need an alternative API or a C++ UFUNCTION (DESIGN.md §6).

| Class | Inaccessible properties | Alternative |
|---|---|---|
| `UBlueprint` | `ParentClass` | `bp.generated_class().get_super_class()` |
| `UWidgetBlueprint` | `WidgetTree`, `Animations`, `NewVariables` | Official unreal-mcp Widget toolset or a C++ UFUNCTION |
| `UWidgetAnimation` | `AnimationBindings`, `MovieScene` (property) | The MovieScene object via the `anim.get_movie_scene()` method / bindings via a C++ UFUNCTION |
| `UEdGraph` / `UEdGraphNode` / `UEdGraphPin` | All (not BlueprintType) | Official unreal-mcp Blueprint toolset or a C++ UFUNCTION |
| `UMovieScene` | `MasterTracks`, `ObjectBindings` | `GetTracks()` / `GetBindings()` methods (renamed in 5.7) |
| `UPanelSlot` | `Parent`, `Content` | The parent **type** is determined by the child's slot class (if it's `VerticalBoxSlot`, the parent is a VerticalBox) / parent **identity** is cross-checked from the parent side with `panel.get_all_children()` |

`NewVariables` and `ImplementedInterfaces` are accessible only on some subclasses such as AnimBP — wrap their use in `try/except`.

---

## References

- [UE Python API](https://dev.epicgames.com/documentation/en-us/unreal-engine/python-api/)
- Architecture and decisions: [DESIGN.md](./DESIGN.md)
