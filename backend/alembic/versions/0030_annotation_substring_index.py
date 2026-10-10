"""Index exact subtitle substrings, including two-character Chinese keywords."""

import sqlalchemy as sa

from alembic import op
from app.models.annotation_text_index import (
    INDEX_NAME,
    AnnotationBigrams,
    create_function,
    drop_function,
)

revision = "0030_annotation_substring_index"
down_revision = "0029_media_queue_fairness"
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    create_function(connection)
    table = sa.Table("media_annotation", sa.MetaData(), sa.Column("text", sa.Text()))
    sa.Index(
        INDEX_NAME,
        AnnotationBigrams(table.c.text, table=table),
        postgresql_using="gin",
    ).create(connection)


def downgrade():
    op.drop_index(INDEX_NAME, table_name="media_annotation")
    drop_function(op.get_bind())
