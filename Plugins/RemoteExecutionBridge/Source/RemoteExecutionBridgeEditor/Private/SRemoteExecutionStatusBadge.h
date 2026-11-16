#pragma once

#include "CoreMinimal.h"
#include "Widgets/SCompoundWidget.h"
#include "RemoteExecutionStatusMonitor.h"

/**
 * Draws the MCP server connection state as a single dot - green while the heartbeat is fresh, red when it drops.
 * Info about the connected MCP process/session is in the tooltip.
 */
class SRemoteExecutionStatusBadge : public SCompoundWidget
{
public:
	SLATE_BEGIN_ARGS(SRemoteExecutionStatusBadge)
		: _Monitor(nullptr)
	{}
		SLATE_ARGUMENT(TSharedPtr<FRemoteExecutionStatusMonitor>, Monitor)
	SLATE_END_ARGS()

	void Construct(const FArguments& InArgs);
	virtual ~SRemoteExecutionStatusBadge() override;

private:
	void OnStatusChanged();

	[[nodiscard]] FSlateColor GetStatusColor() const;
	[[nodiscard]] FText GetTooltipText() const;

	TSharedPtr<FRemoteExecutionStatusMonitor> Monitor;
};
