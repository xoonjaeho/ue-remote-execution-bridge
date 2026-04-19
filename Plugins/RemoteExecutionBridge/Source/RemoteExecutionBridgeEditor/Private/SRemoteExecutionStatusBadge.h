#pragma once

#include "CoreMinimal.h"
#include "Widgets/SCompoundWidget.h"
#include "RemoteExecutionStatusMonitor.h"

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
	void OnStatusChanged(ERemoteExecutionStatus NewStatus);

	FSlateColor GetStatusColor() const;
	FText GetTooltipText() const;

	TSharedPtr<FRemoteExecutionStatusMonitor> Monitor;
	ERemoteExecutionStatus CurrentStatus;
};
