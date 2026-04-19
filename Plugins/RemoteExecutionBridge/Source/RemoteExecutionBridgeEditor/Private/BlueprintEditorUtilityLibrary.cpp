#include "BlueprintEditorUtilityLibrary.h"
#include "EdGraph/EdGraph.h"
#include "EdGraph/EdGraphNode.h"
#include "Engine/Blueprint.h"
#include "Kismet2/BlueprintEditorUtils.h"
#include "K2Node_CallFunction.h"

TArray<UEdGraph*> UBlueprintEditorUtilityLibrary::GetBlueprintGraphs(UBlueprint* Blueprint)
{
	TArray<UEdGraph*> AllGraphs;
	if (!Blueprint)
	{
		return AllGraphs;
	}
	Blueprint->GetAllGraphs(AllGraphs);
	return AllGraphs;
}

TArray<UEdGraphNode*> UBlueprintEditorUtilityLibrary::GetGraphNodes(UEdGraph* Graph)
{
	TArray<UEdGraphNode*> Result;
	if (!Graph)
	{
		return Result;
	}
	for (UEdGraphNode* Node : Graph->Nodes)
	{
		if (Node)
		{
			Result.Add(Node);
		}
	}
	return Result;
}

void UBlueprintEditorUtilityLibrary::DeleteBlueprintNode(UBlueprint* Blueprint, UEdGraphNode* Node)
{
	if (!Blueprint || !Node)
	{
		return;
	}

	FBlueprintEditorUtils::RemoveNode(Blueprint, Node, /*bDontRecompile=*/true);
}

void UBlueprintEditorUtilityLibrary::MarkBlueprintModified(UBlueprint* Blueprint)
{
	if (!Blueprint)
	{
		return;
	}

	FBlueprintEditorUtils::MarkBlueprintAsModified(Blueprint);
}

TArray<UEdGraphNode*> UBlueprintEditorUtilityLibrary::FindNodesByFunctionName(UBlueprint* Blueprint, const FString& FunctionName)
{
	TArray<UEdGraphNode*> Result;
	if (!Blueprint)
	{
		return Result;
	}

	TArray<UEdGraph*> AllGraphs;
	Blueprint->GetAllGraphs(AllGraphs);

	const FName TargetName(*FunctionName);

	for (UEdGraph* Graph : AllGraphs)
	{
		if (!Graph)
		{
			continue;
		}

		for (UEdGraphNode* Node : Graph->Nodes)
		{
			UK2Node_CallFunction* CallNode = Cast<UK2Node_CallFunction>(Node);
			if (CallNode && CallNode->GetFunctionName() == TargetName)
			{
				Result.Add(Node);
			}
		}
	}

	return Result;
}
