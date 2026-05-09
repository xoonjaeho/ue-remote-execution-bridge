#pragma once

#include "CoreMinimal.h"
#include "Kismet/BlueprintFunctionLibrary.h"
#include "RemoteExecutionBridgeLibrary.generated.h"

UCLASS()
class REMOTEEXECUTIONBRIDGE_API URemoteExecutionBridgeLibrary : public UBlueprintFunctionLibrary
{
	GENERATED_BODY()

public:
	// Called by server.py after each successful ping. Thread-safe.
	UFUNCTION(BlueprintCallable, Category = "RemoteExecution")
	static void Heartbeat();

	// Called by server.py to push the currently connected node_id. Thread-safe.
	UFUNCTION(BlueprintCallable, Category = "RemoteExecution")
	static void SetConnectedNodeId(const FString& NodeId);

	// Called by server.py to push raw session fields of the connecting process. Thread-safe.
	UFUNCTION(BlueprintCallable, Category = "RemoteExecution")
	static void SetConnectedPid(int32 Pid);
	UFUNCTION(BlueprintCallable, Category = "RemoteExecution")
	static void SetConnectedPpid(int32 Ppid);
	UFUNCTION(BlueprintCallable, Category = "RemoteExecution")
	static void SetConnectedCwd(const FString& Cwd);
	UFUNCTION(BlueprintCallable, Category = "RemoteExecution")
	static void SetConnectedStartTime(const FString& StartTime);
	UFUNCTION(BlueprintCallable, Category = "RemoteExecution")
	static void SetConnectedParentName(const FString& ParentName);

	// Called by server.py heartbeat with the live session count. Thread-safe.
	UFUNCTION(BlueprintCallable, Category = "RemoteExecution")
	static void SetActiveSessions(int32 Count);

	static double GetLastHeartbeatTime();
	static FString GetConnectedNodeId();
	static int32   GetConnectedPid();
	static int32   GetConnectedPpid();
	static FString GetConnectedCwd();
	static FString GetConnectedStartTime();
	static FString GetConnectedParentName();
	static int32   GetActiveSessions();
	static void    ClearConnectedSession();
};
