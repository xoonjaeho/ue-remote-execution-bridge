#include "RemoteExecutionBridgeAutomationDriverLibrary.h"

#include "Async/AsyncResult.h"
#include "Containers/Ticker.h"
#include "Dom/JsonObject.h"
#include "Dom/JsonValue.h"
#include "Engine/Engine.h"
#include "Engine/EngineTypes.h"
#include "IAutomationDriver.h"
#include "IAutomationDriverModule.h"
#include "IDriverSequence.h"
#include "InputCoreTypes.h"
#include "Misc/Guid.h"
#include "Misc/Timespan.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"

namespace
{
	bool IsPieRunning()
	{
		if (nullptr == GEngine)
		{
			return false;
		}
		for (FWorldContext const& context : GEngine->GetWorldContexts())
		{
			if (EWorldType::PIE == context.WorldType)
			{
				return true;
			}
		}
		return false;
	}

	// Reference count - managed per generation so that one session's Disable/timeout
	// cannot release an Enable share registered by another session.
	FCriticalSection GEnableLock;
	TSet<uint64> GLiveEnableGenerations;
	std::atomic<uint64> GNextEnableGeneration{0};

	void ReleaseAutomationDriverGeneration(uint64 generation)
	{
		bool bShouldDisable = false;
		{
			FScopeLock lock(&GEnableLock);
			if (!GLiveEnableGenerations.Remove(generation))
			{
				return;	// Already released - prevents double release when a normal Disable races the safety timeout
			}
			bShouldDisable = GLiveEnableGenerations.IsEmpty();
		}
		if (bShouldDisable && IAutomationDriverModule::Get().IsEnabled())
		{
			IAutomationDriverModule::Get().Disable();
		}
	}

	struct FPendingDriverSequence
	{
		TSharedPtr<IAsyncAutomationDriver, ESPMode::ThreadSafe> Driver;
		TSharedPtr<IAsyncDriverSequence, ESPMode::ThreadSafe> Sequence;
		TAsyncResult<bool> Result;
	};

	FCriticalSection GPendingSequenceLock;
	TMap<FGuid, FPendingDriverSequence> GPendingSequences;

	bool ParseKeyField(FJsonObject const& stepObject, TCHAR const* fieldName, FKey& outKey)
	{
		FString keyName;
		if (!stepObject.TryGetStringField(fieldName, keyName))
		{
			return false;
		}
		outKey = FKey(*keyName);
		return true;
	}

	// Apply one object of the StepsJson array to the queue. Returns false on failure - the caller discards the whole sequence.
	bool AppendStep(IAsyncActionSequence& actions, TSharedPtr<FJsonObject> const& stepObject)
	{
		if (!stepObject.IsValid())
		{
			return false;
		}
		FString action;
		if (!stepObject->TryGetStringField(TEXT("action"), action))
		{
			return false;
		}

		if (TEXT("press") == action)
		{
			FKey key;
			if (!ParseKeyField(*stepObject, TEXT("key"), key))
			{
				return false;
			}
			actions.Press(key);
			return true;
		}
		if (TEXT("release") == action)
		{
			FKey key;
			if (!ParseKeyField(*stepObject, TEXT("key"), key))
			{
				return false;
			}
			actions.Release(key);
			return true;
		}
		if (TEXT("press_chord") == action)
		{
			TArray<TSharedPtr<FJsonValue>> const* keysArray = nullptr;
			if (!stepObject->TryGetArrayField(TEXT("keys"), keysArray) || nullptr == keysArray)
			{
				return false;
			}
			if (2 == keysArray->Num())
			{
				actions.PressChord(FKey(*(*keysArray)[0]->AsString()), FKey(*(*keysArray)[1]->AsString()));
				return true;
			}
			if (3 == keysArray->Num())
			{
				actions.PressChord(FKey(*(*keysArray)[0]->AsString()), FKey(*(*keysArray)[1]->AsString()), FKey(*(*keysArray)[2]->AsString()));
				return true;
			}
			return false;	// PressChord supports only 2-3 keys
		}
		if (TEXT("type") == action)
		{
			FString text;
			if (!stepObject->TryGetStringField(TEXT("text"), text))
			{
				return false;
			}
			actions.Type(text);
			return true;
		}
		if (TEXT("wait") == action)
		{
			double seconds = 0.0;
			if (!stepObject->TryGetNumberField(TEXT("seconds"), seconds))
			{
				return false;
			}
			actions.Wait(FTimespan::FromSeconds(seconds));
			return true;
		}
		return false;	// Unknown action
	}
}

int64 URemoteExecutionBridgeAutomationDriverLibrary::EnableAutomationDriver(float AutoDisableSeconds)
{
	uint64 const generation = GNextEnableGeneration.fetch_add(1, std::memory_order_relaxed) + 1;
	{
		FScopeLock lock(&GEnableLock);
		bool const bWasEmpty = GLiveEnableGenerations.IsEmpty();
		GLiveEnableGenerations.Add(generation);
		if (bWasEmpty)
		{
			IAutomationDriverModule::Get().Enable();
		}
	}

	FTSTicker::GetCoreTicker().AddTicker(TEXT("RemoteExecutionBridge_AutomationDriverSafety"), AutoDisableSeconds,
		[generation](float) -> bool
		{
			ReleaseAutomationDriverGeneration(generation);
			return false;	// One-shot - no repeat needed
		});

	return static_cast<int64>(generation);
}

bool URemoteExecutionBridgeAutomationDriverLibrary::DisableAutomationDriver(int64 Generation)
{
	uint64 generationToRelease = 0;
	{
		FScopeLock lock(&GEnableLock);
		if (GLiveEnableGenerations.IsEmpty())
		{
			return false;	// Called without a matching Enable
		}
		if (0 != Generation)
		{
			uint64 const requested = static_cast<uint64>(Generation);
			if (!GLiveEnableGenerations.Contains(requested))
			{
				return false;	// Already released or nonexistent generation - includes the case where another call took it first
			}
			generationToRelease = requested;
		}
		else
		{
			generationToRelease = *GLiveEnableGenerations.CreateConstIterator();	// No generation specified - release an arbitrary one (legacy call compatibility; the count stays accurate)
		}
	}
	ReleaseAutomationDriverGeneration(generationToRelease);
	return true;
}

FString URemoteExecutionBridgeAutomationDriverLibrary::RunDriverSequence(FString const& StepsJson)
{
	if (!IsPieRunning())
	{
		return FString();	// No PIE - explicit failure
	}
	if (!IAutomationDriverModule::Get().IsEnabled())
	{
		// CreateAsyncDriver() calls AutomatedApplication.ToSharedRef() internally - in the Disabled state (nullptr),
		// the IsValid() assert crashes the whole editor (observed). Must explicitly fail beforehand.
		return FString();
	}

	TArray<TSharedPtr<FJsonValue>> stepsArray;
	TSharedRef<TJsonReader<>> const jsonReader = TJsonReaderFactory<>::Create(StepsJson);
	if (!FJsonSerializer::Deserialize(jsonReader, stepsArray))
	{
		return FString();
	}

	TSharedRef<IAsyncAutomationDriver, ESPMode::ThreadSafe> const driver = IAutomationDriverModule::Get().CreateAsyncDriver();
	TSharedRef<IAsyncDriverSequence, ESPMode::ThreadSafe> const sequence = driver->CreateSequence();

	IAsyncActionSequence& actions = sequence->Actions();
	for (TSharedPtr<FJsonValue> const& stepValue : stepsArray)
	{
		TSharedPtr<FJsonObject> const stepObject = stepValue.IsValid() ? stepValue->AsObject() : nullptr;
		if (!AppendStep(actions, stepObject))
		{
			return FString();	// Step parsing failed - a partially queued sequence is discarded without calling Perform
		}
	}

	// Perform() returns a TAsyncResult<bool> immediately - actual execution is advanced by CoreTicker over later frames (no game thread blocking)
	TAsyncResult<bool> result = sequence->Perform();

	FGuid const handle = FGuid::NewGuid();
	{
		FScopeLock lock(&GPendingSequenceLock);
		GPendingSequences.Add(handle, FPendingDriverSequence{driver, sequence, MoveTemp(result)});
	}

	return handle.ToString();
}

bool URemoteExecutionBridgeAutomationDriverLibrary::PollDriverSequence(FString const& Handle, bool& bOutDone, bool& bOutSuccess)
{
	bOutDone = false;
	bOutSuccess = false;

	FGuid handleGuid;
	if (!FGuid::Parse(Handle, handleGuid))
	{
		return false;
	}

	FScopeLock lock(&GPendingSequenceLock);
	FPendingDriverSequence* const pending = GPendingSequences.Find(handleGuid);
	if (nullptr == pending)
	{
		return false;	// Unknown handle - already consumed by poll or invalid
	}

	TFuture<bool> const& future = pending->Result.GetFuture();
	if (!future.IsReady())
	{
		return true;	// Still in progress - normal state with bOutDone=false
	}

	bOutDone = true;
	bOutSuccess = future.Get();
	GPendingSequences.Remove(handleGuid);	// Remove immediately on completion - prevents unbounded growth per session
	return true;
}
