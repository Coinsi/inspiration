"""对象适配器:把"侧边栏 AI 助手"接到各类业务对象上(可改一切的核心)。

每个适配器定义三件事:
  - read(db, project_id, target_id) -> (obj, state)  : 给 AI 看的当前状态
  - object_desc / ops_help                            : 进提示词的对象说明与合法操作
  - apply(db, ctx, target_id, ops, dry_run)           : 校验并应用 ops;非 dry_run 时落库 + 版本快照

ops 应用是**原子**的:任一 op 非法则整批拒绝(抛 OpError),由服务层转成可读错误回给用户去细化。
新增一类可被对话修改的对象,只需在此注册一个适配器,无需改服务层。
"""
import uuid

from sqlalchemy.orm import Session

from app.adapters.contracts import SCRIPT_BLOCK_TYPES
from app.core.deps import ProjectContext
from app.core.errors import Locked, NotFound
from app.kernel.versioning import VersioningService
from app.models.asset import Asset
from app.models.narrative import Scene, Script
from app.models.shot import Shot


class OpError(ValueError):
    """ops 校验/应用失败(可读信息回给用户)。"""


_BLOCK_TYPES = set(SCRIPT_BLOCK_TYPES)


def _new_id() -> str:
    return uuid.uuid4().hex


# ── 适配器注册表 ──
_ADAPTERS: dict[str, "TargetAdapter"] = {}


def register(cls):
    """类装饰器:实例化并按 target_type 注册(返回原类,便于继承)。"""
    inst = cls()
    _ADAPTERS[inst.target_type] = inst
    return cls


def get_adapter(target_type: str) -> "TargetAdapter":
    a = _ADAPTERS.get(target_type)
    if a is None:
        raise NotFound(f"不支持的对话对象类型:{target_type}(支持:{','.join(sorted(_ADAPTERS))})")
    return a


def supported_types() -> list[str]:
    return sorted(_ADAPTERS)


class TargetAdapter:
    target_type: str = ""
    object_desc: str = ""
    ops_help: str = ""
    entity_type: str = ""  # 版本内核 entity_type
    edit_action: str = "narrative.edit"  # 应用修改所需权限(按对象类型)

    def read(self, db: Session, project_id: uuid.UUID, target_id: uuid.UUID):
        raise NotImplementedError

    def apply(self, db, ctx, target_id, ops, *, dry_run=False) -> dict:
        raise NotImplementedError


# ── 剧本:块级编辑 ──
@register
class ScriptAdapter(TargetAdapter):
    target_type = "script"
    entity_type = "script"
    object_desc = (
        "一个剧本(Script),正文由有序的 typed blocks 组成。"
        "每个 block 有 id、block_type、text 三个属性。"
        f"block_type 取值:{'/'.join(SCRIPT_BLOCK_TYPES)}。"
    )
    ops_help = (
        '- {"op":"set_title","text":"新标题"}\n'
        '- {"op":"replace_block","id":"<块id>","text":"新文本"} 改某块文本\n'
        '- {"op":"insert_block","block_type":"dialogue","text":"...","after_id":"<块id>"} '
        "在某块之后插入新块(after_id 省略=追加到末尾;也可用 before_id 在某块之前插入)\n"
        '- {"op":"delete_block","id":"<块id>"} 删除某块\n'
        '- {"op":"move_block","id":"<块id>","after_id":"<目标块id>"} 把块移动到目标块之后(after_id 省略=移到开头)\n'
        "注意:id 必须是当前状态里真实存在的块 id;新插入的块不要带 id。"
    )

    def _get(self, db, project_id, target_id) -> Script:
        s = db.get(Script, target_id)
        if s is None or s.project_id != project_id or s.deleted_at is not None:
            raise NotFound("剧本不存在")
        return s

    def read(self, db, project_id, target_id):
        s = self._get(db, project_id, target_id)
        return s, {"title": s.title, "blocks": list(s.content_blocks or [])}

    def _compute(self, title: str, blocks: list[dict], ops: list[dict]):
        blocks = [dict(b) for b in blocks]  # 深拷贝(浅层即可,block 是扁平 dict)
        idx = {b.get("id"): i for i, b in enumerate(blocks)}

        def require(bid):
            if bid not in idx:
                raise OpError(f"找不到块 id={bid},无法定位修改(对象可能已变更,请重试)")
            return idx[bid]

        for op in ops:
            kind = op.get("op")
            if kind == "set_title":
                title = str(op.get("text", "") or "").strip() or title
            elif kind == "replace_block":
                i = require(op.get("id"))
                blocks[i]["text"] = str(op.get("text", "") or "").strip()
            elif kind == "delete_block":
                i = require(op.get("id"))
                blocks.pop(i)
                idx = {b.get("id"): j for j, b in enumerate(blocks)}
            elif kind in ("insert_block", "move_block"):
                if kind == "insert_block":
                    btype = op.get("block_type")
                    if btype not in _BLOCK_TYPES:
                        raise OpError(f"非法 block_type:{btype}")
                    text = str(op.get("text", "") or "").strip()
                    if not text:
                        raise OpError("insert_block 的 text 不能为空")
                    node = {"id": _new_id(), "block_type": btype, "text": text}
                else:
                    i = require(op.get("id"))
                    node = blocks.pop(i)
                # 计算插入位置
                after_id, before_id = op.get("after_id"), op.get("before_id")
                cur = {b.get("id"): j for j, b in enumerate(blocks)}
                if before_id is not None and before_id in cur:
                    pos = cur[before_id]
                elif after_id is not None and after_id in cur:
                    pos = cur[after_id] + 1
                elif after_id is None and before_id is None and kind == "insert_block":
                    pos = len(blocks)  # 追加
                elif after_id is None and kind == "move_block":
                    pos = 0  # 移到开头
                else:
                    raise OpError("插入/移动位置无效:after_id/before_id 不存在")
                blocks.insert(pos, node)
                idx = {b.get("id"): j for j, b in enumerate(blocks)}
            else:
                raise OpError(f"不支持的操作:{kind}")
        return title, blocks

    def apply(self, db, ctx: ProjectContext, target_id, ops, *, dry_run=False) -> dict:
        s = self._get(db, ctx.project.id, target_id)
        if str(s.status) in ("locked", "VersionStatus.locked"):
            raise Locked("对象已锁定，请先在原始对象中解锁")
        new_title, new_blocks = self._compute(s.title, list(s.content_blocks or []), ops)
        state = {"title": new_title, "blocks": new_blocks}
        if dry_run:
            return {"state": state}
        s.title = new_title
        s.content_blocks = new_blocks
        v = VersioningService(db).commit(
            project_id=ctx.project.id, entity_type=self.entity_type, entity_id=s.id,
            content=state, created_by=ctx.user.id, label="AI 助手修改",
        )
        s.current_version_id = v.id
        db.flush()
        return {"state": state, "version_id": str(v.id)}


# ── 通用字段对象:scene / shot / asset(白名单字段)──
class _FieldAdapter(TargetAdapter):
    model = None
    fields: tuple[str, ...] = ()
    name_attr = "title"

    def _get(self, db, project_id, target_id):
        o = db.get(self.model, target_id)
        deleted = getattr(o, "deleted_at", None) if o else None
        if o is None or o.project_id != project_id or deleted is not None:
            raise NotFound(f"{self.target_type} 不存在")
        return o

    def read(self, db, project_id, target_id):
        o = self._get(db, project_id, target_id)
        return o, {"fields": {f: getattr(o, f, None) for f in self.fields}}

    def _snapshot(self, o) -> dict:
        return {f: getattr(o, f, None) for f in self.fields}

    def apply(self, db, ctx: ProjectContext, target_id, ops, *, dry_run=False) -> dict:
        o = self._get(db, ctx.project.id, target_id)
        if str(getattr(o,"status","")) in ("locked", "VersionStatus.locked"):
            raise Locked("对象已锁定，请先在原始对象中解锁")
        new_vals = {f: getattr(o, f, None) for f in self.fields}
        for op in ops:
            if op.get("op") != "set_field":
                raise OpError(f"该对象仅支持 set_field,收到:{op.get('op')}")
            field = op.get("field")
            if field not in self.fields:
                raise OpError(f"不可编辑字段:{field}(可编辑:{','.join(self.fields)})")
            new_vals[field] = op.get("value")
        if dry_run:
            return {"state": {"fields": new_vals}}
        for f, v in new_vals.items():
            setattr(o, f, v)
        ver = VersioningService(db).commit(
            project_id=ctx.project.id, entity_type=self.entity_type, entity_id=o.id,
            content=self._snapshot(o), created_by=ctx.user.id, label="AI 助手修改",
        )
        if hasattr(o, "current_version_id"):
            o.current_version_id = ver.id
        db.flush()
        return {"state": {"fields": new_vals}, "version_id": str(ver.id)}


def _field_help(fields) -> str:
    return (
        '- {"op":"set_field","field":"<字段>","value":"<新值>"} 修改指定字段\n'
        f"可编辑字段:{', '.join(fields)}"
    )


@register
class SceneAdapter(_FieldAdapter):
    target_type = "scene"
    entity_type = "scene"
    model = Scene
    fields = ("title", "summary", "body")
    object_desc = "一个场次(Scene),含 title(标题)、summary(梗概)、body(正文)。"
    ops_help = _field_help(fields)


@register
class ShotAdapter(_FieldAdapter):
    target_type = "shot"
    entity_type = "shot"
    edit_action = "shot.edit"
    model = Shot
    fields = ("title", "description")
    object_desc = "一个镜头(Shot),含 title(镜头名)、description(画面/动作描述)。"
    ops_help = _field_help(fields)


@register
class AssetAdapter(_FieldAdapter):
    target_type = "asset"
    entity_type = "asset"
    edit_action = "asset.edit"
    model = Asset
    fields = ("name", "summary")
    object_desc = "一个资产(Asset,如人物/场景/道具),含 name(名称)、summary(简介)。"
    ops_help = _field_help(fields)


@register
class SettingAdapter(_FieldAdapter):
    target_type = "setting"
    entity_type = "setting"
    model = None  # 延迟绑定,避免循环导入
    fields = ("name", "content")
    object_desc = "一条故事设定(Setting,世界观/体系/人物/地点等),含 name(条目名)、content(设定内容)。"
    ops_help = _field_help(fields)

    def _get(self, db, project_id, target_id):
        from app.models.setting import Setting

        o = db.get(Setting, target_id)
        if o is None or o.project_id != project_id or getattr(o,"deleted_at",None):
            raise NotFound("设定不存在")
        return o
