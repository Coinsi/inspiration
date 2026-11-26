"""Give screenplay scenes stable identities at authoring time."""

import json
import re
import unicodedata
import uuid
from collections import Counter, defaultdict
from hashlib import sha256

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

revision = "0071_screenplay_scenes"
down_revision = "0070_shot_continuity"
branch_labels = depends_on = None


def _sections(blocks):
    sections, group, seen = [], [], set()
    for value in blocks:
        if (
            not isinstance(value, dict)
            or not isinstance(value.get("text"), str)
            or not value.get("block_type")
        ):
            return None
        block = dict(value)
        if not isinstance(block.get("id"), str) or not block["id"] or block["id"] in seen:
            block["id"] = uuid.uuid4().hex
        seen.add(block["id"])
        if block["block_type"] == "scene_heading" and group:
            sections.append(group)
            group = []
        group.append(block)
    return [*sections, group] if group else sections


def _body(group):
    lines = []
    for b in group:
        kind, text = b["block_type"], b["text"]
        if kind == "character":
            text += ":"
        if kind == "parenthetical":
            text = f"({text.strip('()')})"
        if kind != "scene_heading":
            lines.append(text)
    return "\n".join(lines)


def _backfill(db, script):
    groups = _sections(script.content_blocks)
    if not groups:
        return
    old = db.execute(
        sa.text("""
        SELECT id, title, body, source_key FROM scene
        WHERE script_id=:id AND deleted_at IS NULL
        AND adapted_from_chapter_id IS NOT DISTINCT FROM CAST(:chapter AS uuid)
        ORDER BY ordinal, id
    """),
        {"id": script.id, "chapter": script.source_chapter_id},
    )
    candidates = defaultdict(list)
    for row in old:
        candidates[row.title, row.body or ""].append(row)
    counts = Counter()
    totals = Counter(
        (
            (group[0]["text"] if group[0]["block_type"] == "scene_heading" else f"场景 {i}"),
            _body(group),
        )
        for i, group in enumerate(groups, 1)
    )
    coding = (script.settings or {}).get("coding", {})
    prefix = coding.get("prefix", {}).get("scene", "SC")
    width = int(coding.get("width", 4))
    for index, group in enumerate(groups, 1):
        heading = group[0]["block_type"] == "scene_heading"
        title = group[0]["text"] if heading else f"场景 {index}"
        body = _body(group)
        key = (title, body)
        raw = [str(script.source_chapter_id or ""), *key, counts[key]]
        fingerprint = sha256(
            json.dumps(raw, sort_keys=True, ensure_ascii=False, default=str).encode()
        ).hexdigest()
        counts[key] += 1
        options = candidates[key]
        matched = next((row for row in options if row.source_key == fingerprint), None)
        if matched is None and len(options) == 1 and totals[key] == 1:
            matched = options[0]
        values = {
            "id": matched.id if matched else uuid.uuid4(),
            "position": index,
            "anchor": "heading/" + group[0]["id"] if heading else "preamble",
            "blocks": json.dumps(group, ensure_ascii=False),
        }
        if matched:
            options.remove(matched)
        else:
            number = db.scalar(
                sa.text("""
                INSERT INTO code_sequence(project_id, entity_type, next_seq) VALUES (:p, 'scene', 2)
                ON CONFLICT(project_id, entity_type) DO UPDATE SET next_seq=code_sequence.next_seq+1
                RETURNING next_seq-1
            """),
                {"p": script.project_id},
            )
            db.execute(
                sa.text("""
                INSERT INTO scene(id,project_id,script_id,ordinal,title,summary,body,
                                  adapted_from_chapter_id,status,created_by,code)
                VALUES (:id,:p,:s,:position,:title,:summary,'',:chapter,'draft',:creator,:code)
            """),
                {
                    **values,
                    "p": script.project_id,
                    "s": script.id,
                    "title": title[:255],
                    "summary": body[:30].replace("\n", " "),
                    "chapter": script.source_chapter_id,
                    "creator": script.created_by,
                    "code": f"{prefix}-{number:0{width}d}",
                },
            )
        db.execute(
            sa.text("""
            UPDATE scene SET script_anchor=:anchor, script_position=:position, ordinal=:position,
                             script_blocks=CAST(:blocks AS jsonb), body='' WHERE id=:id
        """),
            values,
        )
        _seed_elements(db, values["id"], group)
    db.execute(sa.text("UPDATE script SET content_blocks='[]' WHERE id=:id"), {"id": script.id})


def _seed_elements(db, scene_id, group):
    """Only speaker cues and location headings can be recovered without guessing."""
    for b in group:
        evidence = b["text"]
        if b["block_type"] == "character":
            if re.search(r"画外|旁白|电话音|\b(?:V\.?O|O\.?S)\b", evidence, re.I):
                continue
            kind, name = "character", re.split(r"[（(]", evidence, maxsplit=1)[0].strip()
        elif b["block_type"] == "scene_heading":
            kind = "location"
            name = re.sub(
                r"^(?:INT\.?/EXT\.?|INT\.?|EXT\.?|内景|外景)\s*[:.：-]?\s*",
                "",
                evidence,
                flags=re.I,
            )
            name = re.sub(
                r"\s*[-－—· ]\s*(?:日|夜|清晨|黎明|早晨|上午|白天|午后|下午|黄昏|傍晚|深夜"
                r"|DAY|NIGHT|DAWN|DUSK)$",
                "",
                name,
                flags=re.I,
            ).strip()
        else:
            continue
        if not name or len(name) > 255:
            continue
        # Restore only an existing, unique production reference with the same name and type.
        key = " ".join(unicodedata.normalize("NFKC", name).casefold().split())
        db.execute(
            sa.text("""
            INSERT INTO scene_element(id,scene_id,kind,name,name_key,evidence,prompt,
                                      direction,active,asset_id)
            SELECT :id,:scene,CAST(:kind AS varchar),CAST(:name AS varchar),:key,
                :quote,:quote,'',TRUE,
                (SELECT CASE WHEN count(DISTINCT a.id)=1 THEN min(a.id::text)::uuid END
                 FROM shot_asset_ref r JOIN shot sh ON sh.id=r.shot_id
                 JOIN asset a ON a.id=r.asset_id
                 WHERE sh.scene_id=:scene AND sh.deleted_at IS NULL AND a.deleted_at IS NULL
                   AND a.type=CAST(:kind AS varchar) AND a.name=CAST(:name AS varchar))
            ON CONFLICT(scene_id,kind,name_key) DO NOTHING
        """),
            {
                "id": uuid.uuid4(),
                "scene": scene_id,
                "kind": kind,
                "name": name,
                "key": key,
                "quote": evidence,
            },
        )


def upgrade():
    op.add_column("scene", sa.Column("script_position", sa.Integer()))
    op.add_column("scene", sa.Column("script_anchor", sa.Text()))
    op.add_column("scene", sa.Column("script_blocks", JSONB(none_as_null=True)))
    op.add_column("scene", sa.Column("elements_revision", sa.String(64)))
    op.create_index("uq_scene_script_anchor", "scene", ["script_id", "script_anchor"], unique=True)
    op.create_table(
        "shot_script_source",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("shot_id", UUID, sa.ForeignKey("shot.id", ondelete="CASCADE"), nullable=False),
        sa.Column("scene_id", UUID, sa.ForeignKey("scene.id", ondelete="CASCADE"), nullable=False),
        sa.Column("block_id", sa.Text(), nullable=False),
        sa.Column("accepted_text", sa.Text()),
        sa.Column("current_text", sa.Text()),
        sa.UniqueConstraint("shot_id", "scene_id", "block_id", name="uq_shot_script_source"),
    )
    op.create_index("ix_shot_script_source_scene_id", "shot_script_source", ["scene_id"])
    op.create_table(
        "scene_element",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("scene_id", UUID, sa.ForeignKey("scene.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("name_key", sa.Text(), nullable=False),
        sa.Column("asset_id", UUID, sa.ForeignKey("asset.id")),
        sa.Column("manual_binding", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("evidence", sa.Text(), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("direction", sa.Text(), nullable=False),
        sa.Column("reference_hash", sa.String(128), sa.ForeignKey("blob.hash")),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.UniqueConstraint("scene_id", "kind", "name_key", name="uq_scene_element"),
    )
    op.create_index("ix_scene_element_asset_id", "scene_element", ["asset_id"])
    db = op.get_bind()
    for script in db.execute(
        sa.text("""
        SELECT s.id,s.project_id,s.created_by,s.content_blocks,s.source_chapter_id,p.settings
        FROM script s JOIN project p ON p.id=s.project_id WHERE s.content_blocks <> '[]'::jsonb
        ORDER BY s.project_id,s.id
    """)
    ):
        _backfill(db, script)


def downgrade():
    db = op.get_bind()
    db.execute(
        sa.text("""
        UPDATE script s SET content_blocks=docs.blocks FROM (
          SELECT sc.script_id, jsonb_agg(b.value ORDER BY sc.script_position,b.ord) AS blocks
          FROM scene sc CROSS JOIN LATERAL jsonb_array_elements(sc.script_blocks)
               WITH ORDINALITY AS b(value,ord)
          WHERE sc.script_position IS NOT NULL AND sc.deleted_at IS NULL GROUP BY sc.script_id
        ) docs WHERE docs.script_id=s.id
    """)
    )
    for scene in db.execute(
        sa.text("SELECT id,script_blocks FROM scene WHERE script_blocks IS NOT NULL")
    ):
        db.execute(
            sa.text("UPDATE scene SET body=:body WHERE id=:id"),
            {"body": _body(scene.script_blocks), "id": scene.id},
        )
    op.drop_table("scene_element")
    op.drop_table("shot_script_source")
    op.drop_index("uq_scene_script_anchor", table_name="scene")
    op.drop_column("scene", "script_blocks")
    op.drop_column("scene", "script_anchor")
    op.drop_column("scene", "script_position")
    op.drop_column("scene", "elements_revision")
