#include "RemoteExecutionBridgeSlateInputLibrary.h"

#include "Components/Widget.h"
#include "Containers/Ticker.h"
#include "Engine/Engine.h"
#include "Engine/GameViewportClient.h"
#include "Engine/World.h"
#include "Framework/Application/SlateApplication.h"
#include "GenericPlatform/GenericApplication.h"
#include "Input/Events.h"
#include "Layout/Geometry.h"
#include "Widgets/SViewport.h"
#include "Widgets/SWindow.h"

#include <atomic>

#if PLATFORM_WINDOWS
#include "Windows/AllowWindowsPlatformTypes.h"
#include <Windows.h>
#include "Windows/HideWindowsPlatformTypes.h"
#endif

namespace
{
	int32 constexpr STUCK_BUTTON_WATCHDOG_SECONDS = 15;	// Upper bound for force-release when the matching Up is lost because the host (server.py) crashed - comfortably longer than a normal long press

	struct FPendingButtonRelease
	{
		FVector2D AbsolutePosition{FVector2D::ZeroVector};
		uint64 Generation{0};
	};

	FCriticalSection GPendingButtonLock;
	TMap<FKey, FPendingButtonRelease> GPendingButtons;
	std::atomic<uint64> GNextGeneration{0};

	// Get the native window from the GameViewportClient's SWindow. If null is passed for the Down event, Slate cannot
	// establish mouse capture, so HasMouseCapture()==false at Up time and the CommonUI button click is silently ignored
	// (confirmed via debug logging).
	TSharedPtr<FGenericWindow> ResolveNativeWindowFromViewportClient(UGameViewportClient* viewportClient)
	{
		if (nullptr == viewportClient)
		{
			return nullptr;
		}
		TSharedPtr<SWindow> const window = viewportClient->GetWindow();
		return window.IsValid() ? window->GetNativeWindow() : nullptr;
	}

	TSharedPtr<FGenericWindow> ResolveNativeWindow(UWorld const* world)
	{
		return ResolveNativeWindowFromViewportClient(nullptr != world ? world->GetGameViewport() : nullptr);
	}

	TSharedPtr<FGenericWindow> ResolveNativeWindow(UObject const* worldContextObject)
	{
		return ResolveNativeWindow(nullptr != worldContextObject ? worldContextObject->GetWorld() : nullptr);
	}

	// Only for call paths without a WorldContextObject (SimulateClickAtAbsolutePosition) - references the GEngine global, assuming a single PIE session.
	TSharedPtr<FGenericWindow> ResolveNativeWindow()
	{
		return ResolveNativeWindowFromViewportClient(nullptr != GEngine ? GEngine->GameViewport : nullptr);
	}

	// Mouse capture establishment was observed to depend on the OS-level foreground state - if a synthetic click is injected
	// while the editor window is not foreground (e.g. a command is running from a terminal), HasMouseCapture()
	// intermittently becomes false and the click is silently ignored. Force a foreground switch before injecting Down.
	void ForceWindowForeground(TSharedPtr<FGenericWindow> const& platformWindow)
	{
#if PLATFORM_WINDOWS
		if (platformWindow.IsValid())
		{
			HWND const hwnd = static_cast<HWND>(platformWindow->GetOSWindowHandle());
			if (nullptr != hwnd)
			{
				::SetForegroundWindow(hwnd);
			}
		}
#endif
	}

	void InjectPointerButtonEvent(FVector2D const& absolutePosition, FKey const& button, bool bPressed, TSharedPtr<FGenericWindow> const& platformWindow = nullptr)
	{
		FSlateApplication& slate = FSlateApplication::Get();

		// The event keeps PressedButtons by pointer - lifetime must last until processing completes (synchronous, so a local variable suffices).
		TSet<FKey> const pressedButtons = bPressed ? TSet<FKey>{button} : TSet<FKey>{};

		FPointerEvent const event(
			FSlateApplication::CursorPointerIndex,
			absolutePosition, absolutePosition,
			pressedButtons, button, 0.0f, FModifierKeysState());

		if (bPressed)
		{
			ForceWindowForeground(platformWindow);

			// Send a Move to the position before Down first to establish Hover - SButton::OnMouseButtonUp decides
			// bEventOverButton only via IsHovered() for non-touch events (no geometry fallback), so injecting just Down/Up
			// without a Move gets capture but the click is silently ignored (confirmed via debug logging).
			// With identical Last/New positions it is processed as a zero-delta event and may be ignored, so fake a real move with a 1px offset.
			FVector2D const priorPosition = absolutePosition - FVector2D(1.0, 1.0);
			FPointerEvent const moveEvent(
				FSlateApplication::CursorPointerIndex,
				absolutePosition, priorPosition,
				TSet<FKey>{}, button, 0.0f, FModifierKeysState());
			slate.ProcessMouseMoveEvent(moveEvent);

			// Routing directly via RoutePointer* was observed to crash from conflicts with off-game-thread entry and capture state - keep ProcessMouse*.
			slate.ProcessMouseButtonDownEvent(platformWindow, event);
		}
		else
		{
			slate.ProcessMouseButtonUpEvent(event);
		}
	}

	void CancelStuckButtonWatchdog(FKey const& button)
	{
		FScopeLock lock(&GPendingButtonLock);
		GPendingButtons.Remove(button);
	}

	// Registered at Down time. If the matching Up is lost (e.g. host crash), force-release after N seconds
	// so the button does not stay pressed forever in the FSlateApplication singleton.
	void ScheduleStuckButtonWatchdog(FKey const& button, FVector2D const& absolutePosition)
	{
		uint64 const generation = GNextGeneration.fetch_add(1, std::memory_order_relaxed) + 1;
		{
			FScopeLock lock(&GPendingButtonLock);
			GPendingButtons.Add(button, FPendingButtonRelease{absolutePosition, generation});
		}

		FTSTicker::GetCoreTicker().AddTicker(TEXT("RemoteExecutionBridge_StuckButtonWatchdog"), static_cast<float>(STUCK_BUTTON_WATCHDOG_SECONDS),
			[button, generation](float) -> bool
			{
				bool bShouldRelease = false;
				FVector2D releasePosition{FVector2D::ZeroVector};
				{
					FScopeLock lock(&GPendingButtonLock);
					FPendingButtonRelease const* const pending = GPendingButtons.Find(button);
					if (nullptr != pending && generation == pending->Generation)
					{
						releasePosition = pending->AbsolutePosition;
						bShouldRelease = true;
						GPendingButtons.Remove(button);
					}
				}
				if (bShouldRelease && FSlateApplication::IsInitialized())
				{
					InjectPointerButtonEvent(releasePosition, button, false);
				}
				return false;	// One-shot - no repeat needed
			});
	}
}

FVector2D URemoteExecutionBridgeSlateInputLibrary::ViewportToAbsolutePosition(UObject* WorldContextObject, FVector2D ViewportPosition)
{
	if (nullptr == WorldContextObject)
	{
		return FVector2D::ZeroVector;
	}
	UWorld* const world = WorldContextObject->GetWorld();
	if (nullptr == world || nullptr == world->GetGameViewport())
	{
		return FVector2D::ZeroVector;
	}
	TSharedPtr<SViewport> const viewportWidget = world->GetGameViewport()->GetGameViewportWidget();
	if (!viewportWidget.IsValid())
	{
		return FVector2D::ZeroVector;
	}

	// ProjectWorldToScreen results are viewport-local pixels - convert including widget geometry scale to get desktop absolute coordinates.
	FGeometry const geometry = viewportWidget->GetCachedGeometry();
	FVector2D const local = ViewportPosition / geometry.Scale;
	return geometry.LocalToAbsolute(local);
}

bool URemoteExecutionBridgeSlateInputLibrary::SimulatePointerDown(UObject* WorldContextObject, FVector2D ViewportPosition, FKey Button)
{
	if (!FSlateApplication::IsInitialized())
	{
		return false;
	}
	FVector2D const absolutePosition = ViewportToAbsolutePosition(WorldContextObject, ViewportPosition);
	InjectPointerButtonEvent(absolutePosition, Button, true, ResolveNativeWindow(WorldContextObject));
	ScheduleStuckButtonWatchdog(Button, absolutePosition);
	return true;
}

bool URemoteExecutionBridgeSlateInputLibrary::SimulatePointerUp(UObject* WorldContextObject, FVector2D ViewportPosition, FKey Button)
{
	if (!FSlateApplication::IsInitialized())
	{
		return false;
	}
	CancelStuckButtonWatchdog(Button);
	FVector2D const absolutePosition = ViewportToAbsolutePosition(WorldContextObject, ViewportPosition);
	InjectPointerButtonEvent(absolutePosition, Button, false);
	return true;
}

bool URemoteExecutionBridgeSlateInputLibrary::SimulateClick(UObject* WorldContextObject, FVector2D ViewportPosition, FKey Button)
{
	if (!FSlateApplication::IsInitialized())
	{
		return false;
	}
	FVector2D const absolutePosition = ViewportToAbsolutePosition(WorldContextObject, ViewportPosition);
	InjectPointerButtonEvent(absolutePosition, Button, true, ResolveNativeWindow(WorldContextObject));
	InjectPointerButtonEvent(absolutePosition, Button, false);
	return true;
}

bool URemoteExecutionBridgeSlateInputLibrary::SimulateDrag(UObject* WorldContextObject, FVector2D FromViewportPosition, FVector2D ToViewportPosition, FKey Button, int32 Steps)
{
	if (!FSlateApplication::IsInitialized())
	{
		return false;
	}
	if (1 > Steps)
	{
		return false;	// At least 1 step required - 0 or less is caller misuse
	}

	FVector2D const fromAbsolute = ViewportToAbsolutePosition(WorldContextObject, FromViewportPosition);
	FVector2D const toAbsolute = ViewportToAbsolutePosition(WorldContextObject, ToViewportPosition);

	InjectPointerButtonEvent(fromAbsolute, Button, true, ResolveNativeWindow(WorldContextObject));

	TSet<FKey> const heldButton{Button};
	FVector2D previous = fromAbsolute;
	for (int32 step = 1; step <= Steps; ++step)
	{
		FVector2D const interpolated = FMath::Lerp(fromAbsolute, toAbsolute, static_cast<float>(step) / static_cast<float>(Steps));
		FPointerEvent const moveEvent(
			FSlateApplication::CursorPointerIndex,
			interpolated, previous,
			heldButton, Button, 0.0f, FModifierKeysState());
		FSlateApplication::Get().ProcessMouseMoveEvent(moveEvent);
		previous = interpolated;
	}

	InjectPointerButtonEvent(toAbsolute, Button, false);
	return true;
}

bool URemoteExecutionBridgeSlateInputLibrary::SimulateClickOnWidget(UWidget* Widget)
{
	if (nullptr == Widget || !FSlateApplication::IsInitialized())
	{
		return false;
	}

	FGeometry const geometry = Widget->GetCachedGeometry();
	if (geometry.GetLocalSize().IsNearlyZero())
	{
		return false;	// Widget with no computed layout (not displayed) - cannot be clicked
	}
	FVector2D const center = geometry.LocalToAbsolute(FVector2D(geometry.GetLocalSize()) * 0.5);

	// Uses only cached-geometry hit testing - confirmed to work even with the editor window minimized (DESIGN.md section 9).
	InjectPointerButtonEvent(center, EKeys::LeftMouseButton, true, ResolveNativeWindow(Widget->GetWorld()));
	InjectPointerButtonEvent(center, EKeys::LeftMouseButton, false);
	return true;
}

bool URemoteExecutionBridgeSlateInputLibrary::SimulateClickAtAbsolutePosition(FVector2D AbsolutePosition)
{
	if (!FSlateApplication::IsInitialized())
	{
		return false;
	}
	InjectPointerButtonEvent(AbsolutePosition, EKeys::LeftMouseButton, true, ResolveNativeWindow());
	InjectPointerButtonEvent(AbsolutePosition, EKeys::LeftMouseButton, false);
	return true;
}

bool URemoteExecutionBridgeSlateInputLibrary::InjectKeyDown(FKey Key)
{
	if (!FSlateApplication::IsInitialized())
	{
		return false;
	}
	FKeyEvent const event(Key, FModifierKeysState(), 0, false, 0, 0);
	return FSlateApplication::Get().ProcessKeyDownEvent(event);	// Return value = whether any widget actually consumed it (so the caller can tell focus errors apart)
}

bool URemoteExecutionBridgeSlateInputLibrary::InjectKeyUp(FKey Key)
{
	if (!FSlateApplication::IsInitialized())
	{
		return false;
	}
	FKeyEvent const event(Key, FModifierKeysState(), 0, false, 0, 0);
	return FSlateApplication::Get().ProcessKeyUpEvent(event);
}

bool URemoteExecutionBridgeSlateInputLibrary::InjectKeyChar(FString const& Text)
{
	if (!FSlateApplication::IsInitialized())
	{
		return false;
	}

	FSlateApplication& slate = FSlateApplication::Get();
	bool bAllHandled = true;
	for (TCHAR const character : Text)
	{
		FCharacterEvent const event(character, FModifierKeysState(), 0u, false);
		bAllHandled = slate.ProcessKeyCharEvent(event) && bAllHandled;
	}
	return bAllHandled;
}

bool URemoteExecutionBridgeSlateInputLibrary::FocusGameViewport()
{
	if (!FSlateApplication::IsInitialized())
	{
		return false;
	}
	FSlateApplication::Get().SetAllUserFocusToGameViewport();
	return true;
}
