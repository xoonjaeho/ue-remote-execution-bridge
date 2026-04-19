#include "MaterialEditorUtilityLibrary.h"
#include "Materials/Material.h"
#include "Materials/MaterialExpression.h"

TArray<UMaterialExpression*> UMaterialEditorUtilityLibrary::GetMaterialExpressions(UMaterial* Material)
{
	TArray<UMaterialExpression*> Result;
	if (!Material)
	{
		return Result;
	}
	for (UMaterialExpression* Expr : Material->GetExpressions())
	{
		if (Expr)
		{
			Result.Add(Expr);
		}
	}
	return Result;
}
