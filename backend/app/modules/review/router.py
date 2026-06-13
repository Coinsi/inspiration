"""review 路由(对照 04 §3.9)。"""
import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import ProjectContext, get_project_context, require_action
from app.modules.review import schemas, service

router = APIRouter(prefix="/projects/{project_id}", tags=["review"])
SUBMIT = require_action("review.submit")
DECIDE = require_action("review.decide")


@router.post("/reviews", response_model=schemas.ReviewOut)
def create_review(data: schemas.ReviewIn, ctx: ProjectContext = Depends(SUBMIT), db: Session = Depends(get_db)):
    return service.create_review(db, ctx, data)


@router.get("/reviews", response_model=list[schemas.ReviewOut])
def list_reviews(
    target_type: str = Query("shot"),
    target_id: uuid.UUID = Query(...),
    ctx: ProjectContext = Depends(get_project_context),
    db: Session = Depends(get_db),
):
    return service.list_reviews(db, ctx.project.id, target_type, target_id)


@router.post("/reviews/{review_id}/annotations", response_model=schemas.AnnotationOut)
def add_annotation(
    review_id: uuid.UUID, data: schemas.AnnotationIn,
    ctx: ProjectContext = Depends(SUBMIT), db: Session = Depends(get_db),
):
    return service.add_annotation(db, ctx, review_id, data)


@router.get("/reviews/{review_id}/annotations", response_model=list[schemas.AnnotationOut])
def list_annotations(review_id: uuid.UUID, ctx: ProjectContext = Depends(get_project_context), db: Session = Depends(get_db)):
    return service.list_annotations(db, ctx.project.id, review_id)


@router.post("/reviews/{review_id}/decide", response_model=schemas.ReviewOut)
def decide(
    review_id: uuid.UUID, data: schemas.DecideIn,
    ctx: ProjectContext = Depends(DECIDE), db: Session = Depends(get_db),
):
    return service.decide(db, ctx, review_id, data)
