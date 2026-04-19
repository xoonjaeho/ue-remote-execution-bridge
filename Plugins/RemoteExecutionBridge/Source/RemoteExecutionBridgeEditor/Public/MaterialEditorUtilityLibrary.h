#pragma once

#include "CoreMinimal.h"
#include "Kismet/BlueprintFunctionLibrary.h"
#include "MaterialEditorUtilityLibrary.generated.h"

class UMaterial;
class UMaterialExpression;

UCLASS()
class REMOTEEXECUTIONBRIDGEEDITOR_API UMaterialEditorUtilityLibrary : public UBlueprintFunctionLibrary
{
	GENERATED_BODY()

public:
	// Returns all expression nodes in the material's graph.
	UFUNCTION(BlueprintCallable, Category = "MaterialEditorUtility")
	static TArray<UMaterialExpression*> GetMaterialExpressions(UMaterial* Material);
};
