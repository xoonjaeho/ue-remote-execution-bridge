#include "RemoteExecutionBridgeViewportInputLibrary.h"

#include "Containers/Ticker.h"
#include "Engine/GameViewportClient.h"
#include "Engine/World.h"
#include "InputKeyEventArgs.h"
#include "UnrealClient.h"

#include <atomic>

namespace
{
	int32 constexpr STUCK_KEY_WATCHDOG_SECONDS = 15;	// Upper bound for force-release when the matching Up is lost because the host (server.py) crashed - same policy as Backend A

	struct FPendingKeyRelease
	{
		TWeakObjectPtr<UGameViewportClient> ViewportClient;
		uint64 Generation{0};
	};

	FCriticalSection GPendingKeyLock;
	TMap<FKey, FPendingKeyRelease> GPendingKeys;
	std::atomic<uint64> GNextKeyGeneration{0};

	UGameViewportClient* ResolveGameViewportClient(UObject* worldContextObject)
	{
		if (nullptr == worldContextObject)
		{
			return nullptr;
		}
		UWorld* const world = worldContextObject->GetWorld();
		if (nullptr == world)
		{
			return nullptr;
		}
		return world->GetGameViewport();
	}

	// SetMouse synchronously updates the cursor and CachedCursorPos via SetMouse(x,y), so it must always be called before InputKey.
	bool InjectViewportKeyEvent(UGameViewportClient* viewportClient, FKey const& key, bool bPressed)
	{
		if (nullptr == viewportClient)
		{
			return false;
		}
		FInputKeyEventArgs const args = FInputKeyEventArgs::CreateSimulated(
			key,
			bPressed ? IE_Pressed : IE_Released,
			bPressed ? 1.0f : 0.0f,
			-1,
			INPUTDEVICEID_NONE,
			false,
			viewportClient->Viewport);
		return viewportClient->InputKey(args);
	}

	void CancelStuckKeyWatchdog(FKey const& key)
	{
		FScopeLock lock(&GPendingKeyLock);
		GPendingKeys.Remove(key);
	}

	// Registered at Down time. If the matching Up is lost (e.g. host crash), force-release after N seconds
	// so the key does not stay pressed forever in the game input state.
	void ScheduleStuckKeyWatchdog(FKey const& key, UGameViewportClient* viewportClient)
	{
		uint64 const generation = GNextKeyGeneration.fetch_add(1, std::memory_order_relaxed) + 1;
		{
			FScopeLock lock(&GPendingKeyLock);
			GPendingKeys.Add(key, FPendingKeyRelease{viewportClient, generation});
		}

		FTSTicker::GetCoreTicker().AddTicker(TEXT("RemoteExecutionBridge_StuckViewportKeyWatchdog"), static_cast<float>(STUCK_KEY_WATCHDOG_SECONDS),
			[key, generation](float) -> bool
			{
				TWeakObjectPtr<UGameViewportClient> pendingViewportClient;
				bool bShouldRelease = false;
				{
					FScopeLock lock(&GPendingKeyLock);
					FPendingKeyRelease const* const pending = GPendingKeys.Find(key);
					if (nullptr != pending and generation == pending->Generation)
					{
						pendingViewportClient = pending->ViewportClient;
						bShouldRelease = true;
						GPendingKeys.Remove(key);
					}
				}
				if (bShouldRelease and pendingViewportClient.IsValid())
				{
					InjectViewportKeyEvent(pendingViewportClient.Get(), key, false);
				}
				return false;	// One-shot - no repeat needed
			});
	}
}

bool URemoteExecutionBridgeViewportInputLibrary::SetPointerPosition(UObject* WorldContextObject, FVector2D ViewportPosition)
{
	UGameViewportClient* const viewportClient = ResolveGameViewportClient(WorldContextObject);
	if (nullptr == viewportClient or nullptr == viewportClient->Viewport)
	{
		return false;
	}
	viewportClient->Viewport->SetMouse(FMath::RoundToInt32(ViewportPosition.X), FMath::RoundToInt32(ViewportPosition.Y));
	return true;
}

bool URemoteExecutionBridgeViewportInputLibrary::InjectViewportKey(UObject* WorldContextObject, FKey Key, bool bPressed)
{
	UGameViewportClient* const viewportClient = ResolveGameViewportClient(WorldContextObject);
	bool const handled = InjectViewportKeyEvent(viewportClient, Key, bPressed);
	if (nullptr == viewportClient)
	{
		return handled;	// No PIE - no event existed at all, so no watchdog registration/cancel is needed (prevents phantom entries)
	}
	if (bPressed)
	{
		ScheduleStuckKeyWatchdog(Key, viewportClient);
	}
	else
	{
		CancelStuckKeyWatchdog(Key);
	}
	return handled;
}

bool URemoteExecutionBridgeViewportInputLibrary::SimulateViewportClick(UObject* WorldContextObject, FVector2D ViewportPosition, FKey Button)
{
	if (not SetPointerPosition(WorldContextObject, ViewportPosition))
	{
		return false;
	}
	UGameViewportClient* const viewportClient = ResolveGameViewportClient(WorldContextObject);
	InjectViewportKeyEvent(viewportClient, Button, true);
	InjectViewportKeyEvent(viewportClient, Button, false);
	return true;
}

bool URemoteExecutionBridgeViewportInputLibrary::SimulateViewportDrag(UObject* WorldContextObject, FVector2D FromViewportPosition, FVector2D ToViewportPosition, FKey Button, int32 Steps)
{
	if (1 > Steps)
	{
		return false;	// At least 1 step required - 0 or less is caller misuse
	}
	if (not SetPointerPosition(WorldContextObject, FromViewportPosition))
	{
		return false;
	}
	UGameViewportClient* const viewportClient = ResolveGameViewportClient(WorldContextObject);
	InjectViewportKeyEvent(viewportClient, Button, true);

	for (int32 step = 1; step <= Steps; ++step)
	{
		FVector2D const interpolated = FMath::Lerp(FromViewportPosition, ToViewportPosition, static_cast<float>(step) / static_cast<float>(Steps));
		SetPointerPosition(WorldContextObject, interpolated);	// Only update the position, no InputKey (intermediate point)
	}

	InjectViewportKeyEvent(viewportClient, Button, false);
	return true;
}
