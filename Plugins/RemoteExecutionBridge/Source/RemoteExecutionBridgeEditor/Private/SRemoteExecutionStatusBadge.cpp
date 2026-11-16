#include "SRemoteExecutionStatusBadge.h"
#include "RemoteExecutionBridgeLibrary.h"
#include "Widgets/Layout/SBox.h"
#include "Widgets/Text/STextBlock.h"
#include "Misc/ConfigCacheIni.h"
#include "Misc/Paths.h"
#include "HAL/PlatformProcess.h"

namespace
{
	// Extract only the image name from the PID (GetApplicationName returns the full path). Empty string if the process exited or access was denied -> "unknown"
	FString ResolveProcessName(uint32 processId)
	{
		const FString image = FPaths::GetCleanFilename(FPlatformProcess::GetApplicationName(processId));
		return image.IsEmpty() ? FString(TEXT("unknown")) : image;
	}

	[[nodiscard]] FString FormatHeartbeatAge(double lastHeartbeatTime)
	{
		if (lastHeartbeatTime <= 0.0)
		{
			return TEXT("never");
		}
		return FString::Printf(TEXT("%.1fs ago"), FPlatformTime::Seconds() - lastHeartbeatTime);
	}

	[[nodiscard]] FString FormatConnected(bool bConnected)
	{
		return bConnected ? TEXT("Connected") : TEXT("Disconnected");
	}
}

void SRemoteExecutionStatusBadge::Construct(const FArguments& InArgs)
{
	Monitor = InArgs._Monitor;

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

void SRemoteExecutionStatusBadge::OnStatusChanged()
{
	Invalidate(EInvalidateWidgetReason::Paint);
}

FSlateColor SRemoteExecutionStatusBadge::GetStatusColor() const
{
	if (FRemoteExecutionStatusMonitor::IsBridgeConnected())
	{
		return FSlateColor(FLinearColor(0.0f, 0.8f, 0.1f));
	}
	return FSlateColor(FLinearColor(0.9f, 0.1f, 0.1f));
}

FText SRemoteExecutionStatusBadge::GetTooltipText() const
{
	// Judge directly from heartbeat age (avoids depending on delegate chain timing of the ticker-cached state)
	const bool bConnected = FRemoteExecutionStatusMonitor::IsBridgeConnected();
	const FString StatusStr = FormatConnected(bConnected);

	const double LastTime = URemoteExecutionBridgeLibrary::GetLastHeartbeatTime();
	const FString TimeStr = FormatHeartbeatAge(LastTime);

	static const FString ConfigSection = TEXT("/Script/PythonScriptPlugin.PythonScriptPluginSettings");
	FString Endpoint;
	int32 Ttl = -1;
	GConfig->GetString(*ConfigSection, TEXT("RemoteExecutionMulticastGroupEndpoint"), Endpoint, GEngineIni);
	GConfig->GetInt(*ConfigSection, TEXT("RemoteExecutionMulticastTtl"), Ttl, GEngineIni);

	const FString TtlStr = (Ttl == 0)
		? TEXT("0 (local only)")
		: FString::Printf(TEXT("%d"), Ttl);

	static const FString Unknown = TEXT("unknown");

	FString NodeIdStr = Unknown;
	FString McpPathStr = Unknown;
	FString PidStr = Unknown;
	FString PpidStr = Unknown;
	FString StartedStr = Unknown;
	FString ProjectStr = Unknown;
	FString BranchStr = Unknown;
	FString SessionsStr = TEXT("0");

	if (bConnected)
	{
		const FString NodeId = URemoteExecutionBridgeLibrary::GetConnectedNodeId();
		const int32 Pid = URemoteExecutionBridgeLibrary::GetConnectedPid();
		const int32 Ppid = URemoteExecutionBridgeLibrary::GetConnectedPpid();
		const FString Cwd = URemoteExecutionBridgeLibrary::GetConnectedCwd();
		const FString StartTime = URemoteExecutionBridgeLibrary::GetConnectedStartTime();
		const FString BranchName = URemoteExecutionBridgeLibrary::GetConnectedBranchName();
		const int32 ActiveSessions = URemoteExecutionBridgeLibrary::GetActiveSessions();

		NodeIdStr = NodeId.IsEmpty() ? Unknown : NodeId;
		McpPathStr = Cwd.IsEmpty() ? Unknown : Cwd;
		// The image name is resolved on the editor side directly from the PID - no MCP protocol change needed
		PidStr = (Pid == 0) ? Unknown : FString::Printf(TEXT("%d (%s)"), Pid, *ResolveProcessName(Pid));
		PpidStr = (Ppid == 0) ? Unknown : FString::Printf(TEXT("%d (%s)"), Ppid, *ResolveProcessName(Ppid));
		StartedStr = StartTime.IsEmpty() ? Unknown : StartTime;
		ProjectStr = Cwd.IsEmpty() ? Unknown : FPaths::GetPathLeaf(Cwd);
		BranchStr = BranchName.IsEmpty() ? Unknown : BranchName;
		SessionsStr = FString::Printf(TEXT("%d"), FMath::Max(1, ActiveSessions));
	}

	const FString EditorPidStr = FString::Printf(TEXT("%d"), FPlatformProcess::GetCurrentProcessId());
	// UE's own path - always shown regardless of connection (MCP Path is a separate value pushed on connect)
	const FString UEPathStr = FPaths::ConvertRelativePathToFull(FPaths::GetProjectFilePath());

	return FText::FromString(FString::Printf(
		TEXT("Remote Execution Bridge\nStatus: %s\nSessions: %s\nHeartbeat: %s\nEndpoint: %s\nTTL: %s\nUE\nPID: %s\nNode: %s\nPath: %s\nMCP\nPID: %s\nPPID: %s\nPath: %s\nStarted: %s\nProject: %s\nBranch: %s"),
		*StatusStr,
		*SessionsStr,
		*TimeStr,
		*(Endpoint.IsEmpty() ? Unknown : Endpoint),
		*TtlStr,
		*EditorPidStr,
		*NodeIdStr,
		*UEPathStr,
		*PidStr,
		*PpidStr,
		*McpPathStr,
		*StartedStr,
		*ProjectStr,
		*BranchStr
	));
}
