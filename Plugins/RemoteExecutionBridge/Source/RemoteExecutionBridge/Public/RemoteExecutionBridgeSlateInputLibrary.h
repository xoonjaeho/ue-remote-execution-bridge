#pragma once

#include "CoreMinimal.h"
#include "InputCoreTypes.h"
#include "Kismet/BlueprintFunctionLibrary.h"
#include "RemoteExecutionBridgeSlateInputLibrary.generated.h"

class UWidget;

// Input injection for automated PIE interaction verification - Backend A (FSlateApplication). Goes through all of Slate hit testing and routing.
UCLASS()
class REMOTEEXECUTIONBRIDGE_API URemoteExecutionBridgeSlateInputLibrary final : public UBlueprintFunctionLibrary
{
	GENERATED_BODY()

public:
	// PIE viewport pixel coordinates -> desktop absolute coordinates (converts ProjectWorldToScreen results into click coordinates).
	UFUNCTION(BlueprintCallable, Category = "RemoteExecution|Input|Slate", meta = (WorldContext = "WorldContextObject"))
	static FVector2D ViewportToAbsolutePosition(UObject* WorldContextObject, FVector2D ViewportPosition);

	// Inject a pointer button Down at viewport-local coordinates. The matching Up is a separate call, so a watchdog (force Up after N seconds) is registered.
	UFUNCTION(BlueprintCallable, Category = "RemoteExecution|Input|Slate", meta = (WorldContext = "WorldContextObject"))
	static bool SimulatePointerDown(UObject* WorldContextObject, FVector2D ViewportPosition, FKey Button);

	// Inject a pointer button Up at viewport-local coordinates. Cancels the matching watchdog.
	UFUNCTION(BlueprintCallable, Category = "RemoteExecution|Input|Slate", meta = (WorldContext = "WorldContextObject"))
	static bool SimulatePointerUp(UObject* WorldContextObject, FVector2D ViewportPosition, FKey Button);

	// Synchronous synthetic Down+Up click - completes within the same call, so no watchdog is needed.
	UFUNCTION(BlueprintCallable, Category = "RemoteExecution|Input|Slate", meta = (WorldContext = "WorldContextObject"))
	static bool SimulateClick(UObject* WorldContextObject, FVector2D ViewportPosition, FKey Button);

	// Down at FromViewportPosition -> interpolated MoveEvents over Steps -> Up at ToViewportPosition.
	UFUNCTION(BlueprintCallable, Category = "RemoteExecution|Input|Slate", meta = (WorldContext = "WorldContextObject"))
	static bool SimulateDrag(UObject* WorldContextObject, FVector2D FromViewportPosition, FVector2D ToViewportPosition, FKey Button, int32 Steps = 8);

	// Inject a synthetic mouse click (down+up) at the widget center's desktop absolute coordinates - goes through all of Slate hit testing and routing.
	UFUNCTION(BlueprintCallable, Category = "RemoteExecution|Input|Slate")
	static bool SimulateClickOnWidget(UWidget* Widget);

	// Inject a click at desktop absolute coordinates - for 3D hits (buildings, etc.) and arbitrary coordinates.
	UFUNCTION(BlueprintCallable, Category = "RemoteExecution|Input|Slate")
	static bool SimulateClickAtAbsolutePosition(FVector2D AbsolutePosition);

	// Inject key Down - routed to the widget that currently has keyboard focus (or the viewport). Gameplay keys (movement, console toggle, etc.) require
	// calling FocusGameViewport() first to put focus on the viewport - not called automatically so focus is not stolen while typing in a text field.
	UFUNCTION(BlueprintCallable, Category = "RemoteExecution|Input|Slate")
	static bool InjectKeyDown(FKey Key);

	// Inject key Up.
	UFUNCTION(BlueprintCallable, Category = "RemoteExecution|Input|Slate")
	static bool InjectKeyUp(FKey Key);

	// Inject a string one character at a time as KeyChar events - routed to the widget that currently has keyboard focus (EditableText, etc.).
	// The target text field must have focus acquired beforehand, e.g. via SimulateClick.
	UFUNCTION(BlueprintCallable, Category = "RemoteExecution|Input|Slate")
	static bool InjectKeyChar(FString const& Text);

	// Explicitly move keyboard focus to the PIE game viewport - call before gameplay key input.
	// Do not call when typing into a text field (focus moves to the viewport and the text field loses focus).
	UFUNCTION(BlueprintCallable, Category = "RemoteExecution|Input|Slate")
	static bool FocusGameViewport();
};
