# find_external_event_callers.py  (READ-ONLY - changes nothing)
# When replacing a Custom Event with a BlueprintImplementableEvent, detects places where other assets (cross-asset)
# referencing the target WBP call that event directly from outside. If the function lacks BlueprintCallable,
# those call sites get a hard compile error right after the replacement (root cause is the C++ declaration - cannot be fixed by script).
#
# This script only detects and warns. No automatic fix.
#
# Placeholders:
#   TARGET_WBP        : '/Game/UI/WB_Foo' - the WBP whose events are (were) replaced
#   FUNCTION_NAMES_B64: base64(JSON list) - event names to check (names replaced by the replace step).
#                       Python cannot get a CallFunction node's function name directly, so the names are taken as input
#                       to search in reverse via find_nodes_by_function_name.
#                       (base64 to avoid quote corruption - same convention as edit_widget_tree etc.)
#
# Limitations (also stated in the report):
#   - K2Node_CreateDelegate ("Create Event") references are not caught by find_nodes_by_function_name (it scans CallFunction only).
#     Behavior is fine since resolution is name-first, but they are missing from the count.
#   - String-based calls such as CallFunctionByNameWithArguments cannot be detected in principle.
#   - Calls through a reference typed as a child class of the target class may be missed because the class name does not match the target.
#   - For self-context (target class None) calls, Python cannot verify across multiple levels whether the caller is a child of the target,
#     so they are reported separately as 'possible' (manual check needed).
import base64
import json
import unreal

TARGET_WBP = '__TARGET_WBP__'
FUNCTION_NAMES = json.loads(base64.b64decode('__FUNCTION_NAMES_B64__').decode('utf-8'))

L = unreal.LMBlueprintEditorUtilityLibrary
GS = unreal.LMGraphSerializeLibrary
ar = unreal.AssetRegistryHelpers.get_asset_registry()

result = {
    'target_wbp': TARGET_WBP,
    'target_class': None,
    'parent_class': None,
    'risky_functions': [],     # BIE without BlueprintCallable -> compile error risk on external calls
    'safe_functions': [],      # has BlueprintCallable -> external calls are safe (informational)
    'confirmed_callers': [],   # external call sites (target class name matches) - high confidence
    'possible_callers': [],    # self-context call sites - manually check whether the caller is a child of the target
    'candidates_scanned': 0,
    'limitations': [
        'CreateDelegate("Create Event") references and string-based calls (CallFunctionByNameWithArguments etc.) cannot be detected',
        'self-context calls cannot be checked across multi-level inheritance - separated into possible_callers (manual check)',
    ],
    'message': '',
}


def resolve_parent_class(wb):
    wdata = json.loads(GS.serialize_widget_blueprint(wb))
    pname = wdata.get('parent_class')
    t = getattr(unreal, pname, None) if pname else None
    return (t.static_class() if t else None), pname


try:
    if not FUNCTION_NAMES:
        result['message'] = 'FUNCTION_NAMES is empty - pass the event names to check (use the result of the replace step)'
        raise RuntimeError(result['message'])

    target_wb = unreal.load_asset(TARGET_WBP)
    if target_wb is None:
        result['message'] = f'failed to load target WBP: {TARGET_WBP}'
        raise RuntimeError(result['message'])

    target_cls = target_wb.generated_class()
    result['target_class'] = target_cls.get_name() if target_cls else None
    parent_cls, parent_name = resolve_parent_class(target_wb)
    result['parent_class'] = parent_name

    # Classify each function by risk (based on the parent native class)
    for fn in FUNCTION_NAMES:
        name = unreal.Name(fn)
        is_bie = (parent_cls is not None) and L.is_blueprint_implementable_event(parent_cls, name)
        callable_ok = (parent_cls is not None) and L.has_blueprint_callable_flag(parent_cls, name)
        if is_bie and not callable_ok:
            result['risky_functions'].append(fn)
        elif callable_ok:
            result['safe_functions'].append(fn)
        # if it is neither BIE nor callable it was never a replacement target - ignore

    if not result['risky_functions']:
        result['message'] = ('no risky functions - every checked event either has BlueprintCallable or is not a BIE. '
                             'no compile error risk even if cross-asset call sites exist')
        raise SystemExit  # normal early exit (caught in except, which prints the report)

    # Narrow to candidate assets that reference the target WBP (including inheriting child BPs - hard references)
    pkg_name = TARGET_WBP.rsplit('.', 1)[0]  # strip the class suffix if it is an ObjectPath
    # The default options do not include hard package references and miss call sites (false negative). Set explicitly.
    dep_opt = unreal.AssetRegistryDependencyOptions()
    dep_opt.include_hard_package_references = True
    dep_opt.include_game_package_references = True
    dep_opt.include_editor_only_package_references = True
    refs = ar.get_referencers(unreal.Name(pkg_name), dep_opt)
    ref_paths = [str(r) for r in (refs or [])]
    target_cls_name = result['target_class']

    for rp in ref_paths:
        result['candidates_scanned'] += 1
        cand = unreal.load_asset(rp)
        if cand is None:
            continue
        # Blueprint types only (they hold graphs). Blueprints other than WidgetBlueprint are included.
        try:
            bp = cand if isinstance(cand, unreal.Blueprint) else None
        except Exception:
            bp = None
        if bp is None:
            continue

        for fn in result['risky_functions']:
            call_nodes = L.find_nodes_by_function_name(bp, fn)
            for node in (call_nodes or []):
                tgt = L.get_call_function_target_class(node)
                if tgt is not None:
                    # external call - high confidence when the target class name matches
                    if tgt.get_name() == target_cls_name:
                        result['confirmed_callers'].append({'asset': rp, 'function': fn,
                                                            'target_class': tgt.get_name()})
                else:
                    # self-context - manually check whether the caller is a child of the target
                    result['possible_callers'].append({'asset': rp, 'function': fn,
                                                       'note': 'self-context - check whether the caller is a child of the target WBP'})

    n_conf = len(result['confirmed_callers'])
    n_poss = len(result['possible_callers'])
    if n_conf == 0 and n_poss == 0:
        result['message'] = (f"{len(result['risky_functions'])} risky function(s), but no external call sites were found "
                             f"among {result['candidates_scanned']} referenced assets - replacement is safe (within detection limits)")
    else:
        result['message'] = (f"⚠️ External call sites found - confirmed {n_conf}, possible {n_poss}. "
                             f"Root cause: the function lacks BlueprintCallable. Recommendation: consider adding "
                             f"BlueprintCallable to the C++ declaration (cannot be fixed automatically by script)")

except SystemExit:
    pass
except Exception as e:
    if not result['message']:
        result['message'] = f'{type(e).__name__}: {e}'

print('__MCP_JSON_BEGIN__')
print(json.dumps(result, ensure_ascii=False))
print('__MCP_JSON_END__')
