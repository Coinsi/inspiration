"""Cover production state with the existing opt-in CAS reference guards."""

from alembic import op

revision = "0053_harness_retention_guards"
down_revision = "0052_transcription_execution"
branch_labels = None
depends_on = None


def upgrade():
    from app.storage.gc_guards import install_v1

    # The runtime migration introduced four new JSON reference roots after the
    # original guard installation. Reinstall on every current table so GC can
    # verify complete coverage before it locks and reclaims any physical object.
    install_v1(op.get_bind())


def downgrade():
    # The trigger is backward-compatible and protects references independently
    # of harness execution. Later table downgrades remove their own triggers;
    # existing tables must keep their original reclamation protection.
    pass
