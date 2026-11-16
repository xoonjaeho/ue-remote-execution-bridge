#include "RemoteExecutionStatusMonitor.h"
#include "RemoteExecutionBridgeLibrary.h"

namespace
{
	// Heartbeat interval is 2s; allow up to 6 missed beats before marking Disconnected.
	// This prevents false negatives when another session holds the mutex for several seconds.
	constexpr double BridgeTimeoutSeconds = 12.0;

	[[nodiscard]] bool IsHeartbeatFresh(double LastHeartbeatTime, double TimeoutSeconds)
	{
		if (LastHeartbeatTime <= 0.0)
		{
			return false;
		}
		return (FPlatformTime::Seconds() - LastHeartbeatTime) < TimeoutSeconds;
	}
}

FRemoteExecutionStatusMonitor::FRemoteExecutionStatusMonitor()
	: bBridgeConnected(false)
{
	TickerHandle = FTSTicker::GetCoreTicker().AddTicker(
		FTickerDelegate::CreateRaw(this, &FRemoteExecutionStatusMonitor::OnTick),
		1.0f
	);
}

FRemoteExecutionStatusMonitor::~FRemoteExecutionStatusMonitor()
{
	FTSTicker::GetCoreTicker().RemoveTicker(TickerHandle);
}

bool FRemoteExecutionStatusMonitor::IsBridgeConnected()
{
	return IsHeartbeatFresh(URemoteExecutionBridgeLibrary::GetLastHeartbeatTime(), BridgeTimeoutSeconds);
}

bool FRemoteExecutionStatusMonitor::OnTick(float DeltaTime)
{
	const bool bNewBridgeConnected = IsBridgeConnected();

	if (bNewBridgeConnected == bBridgeConnected)
	{
		return true;
	}

	bBridgeConnected = bNewBridgeConnected;
	OnStatusChanged.Broadcast();

	return true;
}
