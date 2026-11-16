# replace_custom_event_with_bie.py
# Within a single WBP, replaces a Custom Event with the override event node of a same-named BlueprintImplementableEvent.
# Wiring (including struct-split sub-pins) is carried over as is, and everything is rolled back on failure.
# The cross-asset external-caller risk (missing BlueprintCallable) is only warned about - cannot be fixed automatically (the cause is the C++ declaration).
#
# Placeholders (substitute, then run via run_python exec_file):
#   WBP_PATH: '/Game/UI/WB_Foo'
#   DRY_RUN : if True, only reports the replacement plan without changing anything (mutating path not executed)
#   SAVE    : save on success (ignored when DRY_RUN)
#
# Precondition: the target WBP's immediate parent must be a native class for ParentClass to resolve
#       (Python does not expose Blueprint.ParentClass - the parent name is restored via unreal.<Name>.static_class()).
#       With a BP intermediate parent, resolution fails and all events are skipped (safe, with a warning).
import json
import unreal

WBP_PATH = '__WBP_PATH__'
DRY_RUN = __DRY_RUN__
SAVE = __SAVE__

L = unreal.LMBlueprintEditorUtilityLibrary
GS = unreal.LMGraphSerializeLibrary
WT = unreal.LMWidgetTreeEditorUtilityLibrary

result = {
    'success': False,
    'wbp_path': WBP_PATH,
    'dry_run': bool(DRY_RUN),
    'parent_class': None,
    'parent_resolved': False,
    'replaced': [],        # successfully replaced
    'skipped': [],         # normal skips, e.g. not a BIE (does not cause compile errors)
    'rolled_back': [],     # rolled back on signature mismatch or rewiring failure (old node remains)
    'warnings': [],
    'compiled': False,
    'saved': False,
    'has_name_collision': False,  # if rolled_back is non-empty, the old Custom Event name-collides with the native BIE and the Blueprint is in a compile-error state
    'message': '',
}


def pin_key(p):
    return (str(p.name), str(p.direction))


def pin_sig(p):
    # Type identity beyond name+direction - include the full path (sub_category_object_path) instead of the short name (avoids false matches)
    return (str(p.category), str(p.sub_category), str(p.sub_category_object_path),
            bool(p.is_reference), bool(p.is_array), bool(p.is_set), bool(p.is_map))


def resolve_parent_class(wb):
    # Python does not expose Blueprint.ParentClass -> restore the parent_class name from serialize_widget_blueprint via
    # unreal.<Name>.static_class(). Only a native parent resolves (a BP intermediate parent gives None).
    wdata = json.loads(GS.serialize_widget_blueprint(wb))
    pname = wdata.get('parent_class')
    t = getattr(unreal, pname, None) if pname else None
    return (t.static_class() if t else None), pname


def find_existing_override(wb, name_s):
    # Already-existing same-named override event node (K2Node_Event) - tells new from existing before add
    for g in L.get_blueprint_graphs(wb):
        for n in L.get_graph_nodes(g):
            if n.get_class().get_name() == 'K2Node_Event':
                if str(L.get_event_node_function_name(n)) == name_s:
                    return n
    return None


def split_reconstruct(new_node, old_pins):
    # For each split parent pin of old_node, split the same-named pin on new_node to reproduce the sub-pin structure.
    # can_split_pin already filters out pins that are already split, hidden, or not splittable.
    # Sub-pin names are not hard-coded; they are compared by re-querying get_node_pin_infos afterwards.
    for p in old_pins:
        if p.has_split_children and not str(p.parent_pin_name):
            pin_name = str(p.name)
            if L.can_split_pin(new_node, pin_name):
                L.split_node_pin(new_node, pin_name)


def signatures_match(old_node, new_node):
    old_map = {pin_key(p): p for p in L.get_node_pin_infos(old_node)}
    new_map = {pin_key(p): p for p in L.get_node_pin_infos(new_node)}
    if set(old_map) != set(new_map):
        return False, {'missing_in_new': [list(k) for k in sorted(set(old_map) - set(new_map))],
                       'extra_in_new': [list(k) for k in sorted(set(new_map) - set(old_map))]}
    for k in old_map:
        if pin_sig(old_map[k]) != pin_sig(new_map[k]):
            return False, {'pin': list(k), 'old': list(pin_sig(old_map[k])), 'new': list(pin_sig(new_map[k]))}
    return True, None


def replace_one(wb, obj_graph, obj_nodes, old_node, name, pos, out_conns, parent_class):
    # Returns: (status, detail). status in {'replaced','rolled_back','skipped'}
    name_s = str(name)
    detail = {'name': name_s, 'graph': obj_graph.get_name()}

    was_preexisting = find_existing_override(wb, name_s) is not None
    new_node = L.add_override_event_node(obj_graph, wb, name, pos)
    if new_node is None:
        detail['reason'] = 'add_override_event_node None (function not found on parent)'
        return 'skipped', detail
    was_new = not was_preexisting

    # Compare signatures after reproducing the split
    split_reconstruct(new_node, list(L.get_node_pin_infos(old_node)))
    matched, mismatch = signatures_match(old_node, new_node)
    if not matched:
        detail['reason'] = 'signature mismatch'
        detail['mismatch'] = mismatch
        if was_new:
            L.delete_blueprint_node(wb, new_node)  # roll back new nodes only; keep reused existing nodes
        return 'rolled_back', detail

    # Rewire outgoing links - if even one fails, roll everything back
    made = []  # (from_pin, target_node, to_pin) - for exact reverse-connection on rollback
    ok_all = True
    for c in out_conns:
        from_pin = c['from_pin']
        to_idx = c['to_node']
        to_pin = c['to_pin']
        if to_idx < 0 or to_idx >= len(obj_nodes):
            ok_all = False
            break
        target = obj_nodes[to_idx]
        if L.connect_pins(new_node, from_pin, target, to_pin):
            made.append((from_pin, target, to_pin))
        else:
            ok_all = False
            break
    if not ok_all:
        if was_new:
            L.delete_blueprint_node(wb, new_node)  # delete the whole new node = clears all links
        else:
            # Existing reused node: disconnect exactly the links this tool made (keep pre-existing links - uses DisconnectPins)
            for (fp, tnode, tp) in made:
                L.disconnect_pins(new_node, fp, tnode, tp)
        detail['reason'] = 'rewire failed (partial success -> full rollback)'
        return 'rolled_back', detail

    # Internal call-site count (report only) + BlueprintCallable warning
    detail['internal_call_sites'] = len(L.find_nodes_by_function_name(wb, name_s))
    detail['was_preexisting_override'] = was_preexisting
    if parent_class is not None and not L.has_blueprint_callable_flag(parent_class, name):
        detail['callable_warning'] = 'no BlueprintCallable - compile error risk after replacement if cross-asset external call sites exist'

    L.delete_blueprint_node(wb, old_node)  # delete the old Custom Event
    return 'replaced', detail


try:
    wb = unreal.load_asset(WBP_PATH)
    if wb is None:
        result['message'] = f'failed to load WBP: {WBP_PATH}'
        raise RuntimeError(result['message'])

    parent_class, parent_name = resolve_parent_class(wb)
    result['parent_class'] = parent_name
    result['parent_resolved'] = parent_class is not None
    if parent_class is None:
        result['warnings'].append(
            f'ParentClass resolution failed (parent={parent_name}) - not a native immediate parent, so all events skipped')

    gdata = json.loads(GS.serialize_blueprint_graphs(wb))
    obj_graph_by_name = {g.get_name(): g for g in L.get_blueprint_graphs(wb)}

    for sg in gdata.get('graphs', []):
        gname = sg['name']
        obj_graph = obj_graph_by_name.get(gname)
        if obj_graph is None:
            continue
        obj_nodes = list(L.get_graph_nodes(obj_graph))  # correlation baseline: captured once before mutation (keeps target indices stable)
        s_nodes = sg.get('nodes', [])
        if len(obj_nodes) != len(s_nodes):
            # Graph names match but node counts differ - correlation is unreliable (e.g. MathExpression subgraph). Safe skip.
            result['warnings'].append(f'{gname}: node count mismatch (serialize={len(s_nodes)} obj={len(obj_nodes)}) - skipped')
            continue
        conns = sg.get('connections', [])

        for sn in s_nodes:
            if sn.get('class') != 'K2Node_CustomEvent':
                continue
            oi = sn['index']
            old_node = obj_nodes[oi]
            if old_node.get_class().get_name() != 'K2Node_CustomEvent':
                result['warnings'].append(f'{gname}[{oi}]: correlation mismatch (obj={old_node.get_class().get_name()}) - skipped')
                continue

            name = L.get_event_node_function_name(old_node)
            name_s = str(name)
            is_bie = (parent_class is not None) and L.is_blueprint_implementable_event(parent_class, name)
            out_conns = [c for c in conns if c['from_node'] == oi]

            if not is_bie:
                result['skipped'].append({'name': name_s, 'graph': gname,
                                          'reason': 'not a native BIE (not handed off, or has a return value/out param)'})
                continue

            if DRY_RUN:
                plan = {'name': name_s, 'graph': gname, 'outgoing_connections': len(out_conns),
                        'existing_override': find_existing_override(wb, name_s) is not None}
                if parent_class is not None and not L.has_blueprint_callable_flag(parent_class, name):
                    plan['callable_warning'] = True
                result['replaced'].append(plan)  # dry-run: list of planned replacements
                continue

            pos = unreal.Vector2D(float(sn['pos']['x']), float(sn['pos']['y']) + 250.0)
            status, detail = replace_one(wb, obj_graph, obj_nodes, old_node, name, pos, out_conns, parent_class)
            if status == 'replaced':
                result['replaced'].append(detail)
                if detail.get('callable_warning'):
                    result['warnings'].append(f"{name_s}: {detail['callable_warning']}")
            elif status == 'rolled_back':
                result['rolled_back'].append(detail)
            else:
                result['skipped'].append(detail)

    result['has_name_collision'] = len(result['rolled_back']) > 0

    if not DRY_RUN:
        WT.finalize_blueprint(wb, True)  # compile
        # Gate on the actual compile state - DidBlueprintCompileSucceed is a wrapper that reads UBlueprint::Status (BS_Error etc.)
        # in C++ (Blueprint.Status is public but not exposed to Python reflection). It also catches causes outside this tool
        # (pre-existing graph errors, etc.). Additionally gates on has_name_collision (name collision left by a rollback).
        compile_ok = bool(L.did_blueprint_compile_succeed(wb))
        result['compiled'] = compile_ok
        safe_to_save = compile_ok and (not result['has_name_collision'])
        if not safe_to_save:
            result['message'] = ('save skipped - ' + ('compile error (Status)' if not compile_ok
                                 else 'a rolled-back event name-collides with the native BIE - resolve manually'))
        elif SAVE:
            result['saved'] = bool(unreal.EditorLoadingAndSavingUtils.save_dirty_packages(True, True))

    ok = (len(result['rolled_back']) == 0)
    if DRY_RUN:
        result['success'] = ok
        result['message'] = result['message'] or f"dry-run: planned replacements {len(result['replaced'])}, skipped {len(result['skipped'])}"
    else:
        result['success'] = ok and (result['compiled'] or len(result['replaced']) == 0)
        if not result['message']:
            result['message'] = (f"replaced {len(result['replaced'])}, skipped {len(result['skipped'])}, "
                                 f"rolled back {len(result['rolled_back'])}"
                                 + (' - rolled-back BIE is in a compile-error state due to a name collision' if result['has_name_collision'] else ''))

except Exception as e:
    if not result['message']:
        result['message'] = f'{type(e).__name__}: {e}'

print('__MCP_JSON_BEGIN__')
print(json.dumps(result, ensure_ascii=False))
print('__MCP_JSON_END__')
