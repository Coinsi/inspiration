"""Resolve confirmed story names within a novel or an independently authored script."""

import hashlib
import re
import unicodedata
import uuid
from collections import defaultdict

from sqlalchemy import select

from app.core.errors import Conflict
from app.models.asset import Asset, AssetStorySource
from app.models.evidence import StoryIdentity
from app.models.identity import AuditLog, Project
from app.models.narrative import Chapter, Scene, SceneElement, Script
from app.models.shot import Shot, ShotAssetRef
from app.modules.asset import schemas, service


def normalized(name):
    return " ".join(unicodedata.normalize("NFKC", name).casefold().split())


def ambiguous_character(kind, name):
    return kind == "character" and bool(
        re.fullmatch(
            r"(?:一[名个位])?(?:陌生的?|年轻的?)?(?:他|她|他们|她们|男人|女人|男子|女子|青年|少年|老人|孩子|路人|陌生人|守卫|士兵|司机|店员|he|she|man|woman|stranger|guard)",
            normalized(name),
        )
    )


def scope_for(db, script_id=None, chapter_id=None):
    if not chapter_id and script_id:
        chapter_id = db.get(Script, script_id).source_chapter_id
    return db.get(Chapter, chapter_id).novel_id if chapter_id else script_id


class AssetNames:
    def __init__(self, db, project_id, scope):
        self.by_name = defaultdict(list)
        self.other_names = defaultdict(list)
        self.spellings = {}
        scopes = defaultdict(set)
        aliases = defaultdict(set)
        for aid, sid, name in db.execute(
            select(
                AssetStorySource.asset_id, AssetStorySource.scope_id, AssetStorySource.source_name
            ).where(AssetStorySource.project_id == project_id)
        ):
            if sid:
                scopes[aid].add(sid)
            if sid == scope and name and not ambiguous_character("character", name):
                aliases[aid].add(name)
        for identity in db.scalars(
            select(StoryIdentity).where(
                StoryIdentity.project_id == project_id, StoryIdentity.asset_id.is_not(None)
            )
        ):
            aliases[identity.asset_id].update([identity.name, *(identity.aliases or [])])
        for asset in db.scalars(
            select(Asset).where(Asset.project_id == project_id, Asset.deleted_at.is_(None))
        ):
            catalog = (
                self.other_names
                if scope and scopes[asset.id] and scope not in scopes[asset.id]
                else self.by_name
            )
            names = {asset.name, *aliases[asset.id]}
            declared = (asset.meta or {}).get("aliases")
            if isinstance(declared, list):
                names.update(a for a in declared if isinstance(a, str))
            self.spellings[asset.id] = {normalized(n) for n in names if n}
            for name in self.spellings[asset.id]:
                catalog[asset.type, name].append(asset)

    def find(self, kind, name):
        return self.by_name.get((kind, normalized(name)), [])

    def possible_people(self, kind, name, text):
        if kind != "character":
            return []
        name = normalized(name)
        nickname = re.fullmatch(
            r"(?:[小老阿]([\u3400-\u9fff])|([\u3400-\u9fff])(?:哥|姐|叔|伯|爷|姨))", name
        )
        surname = next((s for s in nickname.groups() if s), "") if nickname else ""
        text = normalized(text)
        named_in_passage = ambiguous_character(kind, name) or (
            name in text
            and any(
                term in text for term in ("又名", "外号", "绰号", "人称", "叫我", "也叫", "本名")
            )
        )
        candidates = {}
        for (asset_kind, spelling), rows in self.by_name.items():
            if asset_kind != kind or ambiguous_character(kind, spelling):
                continue
            if (surname and spelling.startswith(surname)) or (
                named_in_passage and spelling in text
            ):
                candidates.update({a.id: a for a in rows})
        return list(candidates.values())

    def visible_subject(self, kind, name, quote):
        matches = self.find(kind, name)
        if len(matches) != 1 or not quote or ambiguous_character(kind, name):
            return None
        asset = matches[0]
        for spelling in self.spellings[asset.id]:
            if not ambiguous_character(kind, spelling) and spelling in normalized(quote):
                return asset
        return None


def attach_pending(db, asset, scope, source_name=None):
    """Only previously proposed visible subjects can become automatic bindings."""
    declared = (asset.meta or {}).get("aliases")
    aliases = (
        {asset.name, *(a for a in declared if isinstance(a, str))}
        if isinstance(declared, list)
        else {asset.name}
    )
    identities = db.scalars(
        select(StoryIdentity).where(
            StoryIdentity.project_id == asset.project_id, StoryIdentity.asset_id == asset.id
        )
    )
    for identity in identities:
        aliases.update([identity.name, *(identity.aliases or [])])
    if source_name:
        aliases.add(source_name)
    from sqlalchemy import or_

    catalogs = {}
    rows = db.execute(select(SceneElement, Scene).join(Scene, Scene.id == SceneElement.scene_id).where(
        Scene.project_id == asset.project_id, Scene.deleted_at.is_(None), SceneElement.active.is_(True),
        SceneElement.asset_id.is_(None), SceneElement.manual_binding.is_(False), SceneElement.kind == asset.type,
        SceneElement.name_key.in_([normalized(n) for n in aliases if n]),
    ).with_for_update(of=SceneElement))
    for member, source_scene in rows:
        member_scope = scope_for(db, source_scene.script_id, source_scene.adapted_from_chapter_id)
        if scope and member_scope != scope:
            continue
        if member_scope not in catalogs:
            catalogs[member_scope] = AssetNames(db, asset.project_id, member_scope)
        if catalogs[member_scope].visible_subject(member.kind, member.name, member.evidence) == asset:
            member.asset_id = asset.id
    db.flush()
    stmt = (
        select(Shot, Scene)
        .join(Scene, Scene.id == Shot.scene_id)
        .where(
            Shot.project_id == asset.project_id,
            Shot.deleted_at.is_(None),
            Scene.deleted_at.is_(None),
            or_(
                *(
                    Shot.storyboard["unresolved_subjects"].contains(
                        [{"name": n, "type": asset.type}]
                    )
                    for n in aliases
                    if isinstance(n, str) and n
                )
            ),
        )
        .with_for_update(of=Shot)
    )
    for shot, scene in db.execute(stmt):
        if str(asset.id) in shot.storyboard.get("suppressed_asset_ids", []):
            continue
        shot_scope = scope_for(db, scene.script_id, scene.adapted_from_chapter_id)
        if scope and shot_scope != scope:
            continue
        if shot_scope not in catalogs:
            catalogs[shot_scope] = AssetNames(db, asset.project_id, shot_scope)
        refs = list(db.scalars(select(ShotAssetRef).where(ShotAssetRef.shot_id == shot.id)))
        remaining = []
        for subject in shot.storyboard["unresolved_subjects"]:
            if (
                catalogs[shot_scope].visible_subject(
                    subject["type"], subject["name"], subject.get("evidence")
                )
                != asset
            ):
                remaining.append(subject)
                continue
            member = None
            if scene.script_position is not None:
                member = db.scalar(select(SceneElement).where(SceneElement.scene_id == scene.id,
                    SceneElement.active.is_(True), SceneElement.asset_id == asset.id,
                    SceneElement.kind == subject["type"], SceneElement.name_key == normalized(subject["name"])))
                if not member:
                    remaining.append(subject)
                    continue
            if not any(r.asset_id == asset.id for r in refs):
                ref = ShotAssetRef(
                    shot_id=shot.id,
                    asset_id=asset.id,
                    role=asset.type,
                    ordinal=len(refs),
                    usage={**{k: v for k, v in subject.items() if k in {"evidence", "look", "aspects"}},
                           **({"scene_element_id": str(member.id)} if member else {})},
                )
                db.add(ref)
                refs.append(ref)
        shot.storyboard = {**shot.storyboard, "unresolved_subjects": remaining}


def resolve(
    db,
    ctx,
    kind,
    name,
    summary,
    *,
    source_type,
    source_id,
    scope,
    chosen=None,
    separate=False,
    evidence="",
):
    name = name.strip()
    if not name or len(name) > 255:
        raise Conflict("请填写不超过 255 字的资产名称")
    db.scalar(select(Project.id).where(Project.id == ctx.project.id).with_for_update())
    key = "" if source_type == "setting" else f"{kind.value}/{normalized(name)}"
    generic = ambiguous_character(kind, name)
    if generic and source_type != "setting":
        key += "/" + hashlib.sha256(normalized(evidence or summary).encode()).hexdigest()
    if len(key) > 300:
        key = hashlib.sha256(key.encode()).hexdigest()
    link = (
        db.scalar(
            select(AssetStorySource).where(
                AssetStorySource.project_id == ctx.project.id,
                AssetStorySource.source_type == source_type,
                AssetStorySource.source_id == source_id,
                AssetStorySource.source_key == key,
            )
        )
        if source_id
        else None
    )
    linked_asset = db.get(Asset, link.asset_id) if link else None
    if linked_asset and not linked_asset.deleted_at:
        asset = linked_asset
        if chosen and chosen != asset.id:
            raise Conflict("此来源已关联其他资产")
        return asset
    names = AssetNames(db, ctx.project.id, scope)
    matches = names.find(kind, name)
    other = names.other_names[kind, normalized(name)]
    possible = (
        names.possible_people(kind, name, evidence or summary or "")
        if not matches and not other
        else []
    )
    if source_type == "setting" and source_id:
        # Before source links existed, conversions recorded the actual asset in the audit log.
        known = db.scalars(
            select(AuditLog.detail).where(
                AuditLog.project_id == ctx.project.id,
                AuditLog.action == "setting.to_asset",
                AuditLog.target_id == source_id,
            )
        )
        old_ids = set()
        for detail in known:
            try:
                old_ids.add(uuid.UUID(str((detail or {}).get("asset_id"))))
            except ValueError:
                continue
        if old_ids:
            previous = list(
                db.scalars(
                    select(Asset).where(
                        Asset.id.in_(old_ids),
                        Asset.project_id == ctx.project.id,
                        Asset.type == kind,
                        Asset.deleted_at.is_(None),
                    )
                )
            )
            if previous:
                matches = previous
    if chosen:
        asset = service.get(db, ctx.project.id, chosen)
        if asset.type != kind:
            raise Conflict("所选资产类型与来源不符")
    elif len(matches) == 1 and not separate and not generic and not link:
        asset = matches[0]
    elif (matches or other or possible or link) and not separate:
        raise Conflict(
            f"请确认「{name}」是否对应已有资产",
            {
                "name": name,
                "kind": kind.value,
                "candidates": [
                    {"id": str(a.id), "name": a.name, "code": a.code, "summary": a.summary}
                    for a in matches or other or possible
                ],
            },
        )
    else:
        asset = service.create(db, ctx, schemas.AssetIn(type=kind, name=name, summary=summary))
    if link:
        link.asset_id, link.source_name, link.evidence = asset.id, name, evidence or summary or ""
        db.flush()
    elif source_id:
        db.add(
            AssetStorySource(
                project_id=ctx.project.id,
                asset_id=asset.id,
                scope_id=scope,
                source_type=source_type,
                source_id=source_id,
                source_key=key,
                source_name=name,
                evidence=evidence or summary or "",
            )
        )
        db.flush()
    attach_pending(db, asset, scope, name)
    return asset
