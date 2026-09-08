"""Constantes et utilitaires partagés par les outils métier."""

from __future__ import annotations

import os
import uuid

from sqlalchemy import select

from ..db import Order, OrderStatus


def _refund_cap() -> float:
    """Plafond de remboursement au-delà duquel l'agent escalade vers un humain.

    Le brief de déploiement range les **seuils des garde-fous** parmi les valeurs à
    externaliser : un plafond métier doit pouvoir être ajusté sans redéployer du code.

    Défaut à 50 € — la valeur historique — afin que rien ne change pour le développement,
    les tests ni l'évaluation. Une valeur illisible est ignorée plutôt que fatale : un
    seuil mal saisi dans un paramètre d'application ne doit pas empêcher l'agent de
    démarrer, il doit le laisser sur son comportement connu.
    """
    brut = os.getenv("VELMO_REFUND_CAP")
    if not brut:
        return 50.0
    try:
        return float(brut.strip().replace(",", "."))
    except ValueError:
        return 50.0


REFUND_CAP = _refund_cap()
# Une commande n'est modifiable / annulable que tant qu'elle n'est pas partie.
MODIFIABLE_STATUSES = {OrderStatus.paid, OrderStatus.prepared}
RETURNABLE_STATUSES = {OrderStatus.delivered}


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def owned_order(session, order_id: str, user_id: str) -> Order | None:
    """Renvoie la commande si elle appartient bien au client, sinon None (isolation R3)."""
    order = session.get(Order, order_id)
    if order is None or order.customer_id != user_id:
        return None
    return order


def order_to_dict(order: Order) -> dict:
    return {
        "order_id": order.id,
        "status": order.status.value,
        "total": float(order.total),
        "shipping_address": order.shipping_address,
        "items": [{"item_id": it.id, "variant_id": it.variant_id, "size": it.size.value} for it in order.items],
    }


__all__ = [
    "REFUND_CAP",
    "MODIFIABLE_STATUSES",
    "RETURNABLE_STATUSES",
    "new_id",
    "owned_order",
    "order_to_dict",
    "select",
]
