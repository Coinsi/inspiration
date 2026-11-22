"""Recover payments and refunds without holding database locks on network calls."""

import logging
from threading import Event, Thread

from sqlalchemy import select

from app.core.database import SessionLocal
from app.models.billing import BillingOrder, BillingProvider, BillingRefund
from app.modules.billing import orders, refunds

logger = logging.getLogger(__name__)
BATCH_LIMIT = 20


def reconcile_once(stop: Event | None = None) -> None:
    timestamp = orders.now()
    with SessionLocal() as db:
        order_ids = list(
            db.scalars(
                select(BillingOrder.id)
                .where(
                    BillingOrder.status.in_(("pending", "paid")),
                    BillingOrder.next_check_at <= timestamp,
                )
                .order_by(BillingOrder.next_check_at, BillingOrder.id)
                .limit(BATCH_LIMIT)
            )
        )
        refund_ids = list(
            db.scalars(
                select(BillingRefund.id)
                .join(BillingOrder, BillingOrder.id == BillingRefund.order_id)
                .join(BillingProvider, BillingProvider.id == BillingOrder.provider_id)
                .where(
                    BillingRefund.status == "pending",
                    BillingRefund.next_check_at <= timestamp,
                    BillingProvider.kind != "easypay",
                )
                .order_by(BillingRefund.next_check_at, BillingRefund.id)
                .limit(BATCH_LIMIT)
            )
        )
    for ids, check in ((order_ids, orders.check_order), (refund_ids, refunds.check_refund)):
        for ident in ids:
            if stop and stop.is_set():
                return
            with SessionLocal() as db:
                try:
                    check(db, ident, worker=True)
                    db.commit()
                except Exception as exc:
                    db.rollback()
                    logger.warning(
                        "Billing reconciliation failed id=%s error=%s", ident, type(exc).__name__
                    )


def start():
    stop = Event()

    def run():
        while not stop.wait(60):
            try:
                reconcile_once(stop)
            except Exception as exc:
                logger.warning("Billing polling failed error=%s", type(exc).__name__)

    thread = Thread(target=run, daemon=True, name="billing-reconcile")
    thread.start()

    def shutdown():
        stop.set()
        thread.join(timeout=8)

    return shutdown
