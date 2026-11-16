# automation_driver_toggle.py
# Backend C (AutomationDriver) enable lifecycle control.
# ACTION: 'enable' | 'disable'
# AUTO_DISABLE_SECONDS: action='enable' only - safety timeout (seconds); if the matching disable is lost, only this Enable's share is returned automatically
# GENERATION: action='disable' only - the generation value returned by enable. 0 returns one arbitrary live generation (legacy compatibility)
import json
import unreal

ACTION = '__ACTION__'
AUTO_DISABLE_SECONDS = __AUTO_DISABLE_SECONDS__
GENERATION = __GENERATION__

result = {'success': False, 'message': ''}

try:
    if not hasattr(unreal, 'RemoteExecutionBridgeAutomationDriverLibrary'):
        result['message'] = 'RemoteExecutionBridgeAutomationDriverLibrary not found - check the C++ plugin build'
        raise SystemExit(0)

    lib = unreal.RemoteExecutionBridgeAutomationDriverLibrary
    if ACTION == 'enable':
        generation = lib.enable_automation_driver(AUTO_DISABLE_SECONDS)
        result['success'] = bool(generation)
        result['generation'] = generation
    elif ACTION == 'disable':
        result['success'] = bool(lib.disable_automation_driver(GENERATION))
        if not result['success']:
            result['message'] = 'no matching Enable, or the given generation was already returned or does not exist'
    else:
        result['message'] = f'unsupported action: {ACTION}'

except SystemExit:
    pass
except Exception as e:
    result['message'] = f'{type(e).__name__}: {e}'

print('__MCP_JSON_BEGIN__')
print(json.dumps(result, ensure_ascii=False))
print('__MCP_JSON_END__')
