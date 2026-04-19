#include "SRemoteExecutionStatusBadge.h"
#include "RemoteExecutionBridgeLibrary.h"
#include "Widgets/Text/STextBlock.h"
#include "Misc/ConfigCacheIni.h"
#include "Misc/Paths.h"

void SRemoteExecutionStatusBadge::Construct(const FArguments& InArgs)
{
	Monitor = InArgs._Monitor;
	CurrentStatus = Monitor.IsValid() ? Monitor->GetCurrentStatus() : ERemoteExecutionStatus::Disconnected;

	if (Monitor.IsValid())
	{
		Monitor->OnStatusChanged.AddRaw(this, &SRemoteExecutionStatusBadge::OnStatusChanged);
	}

	ChildSlot
	.HAlign(HAlign_Right)
	.VAlign(VAlign_Center)
	.Padding(0.0f, 0.0f, 8.0f, 0.0f)
	[
		SNew(SBox)
		.ToolTipText(TAttribute<FText>::CreateRaw(this, &SRemoteExecutionStatusBadge::GetTooltipText))
		[
			SNew(STextBlock)
			.Text(FText::FromString(TEXT("●")))
			.ColorAndOpacity(TAttribute<FSlateColor>::CreateRaw(this, &SRemoteExecutionStatusBadge::GetStatusColor))
		]
	];
}

SRemoteExecutionStatusBadge::~SRemoteExecutionStatusBadge()
{
	if (Monitor.IsValid())
	{
		Monitor->OnStatusChanged.RemoveAll(this);
	}
}

void SRemoteExecutionStatusBadge::OnStatusChanged(ERemoteExecutionStatus NewStatus)
{
	CurrentStatus = NewStatus;
	Invalidate(EInvalidateWidgetReason::Paint);
}

FSlateColor SRemoteExecutionStatusBadge::GetStatusColor() const
{
	return (CurrentStatus == ERemoteExecutionStatus::Connected)
		? FSlateColor(FLinearColor(0.0f, 0.8f, 0.1f))
		: FSlateColor(FLinearColor(0.9f, 0.1f, 0.1f));
}

FText SRemoteExecutionStatusBadge::GetTooltipText() const
{
	const FString StatusStr = (CurrentStatus == ERemoteExecutionStatus::Connected)
		? TEXT("Connected")
		: TEXT("Disconnected");

	const double LastTime = URemoteExecutionBridgeLibrary::GetLastHeartbeatTime();
	FString TimeStr;
	if (LastTime <= 0.0)
	{
		TimeStr = TEXT("never");
	}
	else
	{
		const double Elapsed = FPlatformTime::Seconds() - LastTime;
		TimeStr = FString::Printf(TEXT("%.1fs ago"), Elapsed);
	}

	const FString NodeId = URemoteExecutionBridgeLibrary::GetConnectedNodeId();
	const int32 Pid = URemoteExecutionBridgeLibrary::GetConnectedPid();
	const int32 Ppid = URemoteExecutionBridgeLibrary::GetConnectedPpid();
	const FString Cwd = URemoteExecutionBridgeLibrary::GetConnectedCwd();
	const FString StartTime = URemoteExecutionBridgeLibrary::GetConnectedStartTime();
	static const FString ConfigSection = TEXT("/Script/PythonScriptPlugin.PythonScriptPluginSettings");
	FString Endpoint;
	int32 Ttl = -1;
	GConfig->GetString(*ConfigSection, TEXT("RemoteExecutionMulticastGroupEndpoint"), Endpoint, GEngineIni);
	GConfig->GetInt(*ConfigSection, TEXT("RemoteExecutionMulticastTtl"), Ttl, GEngineIni);

	const FString TtlStr = (Ttl == 0)
		? TEXT("0 (local only)")
		: FString::Printf(TEXT("%d"), Ttl);

	const FString PidStr = (Pid == 0)
		? FString(TEXT("—"))
		: FString::Printf(TEXT("%d (parent %d)"), Pid, Ppid);

	const FString ProjectStr = Cwd.IsEmpty()
		? FString(TEXT("—"))
		: FPaths::GetPathLeaf(Cwd);

	const FString StartedStr = StartTime.IsEmpty() ? FString(TEXT("—")) : StartTime;

	const int32 ActiveSessions = URemoteExecutionBridgeLibrary::GetActiveSessions();
	const FString SessionsStr = FString::Printf(TEXT("%d"), FMath::Max(1, ActiveSessions));

	return FText::FromString(FString::Printf(
		TEXT("Remote Execution Bridge\nStatus: %s\nSessions: %s\nHeartbeat: %s\nNode: %s\nPID     : %s\nStarted : %s\nProject : %s\nEndpoint: %s\nTTL: %s"),
		*StatusStr,
		*SessionsStr,
		*TimeStr,
		*(NodeId.IsEmpty() ? FString(TEXT("—")) : NodeId),
		*PidStr,
		*StartedStr,
		*ProjectStr,
		*(Endpoint.IsEmpty() ? FString(TEXT("—")) : Endpoint),
		*TtlStr
	));
}
