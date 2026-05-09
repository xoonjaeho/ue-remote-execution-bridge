#include "RemoteExecutionBridgeLibrary.h"
#include "Misc/ScopeLock.h"
#include <atomic>

static std::atomic<double> GLastHeartbeatTime{0.0};
static FCriticalSection GNodeIdLock;
static FString GConnectedNodeId;
static std::atomic<int32> GActiveSessions{0};
static std::atomic<int32> GPid{0};
static std::atomic<int32> GPpid{0};
static FCriticalSection GCwdLock;
static FString GCwd;
static FCriticalSection GConnectedStartTimeLock;
static FString GConnectedStartTime;
static FCriticalSection GConnectedParentNameLock;
static FString GConnectedParentName;

void URemoteExecutionBridgeLibrary::Heartbeat()
{
	GLastHeartbeatTime.store(FPlatformTime::Seconds(), std::memory_order_relaxed);
}

void URemoteExecutionBridgeLibrary::SetConnectedNodeId(const FString& NodeId)
{
	FScopeLock Lock(&GNodeIdLock);
	GConnectedNodeId = NodeId;
}

double URemoteExecutionBridgeLibrary::GetLastHeartbeatTime()
{
	return GLastHeartbeatTime.load(std::memory_order_relaxed);
}

FString URemoteExecutionBridgeLibrary::GetConnectedNodeId()
{
	FScopeLock Lock(&GNodeIdLock);
	return GConnectedNodeId;
}

void URemoteExecutionBridgeLibrary::SetActiveSessions(int32 Count)
{
	GActiveSessions.store(Count, std::memory_order_relaxed);
}

int32 URemoteExecutionBridgeLibrary::GetActiveSessions()
{
	return GActiveSessions.load(std::memory_order_relaxed);
}

void URemoteExecutionBridgeLibrary::SetConnectedPid(int32 Pid)
{
	GPid.store(Pid, std::memory_order_relaxed);
}

void URemoteExecutionBridgeLibrary::SetConnectedPpid(int32 Ppid)
{
	GPpid.store(Ppid, std::memory_order_relaxed);
}

void URemoteExecutionBridgeLibrary::SetConnectedCwd(const FString& Cwd)
{
	FScopeLock Lock(&GCwdLock);
	GCwd = Cwd;
}

void URemoteExecutionBridgeLibrary::SetConnectedStartTime(const FString& StartTime)
{
	FScopeLock Lock(&GConnectedStartTimeLock);
	GConnectedStartTime = StartTime;
}

void URemoteExecutionBridgeLibrary::SetConnectedParentName(const FString& ParentName)
{
	FScopeLock Lock(&GConnectedParentNameLock);
	GConnectedParentName = ParentName;
}

int32 URemoteExecutionBridgeLibrary::GetConnectedPid()
{
	return GPid.load(std::memory_order_relaxed);
}

int32 URemoteExecutionBridgeLibrary::GetConnectedPpid()
{
	return GPpid.load(std::memory_order_relaxed);
}

FString URemoteExecutionBridgeLibrary::GetConnectedCwd()
{
	FScopeLock Lock(&GCwdLock);
	return GCwd;
}

FString URemoteExecutionBridgeLibrary::GetConnectedStartTime()
{
	FScopeLock Lock(&GConnectedStartTimeLock);
	return GConnectedStartTime;
}

FString URemoteExecutionBridgeLibrary::GetConnectedParentName()
{
	FScopeLock Lock(&GConnectedParentNameLock);
	return GConnectedParentName;
}

void URemoteExecutionBridgeLibrary::ClearConnectedSession()
{
	{
		FScopeLock Lock(&GNodeIdLock);
		GConnectedNodeId.Empty();
	}
	GActiveSessions.store(0, std::memory_order_relaxed);
	GPid.store(0, std::memory_order_relaxed);
	GPpid.store(0, std::memory_order_relaxed);
	{
		FScopeLock Lock(&GCwdLock);
		GCwd.Empty();
	}
	{
		FScopeLock Lock(&GConnectedStartTimeLock);
		GConnectedStartTime.Empty();
	}
	{
		FScopeLock Lock(&GConnectedParentNameLock);
		GConnectedParentName.Empty();
	}
}
