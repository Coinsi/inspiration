"""参考图一致性:提示词里只带名字,真正的一致性靠生成期参考图(M4)。"""
from app.adapters.contracts import ConsistencyContext, ConsistencyStrategy, consistency_registry


@consistency_registry.register("reference")
class ReferenceConsistency(ConsistencyStrategy):
    name = "reference"

    def prompt_parts(self, ctx: ConsistencyContext) -> list[str]:
        return [ctx.asset_name]

    def generation_extras(self, ctx: ConsistencyContext) -> dict:
        # M4 生成时把参考图集喂给供应商
        return {"reference_set": ctx.reference_set or {}}
