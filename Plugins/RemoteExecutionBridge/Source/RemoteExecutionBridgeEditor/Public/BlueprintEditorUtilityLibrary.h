#pragma once

#include "CoreMinimal.h"
#include "Kismet/BlueprintFunctionLibrary.h"
#include "BlueprintEditorUtilityLibrary.generated.h"

class UBlueprint;
class UEdGraph;
class UEdGraphNode;

UCLASS()
class REMOTEEXECUTIONBRIDGEEDITOR_API UBlueprintEditorUtilityLibrary : public UBlueprintFunctionLibrary
{
	GENERATED_BODY()

public:
	// Returns all graphs (event graphs, function graphs, macro graphs, etc.) in Blueprint.
	UFUNCTION(BlueprintCallable, Category = "BlueprintEditorUtility")
	static TArray<UEdGraph*> GetBlueprintGraphs(UBlueprint* Blueprint);

	// Returns all nodes in Graph.
	UFUNCTION(BlueprintCallable, Category = "BlueprintEditorUtility")
	static TArray<UEdGraphNode*> GetGraphNodes(UEdGraph* Graph);

	// Removes a node from its graph, breaking all connected pin links.
	UFUNCTION(BlueprintCallable, Category = "BlueprintEditorUtility")
	static void DeleteBlueprintNode(UBlueprint* Blueprint, UEdGraphNode* Node);

	// Marks a blueprint as modified so the editor knows to recompile.
	UFUNCTION(BlueprintCallable, Category = "BlueprintEditorUtility")
	static void MarkBlueprintModified(UBlueprint* Blueprint);

	// Returns all K2Node_CallFunction nodes across every graph in Blueprint whose
	// function name matches FunctionName.
	UFUNCTION(BlueprintCallable, Category = "BlueprintEditorUtility")
	static TArray<UEdGraphNode*> FindNodesByFunctionName(UBlueprint* Blueprint, const FString& FunctionName);
};
