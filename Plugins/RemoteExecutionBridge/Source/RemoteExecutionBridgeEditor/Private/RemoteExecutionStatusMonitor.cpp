#include "RemoteExecutionStatusMonitor.h"
#include "RemoteExecutionBridgeLibrary.h"

FRemoteExecutionStatusMonitor::FRemoteExecutionStatusMonitor()
	: CurrentStatus(ERemoteExecutionStatus::Disconnected)
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

bool FRemoteExecutionStatusMonitor::OnTick(float DeltaTime)
{
	const double LastHeartbeat = URemoteExecutionBridgeLibrary::GetLastHeartbeatTime();
	const double Elapsed = (LastHeartbeat > 0.0)
		? (FPlatformTime::Seconds() - LastHeartbeat)
		: TNumericLimits<double>::Max();

	const ERemoteExecutionStatus NewStatus = (Elapsed < TimeoutSeconds)
		? ERemoteExecutionStatus::Connected
		: ERemoteExecutionStatus::Disconnected;

	if (NewStatus != CurrentStatus)
	{
		CurrentStatus = NewStatus;
		OnStatusChanged.Broadcast(NewStatus);
	}

	return true;
}
