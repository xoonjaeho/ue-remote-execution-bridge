# driver_sequence_poll.py
# Non-blocking check of Backend C sequence completion. HANDLE: GUID string returned by driver_sequence_run.py.
# On completion (done=true) the C++ side removes the internal map entry immediately, so polling the same handle again returns done=false.
import json
import unreal

HANDLE = '__HANDLE__'

result = {'success': False, 'done': False, 'done_success': False, 'message': ''}

try:
    if not hasattr(unreal, 'RemoteExecutionBridgeAutomationDriverLibrary'):
        result['message'] = 'RemoteExecutionBridgeAutomationDriverLibrary not found - check the C++ plugin build'
        raise SystemExit(0)

    poll = unreal.RemoteExecutionBridgeAutomationDriverLibrary.poll_driver_sequence(HANDLE)
    # Measured on the UE Python binding (2026-07-10): a bool return plus two bool& comes back as a (bOutDone, bOutSuccess) tuple on success,
    # and as None on failure (handle does not exist) - the returned bool itself is not included in the tuple.
    if isinstance(poll, tuple) and len(poll) == 2:
        done, done_success = poll
        result['success'] = True
        result['done'] = bool(done)
        result['done_success'] = bool(done_success)
    else:
        result['message'] = f'handle does not exist (already consumed or invalid): {HANDLE}'

except SystemExit:
    pass
except Exception as e:
    result['message'] = f'{type(e).__name__}: {e}'

print('__MCP_JSON_BEGIN__')
print(json.dumps(result, ensure_ascii=False))
print('__MCP_JSON_END__')
