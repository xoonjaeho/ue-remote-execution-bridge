#pragma once

#include "CoreMinimal.h"
#include "InputCoreTypes.h"
#include "Kismet/BlueprintFunctionLibrary.h"
#include "RemoteExecutionBridgeViewportInputLibrary.generated.h"

// Input injection for automated PIE interaction verification - Backend B (UGameViewportClient). Reaches gameplay input (PlayerController, etc.) via the SetMouse+InputKey path.
UCLASS()
class REMOTEEXECUTIONBRIDGE_API URemoteExecutionBridgeViewportInputLibrary final : public UBlueprintFunctionLibrary
{
	GENERATED_BODY()

public:
	// Move the cursor to viewport-local coordinates - SetMouse updates cursor state synchronously, so it must be called before InputKey.
	UFUNCTION(BlueprintCallable, Category = "RemoteExecution|Input|Viewport", meta = (WorldContext = "WorldContextObject"))
	static bool SetPointerPosition(UObject* WorldContextObject, FVector2D ViewportPosition);

	// Shared Down/Up injection for mouse buttons and keyboard keys. Down's matching Up is a separate call, so a watchdog (force Up after N seconds) is registered,
	// and Up cancels the matching watchdog.
	UFUNCTION(BlueprintCallable, Category = "RemoteExecution|Input|Viewport", meta = (WorldContext = "WorldContextObject"))
	static bool InjectViewportKey(UObject* WorldContextObject, FKey Key, bool bPressed);

	// SetPointerPosition + synchronous synthetic Down+Up click - completes within the same call, so no watchdog is needed.
	UFUNCTION(BlueprintCallable, Category = "RemoteExecution|Input|Viewport", meta = (WorldContext = "WorldContextObject"))
	static bool SimulateViewportClick(UObject* WorldContextObject, FVector2D ViewportPosition, FKey Button);

	// Down at FromViewportPosition -> interpolated SetPointerPosition moves over Steps -> Up at ToViewportPosition.
	UFUNCTION(BlueprintCallable, Category = "RemoteExecution|Input|Viewport", meta = (WorldContext = "WorldContextObject"))
	static bool SimulateViewportDrag(UObject* WorldContextObject, FVector2D FromViewportPosition, FVector2D ToViewportPosition, FKey Button, int32 Steps = 8);
};
