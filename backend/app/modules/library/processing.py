"""Project-scoped orchestration for the actual upstream services, not model emulation."""

import uuid
from types import SimpleNamespace

from sqlalchemy import exists, func, or_, select

from app.core import audit
from app.core.config import settings
from app.core.errors import CapabilityUnsupported, Conflict, NotFound
from app.models.identity import Project, User
from app.models.library import LibraryMedia, MediaVersion
from app.models.media_processing import MediaProcessingRun
from app.modules.library import service

ACTIVE = ("queued", "processing", "waiting")
PROVIDER_CAPABILITIES = {
    "wise": ("visual", "audio", "face", "object"),
    "mediacms": ("preview", "speech"),
}
MEDIA_CAPABILITIES = {
    "wise": {
        "video": ("visual", "audio", "face", "object"),
        "image": ("visual", "face", "object"),
        "audio": ("audio",),
    },
    "mediacms": {"video": ("preview", "speech"), "audio": ("preview", "speech"), "image": ()},
}


def capabilities_for_media(provider, media_type):
    return tuple(
        cap
        for cap in enabled_capabilities(provider)
        if cap in MEDIA_CAPABILITIES[provider].get(media_type, ())
    )


def capabilities_for_version(provider, version):
    capabilities = capabilities_for_media(provider, version.media_type)
    if version.media_type == "video" and version.has_audio is False:
        return tuple(cap for cap in capabilities if cap not in ("audio", "speech"))
    return capabilities


def enabled_capabilities(provider):
    if provider == "wise":
        return tuple(
            cap
            for cap in PROVIDER_CAPABILITIES[provider]
            if cap in settings.library_wise_capabilities
        )
    if provider == "mediacms" and not settings.library_mediacms_transcription_enabled:
        return ("preview",)
    return PROVIDER_CAPABILITIES[provider]


def providers():
    from app.modules.library.optional_capabilities import optional_states

    optional = optional_states()
    return {
        "enabled": settings.library_processing_enabled,
        "automatic": settings.library_processing_auto,
        "providers": [
            {
                "id": provider,
                "name": "WISE" if provider == "wise" else "MediaCMS",
                "configured": bool(
                    getattr(settings, f"library_{provider}_url")
                    and getattr(settings, f"library_{provider}_token")
                ),
                "capabilities": list(enabled_capabilities(provider)),
                "optional_models": optional if provider == "wise" else {},
                "media_capabilities": {
                    kind: list(capabilities_for_media(provider, kind))
                    for kind in MEDIA_CAPABILITIES[provider]
                },
            }
            for provider in PROVIDER_CAPABILITIES
        ],
    }


def run_out(run):
    # Remote URLs, paths, credentials and provider IDs are deliberately not browser data.
    return {
        "capability_status": run.outputs.get("capabilities", {}),
        **{
            key: getattr(run, key)
            for key in (
                "id",
                "version_id",
                "provider",
                "operation",
                "capabilities",
                "status",
                "progress",
                "error",
                "attempts",
                "cancel_requested",
                "created_at",
                "updated_at",
            )
        },
    }


def get_run(db, ctx, run_id, *, lock=False):
    run = db.get(MediaProcessingRun, run_id)
    if run is None:
        raise NotFound("素材处理任务不存在")
    version = db.get(MediaVersion, run.version_id)
    service.media(
        db, ctx.project.id, version.media_id, lock=lock, include_deleted=run.operation == "delete"
    )
    if lock:
        db.refresh(run)
    return run


def listing(db, ctx, *, version_id=None, offset=0, limit=30):
    if version_id:
        version = db.get(MediaVersion, version_id)
        if version is None:
            raise NotFound("素材版本不存在")
        service.media(db, ctx.project.id, version.media_id, include_deleted=True)
    query = (
        select(MediaProcessingRun, MediaVersion, LibraryMedia)
        .select_from(MediaProcessingRun)
        .join(MediaVersion, MediaVersion.id == MediaProcessingRun.version_id)
        .join(LibraryMedia, LibraryMedia.id == MediaVersion.media_id)
        .where(
            LibraryMedia.project_id == ctx.project.id,
            or_(
                LibraryMedia.deleted_at.is_(None),
                MediaProcessingRun.operation == "delete",
            ),
        )
    )
    if version_id:
        query = query.where(MediaProcessingRun.version_id == version_id)
    rows = list(
        db.execute(
            query.order_by(MediaProcessingRun.created_at.desc(), MediaProcessingRun.id)
            .offset(offset)
            .limit(limit + 1)
        )
    )
    return {
        "items": [
            {**run_out(r), "media_id": m.id, "media_name": m.name, "version_ordinal": v.ordinal}
            for r, v, m in rows[:limit]
        ],
        "has_more": len(rows) > limit,
    }


def submit(db, ctx, version_id, data):
    _, version = service.version(db, ctx.project.id, version_id, lock=True)
    if version.status != "ready":
        raise Conflict("原片和预览准备完成后才能启动内容处理")
    if not settings.library_processing_enabled:
        raise CapabilityUnsupported("素材处理服务尚未启用")
    if not (
        getattr(settings, f"library_{data.provider}_url")
        and getattr(settings, f"library_{data.provider}_token")
    ):
        raise CapabilityUnsupported("素材处理服务尚未配置连接和访问凭证")
    capabilities = sorted(set(data.capabilities))
    if not capabilities or not set(capabilities).issubset(enabled_capabilities(data.provider)):
        raise CapabilityUnsupported("该服务未启用所选处理能力，请先完成对应模型的部署与验证")
    if not set(capabilities).issubset(capabilities_for_media(data.provider, version.media_type)):
        raise CapabilityUnsupported(
            "图片已在入库时生成预览，无需 MediaCMS 转码"
            if data.provider == "mediacms" and version.media_type == "image"
            else "所选处理能力不适用于此素材类型"
        )
    if not set(capabilities).issubset(capabilities_for_version(data.provider, version)):
        raise CapabilityUnsupported(
            "此视频没有音轨，无法进行声音索引或字幕识别；请选择画面索引或视频预览。",
            {"reason": "no_audio_track", "retryable": False},
        )
    if data.provider == "wise":
        from app.modules.library.optional_capabilities import require_optional

        require_optional(capabilities)
    latest_cleanup = db.scalar(
        select(MediaProcessingRun)
        .where(
            MediaProcessingRun.version_id == version_id,
            MediaProcessingRun.provider == data.provider,
            MediaProcessingRun.operation == "delete",
        )
        .order_by(MediaProcessingRun.created_at.desc(), MediaProcessingRun.id.desc())
        .limit(1)
    )
    if latest_cleanup is not None and latest_cleanup.status != "ready":
        raise Conflict("此素材的旧索引或临时文件尚未清理完成，请先重试或等待清理任务完成")
    # One job per version/provider, including tasks waiting for the upstream worker.
    current = db.scalar(
        select(MediaProcessingRun).where(
            MediaProcessingRun.version_id == version_id,
            MediaProcessingRun.provider == data.provider,
            MediaProcessingRun.status.in_(ACTIVE),
        )
    )
    if current:
        if not set(capabilities).issubset(current.capabilities):
            raise Conflict("此素材正在处理，请等待完成后再添加其他能力")
        return run_out(current)
    if (
        db.scalar(
            select(func.count())
            .select_from(MediaProcessingRun)
            .where(
                MediaProcessingRun.status.in_(ACTIVE),
            )
        )
        or 0
    ) >= 100:
        raise Conflict("素材处理队列已满，请等待当前任务完成后再提交")
    source = version.proxy_hash if data.provider == "wise" else version.original_hash
    if data.provider == "wise":
        # A later preview transcode must not mutate WISE's immutable source identity.
        source = (
            db.scalar(
                select(MediaProcessingRun.source_hash)
                .where(
                    MediaProcessingRun.version_id == version.id,
                    MediaProcessingRun.provider == "wise",
                )
                .order_by(MediaProcessingRun.created_at)
                .limit(1)
            )
            or source
        )
    if not source:
        raise Conflict("处理所需的素材文件尚未准备好")
    run = MediaProcessingRun(
        id=uuid.uuid4(),
        version_id=version.id,
        created_by=ctx.user.id,
        provider=data.provider,
        capabilities=capabilities,
        source_hash=source,
        outputs={"restore_after_job_id": str(latest_cleanup.id)}
        if data.provider == "wise" and latest_cleanup is not None
        else {},
    )
    db.add(run)
    db.flush()
    audit.record(
        db,
        action="library.processing.submit",
        user_id=ctx.user.id,
        project_id=ctx.project.id,
        target_id=run.id,
        detail={"provider": data.provider, "capabilities": capabilities},
    )
    return run_out(run)


def cancel(db, ctx, run_id):
    run = get_run(db, ctx, run_id, lock=True)
    if run.operation == "delete":
        raise Conflict("清理任务用于同步回收站状态，不能停止；失败后可以重试")
    if run.status in ACTIVE:
        run.cancel_requested = True
        run.next_poll_at = None
        # Never mark an in-flight external operation canceled before reconciling it.
        if run.status == "queued" and run.attempts == 0 and not run.remote_id:
            run.status = "canceled"
    audit.record(
        db,
        action="library.processing.cancel",
        user_id=ctx.user.id,
        project_id=ctx.project.id,
        target_id=run.id,
    )
    return run_out(run)


def retry(db, ctx, run_id):
    run = get_run(db, ctx, run_id, lock=True)
    if run.status != "failed":
        raise Conflict("只有失败的任务可以重试；已停止任务请重新提交")
    if run.operation == "process" and db.scalar(
        select(MediaProcessingRun.id)
        .where(
            MediaProcessingRun.version_id == run.version_id,
            MediaProcessingRun.provider == run.provider,
            MediaProcessingRun.operation == "delete",
            MediaProcessingRun.created_at > run.created_at,
        )
        .limit(1)
    ):
        raise Conflict("这次任务的远端数据已经安排清理，请为恢复后的素材重新开始处理")
    if db.scalar(
        select(MediaProcessingRun.id).where(
            MediaProcessingRun.version_id == run.version_id,
            MediaProcessingRun.provider == run.provider,
            MediaProcessingRun.status.in_(ACTIVE),
        )
    ):
        raise Conflict("此素材已有正在进行的处理任务")
    run.status, run.error, run.cancel_requested = "queued", None, False
    run.next_poll_at = None
    # Preserve remote identity. A timeout is not proof that the upstream submission failed.
    audit.record(
        db,
        action="library.processing.retry",
        user_id=ctx.user.id,
        project_id=ctx.project.id,
        target_id=run.id,
    )
    return run_out(run)


def enqueue_cleanup(db, ctx, media_id):
    """Transactional outbox: trash hides immediately; remote cleanup may retry."""
    from app.modules.library.providers.mediacms import token_for_upload

    rows = db.scalars(
        select(MediaProcessingRun)
        .join(MediaVersion)
        .where(
            MediaVersion.media_id == media_id,
            MediaProcessingRun.operation == "process",
            MediaProcessingRun.attempts > 0,
        )
        .order_by(MediaProcessingRun.created_at.desc())
    ).all()
    # WISE has one source/version. MediaCMS may have several unfinished temp uploads.
    wise_jobs = {}
    cms_jobs = {}
    for run in rows:
        if run.provider == "wise":
            if run.version_id in wise_jobs:
                cleanup = wise_jobs[run.version_id]
                cleanup.outputs = {
                    "source_job_ids": [*cleanup.outputs["source_job_ids"], str(run.id)]
                }
                continue
            identity = None
        else:
            if run.outputs.get("upstream_removed"):
                continue
            identity = run.remote_id or token_for_upload(str(run.id))
            if run.version_id in cms_jobs:
                cleanup = cms_jobs[run.version_id]
                cleanup.outputs = {"remote_ids": [*cleanup.outputs["remote_ids"], identity]}
                continue
        cleanup = MediaProcessingRun(
            version_id=run.version_id,
            created_by=ctx.user.id,
            provider=run.provider,
            operation="delete",
            capabilities=[],
            source_hash=run.source_hash,
            remote_id=identity,
            # Multiple MediaCMS copies are one sequential cleanup job per version.
            outputs={"remote_ids": [identity]} if identity else {"source_job_ids": [str(run.id)]},
        )
        db.add(cleanup)
        if run.provider == "mediacms":
            cms_jobs[run.version_id] = cleanup
        else:
            wise_jobs[run.version_id] = cleanup


def enqueue_automatic(db):
    """Reconcile ready uploads, including a crash between proxy commit and enqueue.

    Opt-in only. Existing failed/canceled runs require an explicit retry; never
    undo the user's cancellation in the next automatic scan.
    """
    if not settings.library_processing_enabled or not settings.library_processing_auto:
        return False
    from app.modules.library.optional_capabilities import OPTIONAL, optional_states

    if (
        db.scalar(
            select(func.count())
            .select_from(MediaProcessingRun)
            .where(
                MediaProcessingRun.status.in_(ACTIVE),
            )
        )
        or 0
    ) >= 100:
        return False
    provider_load = dict(
        db.execute(
            select(MediaProcessingRun.provider, func.count())
            .where(MediaProcessingRun.status.in_(ACTIVE))
            .group_by(MediaProcessingRun.provider)
        ).all()
    )
    wise_capabilities = [
        cap
        for cap in enabled_capabilities("wise")
        if cap in settings.library_processing_wise_auto_capabilities
    ]
    if set(wise_capabilities).intersection(OPTIONAL):
        optional = optional_states()
        # Configured optional models remain visible in providers(), but a
        # missing/expired receipt must not hold base ingestion hostage.
        wise_capabilities = [
            cap
            for cap in wise_capabilities
            if cap not in OPTIONAL or optional.get(cap, {}).get("ready") is True
        ]
    choices = [
        ("wise", wise_capabilities),
        ("mediacms", list(enabled_capabilities("mediacms"))),
    ]
    # Fill both provider queues and spread each across projects. Otherwise one
    # old project's WISE backlog can fill the entire shared admission budget
    # before MediaCMS or newer projects receive even a first task.
    choices.sort(key=lambda choice: provider_load.get(choice[0], 0))
    for provider, capabilities in choices:
        if not capabilities:
            continue
        eligible_types = [
            kind
            for kind in MEDIA_CAPABILITIES[provider]
            if set(capabilities) & set(capabilities_for_media(provider, kind))
        ]
        if not (
            getattr(settings, f"library_{provider}_url")
            and getattr(settings, f"library_{provider}_token")
        ):
            continue
        # When this provider only requests sound, skip confirmed silent videos
        # in SQL so the oldest ineligible item cannot starve later candidates.
        sound_only = not set(capabilities).difference(("audio", "speech"))
        project_load = (
            select(LibraryMedia.project_id, func.count().label("pending"))
            .select_from(MediaProcessingRun)
            .join(MediaVersion, MediaVersion.id == MediaProcessingRun.version_id)
            .join(LibraryMedia, LibraryMedia.id == MediaVersion.media_id)
            .where(
                MediaProcessingRun.provider == provider,
                MediaProcessingRun.status.in_(ACTIVE),
            )
            .group_by(LibraryMedia.project_id)
            .subquery()
        )
        candidate = db.scalar(
            select(MediaVersion)
            .join(LibraryMedia)
            .join(
                Project,
                Project.id == LibraryMedia.project_id,
            )
            .outerjoin(project_load, project_load.c.project_id == LibraryMedia.project_id)
            .where(
                Project.deleted_at.is_(None),
                LibraryMedia.deleted_at.is_(None),
                MediaVersion.status == "ready",
                MediaVersion.media_type.in_(eligible_types),
                or_(MediaVersion.media_type != "video", MediaVersion.has_audio.is_not(False))
                if sound_only
                else True,
                ~exists(
                    select(MediaProcessingRun.id).where(
                        MediaProcessingRun.version_id == MediaVersion.id,
                        MediaProcessingRun.provider == provider,
                    )
                ),
            )
            .order_by(
                func.coalesce(project_load.c.pending, 0),
                MediaVersion.created_at,
                MediaVersion.id,
            )
            .limit(1)
        )
        if candidate:
            media = db.get(LibraryMedia, candidate.media_id)
            project = db.get(Project, media.project_id)
            if project.deleted_at:
                continue
            ctx = SimpleNamespace(project=project, user=db.get(User, project.owner_id))
            requested = [
                cap for cap in capabilities if cap in capabilities_for_version(provider, candidate)
            ]
            while requested:
                try:
                    submit(
                        db,
                        ctx,
                        candidate.id,
                        SimpleNamespace(provider=provider, capabilities=requested),
                    )
                except CapabilityUnsupported:
                    db.rollback()
                    # submit rechecks readiness: a model can disappear after
                    # the scan. At most one fallback, with no optional models;
                    # if it is unavailable too, continue to the other provider.
                    fallback = [cap for cap in requested if cap not in OPTIONAL]
                    if provider != "wise" or fallback == requested:
                        break
                    requested = fallback
                except Conflict:
                    db.rollback()
                    break
                else:
                    db.commit()
                    return True
    return False
