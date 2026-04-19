#pragma once

#include "CoreMinimal.h"
#include "Containers/Ticker.h"

enum class ERemoteExecutionStatus : uint8
{
	Connected,
	Disconnected,
};

DECLARE_MULTICAST_DELEGATE_OneParam(FOnRemoteExecutionStatusChanged, ERemoteExecutionStatus);

class FRemoteExecutionStatusMonitor
{
public:
	FRemoteExecutionStatusMonitor();
	~FRemoteExecutionStatusMonitor();

	FOnRemoteExecutionStatusChanged OnStatusChanged;

	ERemoteExecutionStatus GetCurrentStatus() const { return CurrentStatus; }

private:
	bool OnTick(float DeltaTime);

	FTSTicker::FDelegateHandle TickerHandle;
	ERemoteExecutionStatus CurrentStatus;

	// Heartbeat interval is 2s; allow up to 6 missed beats before marking Disconnected.
	// This prevents false negatives when another session holds the mutex for several seconds.
	static constexpr double TimeoutSeconds = 12.0;
};
