import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.adapters.contracts import GenerationRequest
from app.core.database import get_db
from app.core.deps import ProjectContext, require_action
from app.modules.generation import model_catalog as catalog


def create_router(prefix, manage):
    router = APIRouter(prefix=prefix, tags=["creative-models"])
    MANAGE = manage

    @router.get("")
    def listing(
        ctx: ProjectContext = Depends(MANAGE), db: Session = Depends(get_db, scope="function")
    ):
        return [catalog.public(db, row, True) for row in catalog.rows(db, ctx.project.id)]

    @router.post("")
    def create(
        data: catalog.CreativeModelIn,
        ctx: ProjectContext = Depends(MANAGE),
        db: Session = Depends(get_db, scope="function"),
    ):
        return catalog.public(db, catalog.save(db, ctx, data), True)

    @router.post("/import-profiles")
    def adopt(
        ctx: ProjectContext = Depends(MANAGE), db: Session = Depends(get_db, scope="function")
    ):
        return catalog.import_profiles(db, ctx)

    @router.post("/select-default")
    def select_default(
        data: catalog.DefaultModelIn,
        ctx: ProjectContext = Depends(MANAGE),
        db: Session = Depends(get_db, scope="function"),
    ):
        return catalog.public(db, catalog.select_default(db, ctx, data), True)

    @router.put("/{model_id}")
    def update(
        model_id: uuid.UUID,
        data: catalog.CreativeModelIn,
        ctx: ProjectContext = Depends(MANAGE),
        db: Session = Depends(get_db, scope="function"),
    ):
        return catalog.public(db, catalog.save(db, ctx, data, model_id), True)

    @router.post("/{model_id}/simulate")
    def simulate(
        model_id: uuid.UUID,
        data: GenerationRequest,
        ctx: ProjectContext = Depends(MANAGE),
        db: Session = Depends(get_db, scope="function"),
    ):
        plan = catalog.plan(db, catalog.get_model(db, ctx.project.id, model_id), data)
        return {
            "estimate": catalog.estimate_plan(plan),
            "candidates": [
                {
                    k: c[k]
                    for k in (
                        "profile_id",
                        "channel_name",
                        "model_name",
                        "priority",
                        "weight",
                        "cost_points",
                    )
                }
                for c in plan["candidates"]
            ],
            "rejected": plan["rejected"],
            "message": "仅检查配置，不会请求供应商或产生生成费用；实际选择按当时可用线路计算",
        }

    return router


router = create_router("/projects/{project_id}/creative-models", require_action("provider.manage"))
