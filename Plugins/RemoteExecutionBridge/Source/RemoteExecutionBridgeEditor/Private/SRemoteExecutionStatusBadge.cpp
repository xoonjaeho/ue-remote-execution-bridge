#include "SRemoteExecutionStatusBadge.h"
#include "RemoteExecutionBridgeLibrary.h"
#include "Widgets/Text/STextBlock.h"
#include "HAL/PlatformProcess.h"
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
	FString HeartbeatStr;
	if (LastTime <= 0.0)
	{
		HeartbeatStr = TEXT("never");
	}
	else
	{
		const double Elapsed = FPlatformTime::Seconds() - LastTime;
		HeartbeatStr = FString::Printf(TEXT("%.1fs ago"), Elapsed);
	}

	static const FString ConfigSection = TEXT("/Script/PythonScriptPlugin.PythonScriptPluginSettings");
	FString Endpoint;
	int32 Ttl = -1;
	GConfig->GetString(*ConfigSection, TEXT("RemoteExecutionMulticastGroupEndpoint"), Endpoint, GEngineIni);
	GConfig->GetInt(*ConfigSection, TEXT("RemoteExecutionMulticastTtl"), Ttl, GEngineIni);

	const FString EndpointStr = Endpoint.IsEmpty() ? FString(TEXT("—")) : Endpoint;
	const FString TtlStr = (Ttl == 0)
		? FString(TEXT("0 (local only)"))
		: FString::Printf(TEXT("%d"), Ttl);

	const int32 ActiveSessions = URemoteExecutionBridgeLibrary::GetActiveSessions();
	const FString SessionsStr = (CurrentStatus == ERemoteExecutionStatus::Connected)
		? FString::Printf(TEXT("%d"), FMath::Max(1, ActiveSessions))
		: FString(TEXT("0"));

	const uint32 EditorPid = FPlatformProcess::GetCurrentProcessId();
	const FString EditorPidStr = FString::Printf(TEXT("%u"), EditorPid);
	const FString EditorPath = FPaths::ConvertRelativePathToFull(FPaths::ProjectDir());

	const bool bConnected = (CurrentStatus == ERemoteExecutionStatus::Connected);
	const FString NodeId = bConnected ? URemoteExecutionBridgeLibrary::GetConnectedNodeId() : FString();
	const FString NodeStr = NodeId.IsEmpty() ? FString(TEXT("—")) : NodeId;

	const int32 McpPid = bConnected ? URemoteExecutionBridgeLibrary::GetConnectedPid() : 0;
	const int32 McpPpid = bConnected ? URemoteExecutionBridgeLibrary::GetConnectedPpid() : 0;
	const FString McpParentName = bConnected ? URemoteExecutionBridgeLibrary::GetConnectedParentName() : FString();
	FString McpPidStr;
	if (McpPid == 0)
	{
		McpPidStr = TEXT("—");
	}
	else
	{
		const FString ParentNameStr = McpParentName.IsEmpty() ? FString(TEXT("?")) : McpParentName;
		McpPidStr = FString::Printf(TEXT("%d (parent : %d, %s)"), McpPid, McpPpid, *ParentNameStr);
	}

	const FString StartTime = bConnected ? URemoteExecutionBridgeLibrary::GetConnectedStartTime() : FString();
	const FString StartedStr = StartTime.IsEmpty() ? FString(TEXT("—")) : StartTime;

	const FString Cwd = bConnected ? URemoteExecutionBridgeLibrary::GetConnectedCwd() : FString();
	const FString ProjectStr = Cwd.IsEmpty() ? FString(TEXT("—")) : FPaths::GetPathLeaf(Cwd);

	return FText::FromString(FString::Printf(
		TEXT("Remote Execution Bridge\n")
		TEXT("Status: %s\n")
		TEXT("Sessions: %s\n")
		TEXT("Heartbeat: %s\n")
		TEXT("Endpoint: %s\n")
		TEXT("TTL: %s\n")
		TEXT("\n")
		TEXT("UE\n")
		TEXT("PID: %s\n")
		TEXT("Node: %s\n")
		TEXT("Path : %s\n")
		TEXT("\n")
		TEXT("MCP\n")
		TEXT("PID: %s\n")
		TEXT("Started: %s\n")
		TEXT("Project: %s"),
		*StatusStr,
		*SessionsStr,
		*HeartbeatStr,
		*EndpointStr,
		*TtlStr,
		*EditorPidStr,
		*NodeStr,
		*EditorPath,
		*McpPidStr,
		*StartedStr,
		*ProjectStr
	));
}
