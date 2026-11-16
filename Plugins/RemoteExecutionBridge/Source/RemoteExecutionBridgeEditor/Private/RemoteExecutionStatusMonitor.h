#pragma once

#include "CoreMinimal.h"
#include "Containers/Ticker.h"

// Broadcast when the connection state changes. Carries no value - the receiver (badge) reads the static judgment directly.
DECLARE_MULTICAST_DELEGATE(FOnRemoteExecutionStatusChanged);

/**
 * Watches the MCP server connection state with a ticker. The only evidence is "when the MCP server last pinged us".
 */
class FRemoteExecutionStatusMonitor
{
public:
	FRemoteExecutionStatusMonitor();
	~FRemoteExecutionStatusMonitor();

	// State change notification - only gives a reason to redraw. Read the value directly via the static below.
	FOnRemoteExecutionStatusChanged OnStatusChanged;

	// Judged immediately from heartbeat age. It does not go through the ticker-cached value, so it does not depend on delegate chain timing
	// - this prevents a tooltip from drawing a stale state just before the update.
	[[nodiscard]] static bool IsBridgeConnected();

private:
	bool OnTick(float DeltaTime);

	FTSTicker::FDelegateHandle TickerHandle;

	// Cache for change detection. The judgment itself is made by the static above.
	bool bBridgeConnected;
};
