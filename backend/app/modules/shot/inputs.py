"""Resolve the exact version and visible details consumed by a shot."""

from sqlalchemy import select

from app.adapters.contracts import ConsistencyContext, consistency_registry
from app.core.errors import Conflict, NotFound
from app.models.asset import Asset, AssetReferenceImage
from app.models.consistency import VisualIdentity
from app.models.prompt import PromptFragment
from app.models.shot import ShotAssetRef
from app.models.versioning import Version

ASPECTS = {
    "face": "面部",
    "wardrobe": "衣着",
    "layout": "空间布局",
    "light": "光线",
    "appearance": "外观",
}


def dependencies(inputs, references, composed):
    result = []
    used = {ref.blob_hash for ref in references}
    for item in inputs:
        hashes = [h for h in item["hashes"] if h in used]
        if composed or hashes:
            result.append(
                {
                    **{k: v for k, v in item.items() if k not in {"hashes", "parts"}},
                    "prompt_parts": item["parts"] if composed else [],
                    "reference_hashes": hashes,
                }
            )
    return result


def appearance(db, asset):
    identity = db.scalar(select(VisualIdentity).where(VisualIdentity.asset_id == asset.id))
    parts = []
    if identity:
        fragment = (
            db.get(PromptFragment, identity.prompt_fragment_id)
            if identity.prompt_fragment_id
            else None
        )
        if fragment and (fragment.project_id != asset.project_id or fragment.deleted_at):
            fragment = None
        context = ConsistencyContext(
            asset_name=asset.name,
            prompt_fragment_text=fragment.text if fragment else None,
            lora_ref=identity.lora_ref,
            reference_set=identity.reference_set,
        )
        parts = consistency_registry.create(identity.strategy).prompt_parts(context)
    images = db.scalars(
        select(AssetReferenceImage.blob_hash)
        .where(AssetReferenceImage.asset_id == asset.id)
        .order_by(AssetReferenceImage.ordinal, AssetReferenceImage.created_at)
    )
    return {
        "visual_parts": parts,
        "reference_hashes": list(
            dict.fromkeys(h for h in [asset.representative_blob_hash, *images] if h)
        ),
    }


def pinned(db, project_id, asset_id, version_id):
    version = db.get(Version, version_id) if version_id else None
    if (
        not version
        or version.entity_id != asset_id
        or version.entity_type != "asset"
        or version.project_id != project_id
    ):
        raise Conflict("所选版本不属于这项资产")
    return version


def visual_traits(meta, look):
    base = meta.get("visual_traits")
    looks = meta.get("looks")
    named = looks.get(look) if isinstance(looks, dict) else None
    return {
        **(base if isinstance(base, dict) else {}),
        **(named if isinstance(named, dict) else {}),
    }


def look_image(meta, look):
    images = meta.get("look_images")
    return images.get(look) if isinstance(images, dict) else None


def resolved(db, shot):
    rows = db.execute(
        select(ShotAssetRef, Asset)
        .join(Asset, Asset.id == ShotAssetRef.asset_id)
        .where(ShotAssetRef.shot_id == shot.id, Asset.project_id == shot.project_id)
        .order_by(ShotAssetRef.ordinal)
    )
    result = []
    for ref, asset in rows:
        if ref.ref_mode == "pinned":
            version = pinned(db, shot.project_id, asset.id, ref.pinned_version_id)
            data = version.content
            version_id = version.id
        else:
            if asset.deleted_at:
                raise NotFound(f"镜头引用的资产「{asset.name}」已移入回收站，请更换参考")
            data = {
                "name": asset.name,
                "summary": asset.summary,
                "metadata": asset.meta,
                **appearance(db, asset),
            }
            version_id = asset.current_version_id
        usage = ref.usage or {}
        aspects = usage.get("aspects") or list(ASPECTS)
        meta = data.get("metadata") or {}
        look = usage.get("look", "")
        traits = visual_traits(meta, look)
        hashes = data.get("reference_hashes", [data.get("representative_blob_hash")])
        if look:
            chosen = look_image(meta, look)
            hashes = [chosen] if chosen and chosen in hashes else []
        # A broad description may belong to another period or include off-camera details.
        use_description = not look and not (usage.get("aspects") and traits)
        parts = [data["name"]]
        if use_description:
            parts.extend([data.get("summary"), *data.get("visual_parts", [])])
        if look:
            parts.append(f"造型：{look}")
        parts.extend(
            f"{ASPECTS[a]}：{traits[a]}" for a in aspects if a in ASPECTS and traits.get(a)
        )
        result.append(
            {
                "asset_id": str(asset.id),
                "name": data["name"],
                "role": ref.role,
                "version_id": str(version_id) if version_id else None,
                "ref_mode": ref.ref_mode,
                "look": look,
                "aspects": usage.get("aspects") or [],
                "evidence": usage.get("evidence", ""),
                "uses_description": use_description,
                "parts": list(dict.fromkeys(p for p in parts if p)),
                "hashes": hashes,
            }
        )
    return result
