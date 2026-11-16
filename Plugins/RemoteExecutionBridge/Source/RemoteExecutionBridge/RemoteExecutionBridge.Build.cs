using UnrealBuildTool;

public class RemoteExecutionBridge : ModuleRules
{
	public RemoteExecutionBridge(ReadOnlyTargetRules Target) : base(Target)
	{
		PCHUsage = ModuleRules.PCHUsageMode.UseExplicitOrSharedPCHs;

		PublicDependencyModuleNames.AddRange(new string[]
		{
			"Core",
			"CoreUObject",
			"Engine",
			"InputCore",
			"Slate",
			"SlateCore",
			"UMG",
		});

		PrivateDependencyModuleNames.AddRange(new string[]
		{
			"AutomationDriver",
			"Json",
		});
	}
}
