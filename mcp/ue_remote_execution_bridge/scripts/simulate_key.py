# simulate_key.py
# Keyboard input injection - shared by Backend A (Slate) / B (Viewport). backend='viewport' does not support action='char'
# (GameViewportClient::InputKey only handles raw key events - text typing is Slate ProcessKeyCharEvent only).
# ACTION: 'down' | 'up' | 'char' | 'focus_viewport'
# BACKEND: 'slate' | 'viewport'
# KEY: FKey name (ignored for action='char'|'focus_viewport')
# TEXT_B64: base64(string) - action='char' only
import base64
import json
import unreal

ACTION = '__ACTION__'
BACKEND = '__BACKEND__'
KEY = '__KEY__'
TEXT_B64 = '__TEXT_B64__'

result = {'success': False, 'handled': None, 'message': ''}

try:
    if ACTION == 'focus_viewport':
        result['handled'] = unreal.RemoteExecutionBridgeSlateInputLibrary.focus_game_viewport()
        result['success'] = True
        raise SystemExit(0)

    if BACKEND == 'slate':
        lib = unreal.RemoteExecutionBridgeSlateInputLibrary
        if ACTION == 'down':
            key = unreal.Key()
            key.set_editor_property('key_name', KEY)
            result['handled'] = lib.inject_key_down(key)
            result['success'] = True
        elif ACTION == 'up':
            key = unreal.Key()
            key.set_editor_property('key_name', KEY)
            result['handled'] = lib.inject_key_up(key)
            result['success'] = True
        elif ACTION == 'char':
            text = base64.b64decode(TEXT_B64).decode('utf-8')
            result['handled'] = lib.inject_key_char(text)
            result['success'] = True
        else:
            result['message'] = f'unsupported action: {ACTION}'

    elif BACKEND == 'viewport':
        if ACTION == 'char':
            result['message'] = "backend='viewport' does not support action='char' - use backend='slate'"
            raise SystemExit(0)

        es = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem)
        world = es.get_game_world()
        if world is None:
            result['message'] = 'no PIE world — check that PIE is running'
            raise SystemExit(0)
        pc = unreal.GameplayStatics.get_player_controller(world, 0)

        lib = unreal.RemoteExecutionBridgeViewportInputLibrary
        key = unreal.Key()
        key.set_editor_property('key_name', KEY)
        if ACTION == 'down':
            result['handled'] = lib.inject_viewport_key(pc, key, True)
            result['success'] = True
        elif ACTION == 'up':
            result['handled'] = lib.inject_viewport_key(pc, key, False)
            result['success'] = True
        else:
            result['message'] = f'unsupported action: {ACTION}'

    else:
        result['message'] = f'unsupported backend: {BACKEND}'

except SystemExit:
    pass
except Exception as e:
    result['message'] = f'{type(e).__name__}: {e}'

print('__MCP_JSON_BEGIN__')
print(json.dumps(result, ensure_ascii=False))
print('__MCP_JSON_END__')
