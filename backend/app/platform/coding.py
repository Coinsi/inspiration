"""编码生成:基于 code_sequence 的项目内自增编号(对照 01 需求 §9.1)。

默认规则:<前缀>-<零填充序号>,可由 project.settings['coding'] 覆盖前缀与位宽。
镜头编码较特殊(场+镜),由 shot 服务单独拼装,不走此通用函数。
"""
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.identity import CodeSequence, Project

DEFAULT_PREFIX: dict[str, str] = {
    "novel": "NV",
    "script": "SCR",
    "scene": "SC",
    "character": "CH",
    "prop": "PR",
    "location": "LOC",
    "costume": "CO",
    "vehicle": "VH",
    "style": "ST",
    "model_lora": "ML",
    "prompt": "PT",
    "prompt_fragment": "PF",
    "generation": "GEN",
    "baseline": "BL",
    "cut": "CUT",
    "outline": "OL",
}
DEFAULT_WIDTH = 4


def next_code(db: Session, project: Project, entity_type: str) -> str:
    """原子取下一个序号并格式化为业务编码。"""
    coding = (project.settings or {}).get("coding", {})
    prefix = coding.get("prefix", {}).get(entity_type, DEFAULT_PREFIX.get(entity_type, entity_type.upper()))
    width = int(coding.get("width", DEFAULT_WIDTH))

    seq = db.scalar(
        select(CodeSequence)
        .where(CodeSequence.project_id == project.id, CodeSequence.entity_type == entity_type)
        .with_for_update()
    )
    if seq is None:
        seq = CodeSequence(project_id=project.id, entity_type=entity_type, next_seq=1)
        db.add(seq)
        db.flush()

    number = seq.next_seq
    seq.next_seq = number + 1
    db.flush()
    return f"{prefix}-{number:0{width}d}"
