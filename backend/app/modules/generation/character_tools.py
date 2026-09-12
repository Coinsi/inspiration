"""Reference-based character presets; estimates and submissions use the same prompt."""

from app.core.errors import CapabilityUnsupported
from app.modules.generation import jobs, schemas, service

# Keys are persisted in job history. Changing instructions requires a version bump.
VERSION = 1
PRESETS = {
    "view": {
        "front": ("正面", "Front", "正面平视，面部正对镜头。"),
        "three_quarter": (
            "四分之三侧面",
            "Three-quarter",
            "平视四分之三侧面，鼻尖朝画面左侧，双眼仍可见。",
        ),
        "profile_left": (
            "朝左侧面",
            "Left-facing profile",
            "严格侧面平视，鼻尖朝画面左侧，仅一侧眼睛可见。",
        ),
        "profile_right": (
            "朝右侧面",
            "Right-facing profile",
            "严格侧面平视，鼻尖朝画面右侧，仅一侧眼睛可见。",
        ),
        "back": ("背面", "Back", "背面对镜头，不露正脸，呈现背面发型和服装。"),
    },
    "expression": {
        "neutral": ("平静", "Neutral", "平静自然，嘴部放松，眉眼舒展。"),
        "happy": ("微笑", "Smile", "自然微笑，嘴角上扬，眼神温暖。"),
        "sad": ("悲伤", "Sad", "悲伤低落，眉眼下垂，嘴角微微下垂。"),
        "angry": ("生气", "Angry", "克制的愤怒，眉头收紧，目光坚定，避免夸张变形。"),
        "surprised": ("惊讶", "Surprised", "惊讶，眉毛抬起，眼睛睁大，嘴唇微张。"),
        "serious": ("严肃", "Serious", "严肃专注，嘴唇自然闭合，目光沉稳。"),
    },
    "sheet": {
        "three_view": (
            "正侧背三视图",
            "Three-view sheet",
            "一张横向角色设定参考板，恰好三个等宽区域，从左到右依次为正面、鼻尖朝画面左侧的严格侧面、背面。三个区域是同一个角色，均为完整全身、自然站立、平视、相同身高比例，头顶与脚底对齐，人物互不重叠，不裁切头脚。纯浅灰背景，无文字、标签、道具或额外人物。",
        ),
    },
}


def catalog():
    return {
        "version": VERSION,
        "groups": {
            mode: [
                {"id": key, "label": value[0], "label_en": value[1]}
                for key, value in presets.items()
            ]
            for mode, presets in PRESETS.items()
        },
    }


def prepare(db, ctx, gen_id, data):
    source = jobs.source(db, ctx.project.id, gen_id, True)
    if source.target_type not in ("asset", "shot"):
        raise CapabilityUnsupported("请选择资产或镜头中的角色图片")
    jobs.validate_target(db, ctx.project.id, source.target_type, source.target_id)
    preset = PRESETS[data.mode].get(data.preset)
    if not preset:
        raise CapabilityUnsupported("该分类下不存在所选角色预设")
    caps = service._provider_instance(db, ctx.project.id, data.provider).capabilities()
    if "image" not in caps.modalities or "img2img" not in caps.features:
        raise CapabilityUnsupported(
            "请使用支持参考图编辑的图片供应商；测试素材不能用于角色一致性生成"
        )
    instruction = {
        "view": "只调整观察视角。保持参考角色的表情、服装、发型、画风和取景范围。",
        "expression": "只调整面部表情。保持参考角色的视角、姿势、服装、发型、画风、构图和背景。",
        "sheet": "绘制角色正侧背三视图参考板。参考图未展示的背面和服装细节需作合理推演，不改变已知设计。",
    }[data.mode]
    prompt = (
        "以随附图片中唯一的主要角色为身份参考，保持脸型、五官、年龄特征、肤色、发色、服装及配饰的识别特征。"
        "不要换成另一个人物，不要添加水印。\n" + instruction + "\n" + preset[2]
    )
    if data.notes.strip():
        prompt += "\n用户补充要求：\n" + data.notes.strip()
    request = schemas.GenerateIn(
        provider=data.provider,
        count=1,
        use_references=False,
        source_generation_id=source.id,
        prompt_override=prompt,
        provider_params={"size": "1536x1024" if data.mode == "sheet" else "1024x1024"}
        if data.provider == "gpt_image"
        else {},
    )
    metadata = {
        "version": VERSION,
        "mode": data.mode,
        "preset": data.preset,
        "label": preset[0],
        "label_en": preset[1],
        "notes": data.notes.strip(),
    }
    return source, request, metadata


def preview(db, ctx, gen_id, data):
    source, request, preset = prepare(db, ctx, gen_id, data)
    estimate = service.estimate(db, ctx, source.target_type, source.target_id, request)
    return {
        "prompt": request.prompt_override,
        "points": estimate.points,
        "preset": preset,
        "source_generation_id": str(source.id),
        "count": 1,
    }


def submit(db, ctx, gen_id, data):
    source, request, preset = prepare(db, ctx, gen_id, data)
    return service.submit(
        db, ctx, source.target_type, source.target_id, request, character_preset=preset
    )
