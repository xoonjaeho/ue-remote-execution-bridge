#pragma once

#include "CoreMinimal.h"
#include "Kismet/BlueprintFunctionLibrary.h"
#include "RemoteExecutionBridgeAutomationDriverLibrary.generated.h"

// Input injection for automated PIE interaction verification - Backend C (AutomationDriver). Async kickoff+poll only, keyboard sequences only
// (M1 spike finding: the MoveByOffset cursor coordinate system is completely separate from SetMouse, so coordinate-based mouse is out of scope - use Backend B if needed).
UCLASS()
class REMOTEEXECUTIONBRIDGE_API URemoteExecutionBridgeAutomationDriverLibrary final : public UBlueprintFunctionLibrary
{
	GENERATED_BODY()

public:
	// Enable AutomationDriver (reference counted) - real HW input is blocked while enabled, so after AutoDisableSeconds
	// elapses, only the share whose matching Disable was lost is released automatically (other sessions' Enables are untouched). The return value is this call's
	// generation - passing it as-is to DisableAutomationDriver(Generation) releases exactly this call's share.
	UFUNCTION(BlueprintCallable, Category = "RemoteExecution|Input|AutomationDriver")
	static int64 EnableAutomationDriver(float AutoDisableSeconds = 30.f);

	// Release one AutomationDriver reference - it is actually Disabled only when the count reaches 0. With Generation specified, only that
	// Enable call's share is released (prevents another session from releasing it first) - false if the generation
	// was already released or does not exist. With Generation=0 (default), one arbitrary live generation is released (for legacy call compatibility -
	// the reference count itself is always accurate either way).
	UFUNCTION(BlueprintCallable, Category = "RemoteExecution|Input|AutomationDriver")
	static bool DisableAutomationDriver(int64 Generation = 0);

	// Run a keyboard-only sequence (JSON step array: press/release/press_chord/type/wait) asynchronously - returns only a GUID handle
	// immediately (Python remote execution runs synchronously on the game thread, so blocking here would deadlock). Check completion by
	// polling with PollDriverSequence. Explicitly fails (empty string) if PIE is not running.
	UFUNCTION(BlueprintCallable, Category = "RemoteExecution|Input|AutomationDriver")
	static FString RunDriverSequence(FString const& StepsJson);

	// Non-blocking completion check. On completion (bOutDone=true) the handle is removed from the internal map immediately (prevents unbounded growth per session).
	// A false return value means the handle itself does not exist (already consumed or invalid).
	UFUNCTION(BlueprintCallable, Category = "RemoteExecution|Input|AutomationDriver")
	static bool PollDriverSequence(FString const& Handle, bool& bOutDone, bool& bOutSuccess);
};
