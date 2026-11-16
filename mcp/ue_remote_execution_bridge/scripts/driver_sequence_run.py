# driver_sequence_run.py
# Asynchronous kickoff of a Backend C (AutomationDriver) keyboard sequence - returns a GUID handle immediately (no blocking).
# STEPS_B64: base64(JSON array) - [{"action":"press","key":"A"}, {"action":"release","key":"A"}, ...]
#   Supported actions: press({"key"})/release({"key"})/press_chord({"keys":[2-3 keys]})/type({"text"})/wait({"seconds"})
# Must be enabled beforehand via automation_driver_toggle.py (ACTION='enable').
import base64
import json
import unreal

STEPS_B64 = '__STEPS_B64__'

result = {'success': False, 'handle': '', 'message': ''}

try:
    if not hasattr(unreal, 'RemoteExecutionBridgeAutomationDriverLibrary'):
        result['message'] = 'RemoteExecutionBridgeAutomationDriverLibrary not found - check the C++ plugin build'
        raise SystemExit(0)

    steps_json = base64.b64decode(STEPS_B64).decode('utf-8')
    handle = unreal.RemoteExecutionBridgeAutomationDriverLibrary.run_driver_sequence(steps_json)
    if handle:
        result['success'] = True
        result['handle'] = handle
    else:
        result['message'] = 'RunDriverSequence failed - PIE not running, AutomationDriver disabled (or auto-disabled by the safety timeout), or step parsing failed'

except SystemExit:
    pass
except Exception as e:
    result['message'] = f'{type(e).__name__}: {e}'

print('__MCP_JSON_BEGIN__')
print(json.dumps(result, ensure_ascii=False))
print('__MCP_JSON_END__')
