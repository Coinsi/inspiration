"""Create payment orders and the personal points ledger."""

import sqlalchemy as sa

from alembic import op

revision = "0064_billing"
down_revision = "0063_clip_spacing"
branch_labels = depends_on = None


def upgrade():
    op.execute("""
        CREATE TABLE billing_settings (
            id SERIAL NOT NULL,
            enabled BOOLEAN NOT NULL,
            payment_required BOOLEAN NOT NULL,
            public_origin VARCHAR(300) NOT NULL,
            checkout_minutes INTEGER NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
            updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
            PRIMARY KEY (id),
            CONSTRAINT ck_billing_settings_singleton CHECK (id = 1),
            CONSTRAINT ck_billing_checkout_minutes CHECK (checkout_minutes between 35 and 120)
        )
    """)
    op.execute("""
        CREATE TABLE billing_pack (
            id UUID NOT NULL,
            name VARCHAR(100) NOT NULL,
            description VARCHAR(500) NOT NULL,
            currency VARCHAR(3) NOT NULL,
            amount_minor BIGINT NOT NULL,
            credits BIGINT NOT NULL,
            active BOOLEAN NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
            updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
            PRIMARY KEY (id),
            CONSTRAINT ck_billing_pack_amounts CHECK (amount_minor > 0 and credits > 0)
        )
    """)
    op.execute("""
        CREATE TABLE billing_provider (
            id UUID NOT NULL,
            name VARCHAR(100) NOT NULL,
            kind VARCHAR(32) NOT NULL,
            currency VARCHAR(3) NOT NULL,
            config_enc BYTEA NOT NULL,
            active BOOLEAN NOT NULL,
            allow_refunds BOOLEAN NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
            updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
            PRIMARY KEY (id)
        )
    """)
    op.execute("""
        CREATE TABLE billing_order (
            id UUID NOT NULL,
            user_id UUID NOT NULL,
            pack_id UUID NOT NULL,
            provider_id UUID NOT NULL,
            idempotency_key UUID NOT NULL,
            pack_name VARCHAR(100) NOT NULL,
            provider_name VARCHAR(100) NOT NULL,
            public_origin VARCHAR(300) NOT NULL,
            currency VARCHAR(3) NOT NULL,
            amount_minor BIGINT NOT NULL,
            credits BIGINT NOT NULL,
            status VARCHAR(24) NOT NULL,
            provider_order_id VARCHAR(200),
            payment_id VARCHAR(200),
            checkout_url TEXT,
            qr_payload TEXT,
            expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
            next_check_at TIMESTAMP WITH TIME ZONE NOT NULL,
            paid_at TIMESTAMP WITH TIME ZONE,
            completed_at TIMESTAMP WITH TIME ZONE,
            refunded_minor BIGINT NOT NULL,
            refunded_credits BIGINT NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
            updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
            PRIMARY KEY (id),
            CONSTRAINT uq_billing_order_idempotency UNIQUE (user_id, idempotency_key),
            CONSTRAINT uq_billing_provider_order UNIQUE (provider_id, provider_order_id),
            CONSTRAINT uq_billing_provider_payment UNIQUE (provider_id, payment_id),
            CONSTRAINT ck_billing_order_amounts CHECK (
                amount_minor > 0 and credits > 0 and refunded_minor between 0 and amount_minor
                and refunded_credits between 0 and credits),
            CONSTRAINT ck_billing_order_status CHECK (
                status in ('pending','paid','completed','failed','expired','cancelled')),
            FOREIGN KEY(user_id) REFERENCES "user" (id),
            FOREIGN KEY(pack_id) REFERENCES billing_pack (id),
            FOREIGN KEY(provider_id) REFERENCES billing_provider (id)
        )
    """)
    op.execute("CREATE INDEX ix_billing_order_reconcile ON billing_order (status, next_check_at)")
    op.execute("CREATE INDEX ix_billing_order_user_id ON billing_order (user_id)")
    op.execute("""
        CREATE TABLE billing_refund (
            id UUID NOT NULL,
            order_id UUID NOT NULL,
            requested_by UUID NOT NULL,
            idempotency_key UUID NOT NULL,
            amount_minor BIGINT NOT NULL,
            credits BIGINT NOT NULL,
            reason VARCHAR(500) NOT NULL,
            status VARCHAR(24) NOT NULL,
            provider_refund_id VARCHAR(200),
            submitted_at TIMESTAMP WITH TIME ZONE,
            next_check_at TIMESTAMP WITH TIME ZONE NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
            updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
            PRIMARY KEY (id),
            CONSTRAINT uq_billing_refund_idempotency UNIQUE (order_id, idempotency_key),
            CONSTRAINT ck_billing_refund_amounts CHECK (amount_minor > 0 and credits >= 0),
            CONSTRAINT ck_billing_refund_status CHECK (
                status in ('requested','pending','succeeded','failed','rejected')),
            FOREIGN KEY(order_id) REFERENCES billing_order (id),
            FOREIGN KEY(requested_by) REFERENCES "user" (id)
        )
    """)
    op.execute("CREATE INDEX ix_billing_refund_order_id ON billing_refund (order_id)")
    op.execute("CREATE INDEX ix_billing_refund_reconcile ON billing_refund (status, next_check_at)")
    op.execute("""
        CREATE TABLE credit_account (
            user_id UUID NOT NULL,
            available BIGINT NOT NULL,
            reserved BIGINT NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
            updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
            PRIMARY KEY (user_id),
            CONSTRAINT ck_credit_account_balance CHECK (available >= 0 and reserved >= 0),
            FOREIGN KEY(user_id) REFERENCES "user" (id)
        )
    """)
    op.execute("""
        CREATE TABLE credit_entry (
            id UUID NOT NULL,
            user_id UUID NOT NULL,
            kind VARCHAR(32) NOT NULL,
            reference_id UUID NOT NULL,
            available_delta BIGINT NOT NULL,
            reserved_delta BIGINT NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
            updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
            PRIMARY KEY (id),
            CONSTRAINT uq_credit_entry_operation UNIQUE (kind, reference_id),
            FOREIGN KEY(user_id) REFERENCES credit_account (user_id)
        )
    """)
    op.execute("CREATE INDEX ix_credit_entry_user_created ON credit_entry (user_id, created_at)")
    op.execute("""
        CREATE TABLE credit_reservation (
            job_id UUID NOT NULL,
            user_id UUID NOT NULL,
            units BIGINT NOT NULL,
            settled_units BIGINT,
            status VARCHAR(16) NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
            updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
            PRIMARY KEY (job_id),
            CONSTRAINT ck_credit_reservation_units CHECK (units > 0 and settled_units >= 0),
            CONSTRAINT ck_credit_reservation_status CHECK (
                status in ('reserved','settled','released')),
            FOREIGN KEY(job_id) REFERENCES generation_job (id),
            FOREIGN KEY(user_id) REFERENCES credit_account (user_id)
        )
    """)


def downgrade():
    conn = op.get_bind()
    for table in ("billing_order", "billing_refund", "credit_entry"):
        if conn.scalar(sa.text(f"SELECT EXISTS (SELECT 1 FROM {table})")):
            raise RuntimeError(
                "Payment history exists. Back up and migrate it before removing billing."
            )
    op.drop_table("credit_reservation")
    op.drop_table("credit_entry")
    op.drop_table("credit_account")
    op.drop_table("billing_refund")
    op.drop_table("billing_order")
    op.drop_table("billing_provider")
    op.drop_table("billing_pack")
    op.drop_table("billing_settings")
