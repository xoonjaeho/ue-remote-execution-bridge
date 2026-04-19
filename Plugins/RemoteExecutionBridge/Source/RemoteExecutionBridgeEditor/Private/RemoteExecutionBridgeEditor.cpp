#include "CoreMinimal.h"
#include "Modules/ModuleManager.h"
#include "ToolMenus.h"
#include "RemoteExecutionStatusMonitor.h"
#include "SRemoteExecutionStatusBadge.h"

DEFINE_LOG_CATEGORY_STATIC(LogRemoteExecutionBridgeEditor, Log, All);

class FRemoteExecutionBridgeEditorModule : public IModuleInterface
{
public:
	virtual void StartupModule() override
	{
		UE_LOG(LogRemoteExecutionBridgeEditor, Log, TEXT("StartupModule"));

		Monitor = MakeShared<FRemoteExecutionStatusMonitor>();

		UToolMenus::RegisterStartupCallback(
			FSimpleMulticastDelegate::FDelegate::CreateRaw(this, &FRemoteExecutionBridgeEditorModule::RegisterMenuExtensions)
		);
	}

	virtual void ShutdownModule() override
	{
		UToolMenus::UnRegisterStartupCallback(this);

		if (UObjectInitialized())
		{
			UToolMenus::Get()->UnregisterOwner(TEXT("RemoteExecutionBridge"));
		}

		Monitor.Reset();
	}

private:
	void RegisterMenuExtensions()
	{
		FToolMenuOwnerScoped OwnerScoped(TEXT("RemoteExecutionBridge"));

		UToolMenu* Menu = UToolMenus::Get()->ExtendMenu("LevelEditor.LevelEditorToolBar.User");
		if (!Menu)
		{
			UE_LOG(LogRemoteExecutionBridgeEditor, Warning,
				TEXT("LevelEditorToolBar.User not found. Run 'ToolMenus.Edit' in the console to inspect menu names."));
			return;
		}

		FToolMenuSection& Section = Menu->AddSection(
			"RemoteExecutionStatus",
			FText::GetEmpty(),
			FToolMenuInsert(NAME_None, EToolMenuInsertType::Last)
		);

		FToolMenuEntry BadgeEntry = FToolMenuEntry::InitWidget(
			"RemoteExecutionBadge",
			SNew(SRemoteExecutionStatusBadge)
			.Monitor(Monitor),
			FText::GetEmpty(),
			true
		);
		BadgeEntry.WidgetData.StyleParams.SizeRule = FSizeParam::SizeRule_Stretch;
		BadgeEntry.WidgetData.StyleParams.FillSize = 1.0f;
		Section.AddEntry(BadgeEntry);
	}

	TSharedPtr<FRemoteExecutionStatusMonitor> Monitor;
};

IMPLEMENT_MODULE(FRemoteExecutionBridgeEditorModule, RemoteExecutionBridgeEditor)
