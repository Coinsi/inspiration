"""review 业务:评审、批注、返修与状态联动。"""
import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core import audit
from app.core.deps import ProjectContext
from app.core.errors import Conflict, NotFound
from app.models.enums import ProductionStatus, ReviewStatus
from app.models.review import Annotation, Review, Revision
from app.models.shot import Shot
from app.modules.review import schemas
from app.modules.shot import service as shot_service


def create_review(db: Session, ctx: ProjectContext, data: schemas.ReviewIn) -> Review:
    # 轮次 = 同 target 已有评审数 + 1
    rounds = db.scalar(
        select(func.count()).where(
            Review.target_type == data.target_type, Review.target_id == data.target_id
        )
    )
    r = Review(
        project_id=ctx.project.id, target_type=data.target_type, target_id=data.target_id,
        generation_id=data.generation_id, status=ReviewStatus.pending, round_no=(rounds or 0) + 1,
        requested_by=ctx.user.id,
    )
    db.add(r)
    db.flush()
    audit.record(db, action="review.create", user_id=ctx.user.id, project_id=ctx.project.id,
                 target_type=data.target_type, target_id=data.target_id, detail={"review": str(r.id)})
    return r


def _get_review(db: Session, project_id: uuid.UUID, review_id: uuid.UUID) -> Review:
    r = db.get(Review, review_id)
    if r is None or r.project_id != project_id:
        raise NotFound("评审不存在")
    return r


def add_annotation(db: Session, ctx: ProjectContext, review_id: uuid.UUID, data: schemas.AnnotationIn) -> Annotation:
    r = _get_review(db, ctx.project.id, review_id)
    a = Annotation(
        review_id=r.id, kind=data.kind, geometry=data.geometry,
        timecode_ms=data.timecode_ms, comment=data.comment, author_id=ctx.user.id,
    )
    db.add(a)
    db.flush()
    return a


def list_annotations(db: Session, project_id: uuid.UUID, review_id: uuid.UUID) -> list[Annotation]:
    _get_review(db, project_id, review_id)
    return list(db.scalars(select(Annotation).where(Annotation.review_id == review_id).order_by(Annotation.created_at)))


def list_reviews(db: Session, project_id: uuid.UUID, target_type: str, target_id: uuid.UUID) -> list[Review]:
    return list(
        db.scalars(
            select(Review).where(
                Review.project_id == project_id,
                Review.target_type == target_type,
                Review.target_id == target_id,
            ).order_by(Review.round_no)
        )
    )


def decide(db: Session, ctx: ProjectContext, review_id: uuid.UUID, data: schemas.DecideIn) -> Review:
    r = _get_review(db, ctx.project.id, review_id)
    if r.status != ReviewStatus.pending:
        raise Conflict("该评审已结办")
    r.decision = data.note
    r.resolved_at = func.now()
    r.reviewer_id = ctx.user.id

    # 与镜头状态联动(target 为 shot 时)
    if r.target_type == "shot":
        shot = db.get(Shot, r.target_id)
        if shot is not None:
            cur = shot.production_status
            cur = cur if isinstance(cur, ProductionStatus) else ProductionStatus(cur)
            if data.approve:
                if cur == ProductionStatus.pending_review:
                    shot_service.transition(db, ctx, shot.id, ProductionStatus.approved)
            else:
                if cur == ProductionStatus.pending_review:
                    shot_service.transition(db, ctx, shot.id, ProductionStatus.revising)
                db.add(Revision(review_id=r.id, shot_id=shot.id, round_no=r.round_no, note=data.note))

    r.status = ReviewStatus.approved if data.approve else ReviewStatus.rejected
    audit.record(db, action="review.decide", user_id=ctx.user.id, project_id=ctx.project.id,
                 target_type=r.target_type, target_id=r.target_id,
                 detail={"approve": data.approve, "round": r.round_no})
    db.flush()
    return r
