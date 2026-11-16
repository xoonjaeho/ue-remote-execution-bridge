# simulate_pointer.py
# Mouse pointer input injection - shared by Backend A (Slate) / B (Viewport).
# ACTION: 'click' | 'drag' | 'down' | 'up' (down/up are only for server.py's long_press orchestration)
# BACKEND: 'slate' | 'viewport'
# POSITION_B64: base64(JSON [x, y]) - coordinates for click/down/up, the From coordinate for drag
# TO_POSITION_B64: base64(JSON [x, y]) - To coordinate for drag only (ignored for other actions)
# BUTTON: FKey name (e.g. 'LeftMouseButton')
# STEPS: number of interpolation steps for drag (ignored for other actions)
import base64
import json
import unreal

ACTION = '__ACTION__'
BACKEND = '__BACKEND__'
POSITION_B64 = '__POSITION_B64__'
TO_POSITION_B64 = '__TO_POSITION_B64__'
BUTTON = '__BUTTON__'
STEPS = __STEPS__

result = {'success': False, 'handled': None, 'message': ''}

try:
    es = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem)
    world = es.get_game_world()
    if world is None:
        result['message'] = 'no PIE world — check that PIE is running'
        raise SystemExit(0)
    pc = unreal.GameplayStatics.get_player_controller(world, 0)
    if pc is None:
        result['message'] = 'PlayerController not found'
        raise SystemExit(0)

    pos = json.loads(base64.b64decode(POSITION_B64).decode('utf-8'))
    position = unreal.Vector2D(pos[0], pos[1])

    key = unreal.Key()
    key.set_editor_property('key_name', BUTTON)

    if BACKEND == 'slate':
        lib = unreal.RemoteExecutionBridgeSlateInputLibrary
    elif BACKEND == 'viewport':
        lib = unreal.RemoteExecutionBridgeViewportInputLibrary
    else:
        result['message'] = f'unsupported backend: {BACKEND}'
        raise SystemExit(0)

    if ACTION == 'click':
        if BACKEND == 'slate':
            result['handled'] = lib.simulate_click(pc, position, key)
        else:
            result['handled'] = lib.simulate_viewport_click(pc, position, key)
        result['success'] = True

    elif ACTION == 'drag':
        to_pos = json.loads(base64.b64decode(TO_POSITION_B64).decode('utf-8'))
        to_position = unreal.Vector2D(to_pos[0], to_pos[1])
        if BACKEND == 'slate':
            result['handled'] = lib.simulate_drag(pc, position, to_position, key, STEPS)
        else:
            result['handled'] = lib.simulate_viewport_drag(pc, position, to_position, key, STEPS)
        result['success'] = True

    elif ACTION == 'down':
        if BACKEND == 'slate':
            result['handled'] = lib.simulate_pointer_down(pc, position, key)
        else:
            # SetPointerPosition must be called before InputKey
            lib.set_pointer_position(pc, position)
            result['handled'] = lib.inject_viewport_key(pc, key, True)
        result['success'] = True

    elif ACTION == 'up':
        if BACKEND == 'slate':
            result['handled'] = lib.simulate_pointer_up(pc, position, key)
        else:
            lib.set_pointer_position(pc, position)
            result['handled'] = lib.inject_viewport_key(pc, key, False)
        result['success'] = True

    else:
        result['message'] = f'unsupported action: {ACTION}'

except SystemExit:
    pass
except Exception as e:
    result['message'] = f'{type(e).__name__}: {e}'

print('__MCP_JSON_BEGIN__')
print(json.dumps(result, ensure_ascii=False))
print('__MCP_JSON_END__')
