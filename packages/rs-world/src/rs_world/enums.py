"""Closed vocabularies of the helpdesk world.

They are `Literal` types on purpose: Pydantic turns them into an inline JSON-Schema `enum`, so every
tool's parameter schema lists the valid values (agents read them, and mutation tools can pick a
valid alternative value without parsing `$ref`s).
"""

from __future__ import annotations

from typing import Literal

Tier = Literal["standard", "silver", "gold"]
OrderStatus = Literal["pending", "processing", "shipped", "delivered", "returned", "cancelled", "refunded"]
OrderStatusTarget = Literal["processing", "shipped", "delivered", "returned", "cancelled"]
TicketStatus = Literal["open", "pending", "resolved", "closed"]
Priority = Literal["low", "normal", "high", "urgent"]
Decision = Literal["approved", "rejected"]
TicketCategory = Literal["damaged", "missing", "late", "refund", "refund_status", "cancel", "billing", "account"]

# Order status changes the app allows through `update_order_status`. "refunded" is reached only
# through `create_refund`, and "returned" / "cancelled" / "refunded" are terminal here.
ORDER_TRANSITIONS: dict[str, tuple[str, ...]] = {
    "pending": ("processing", "cancelled"),
    "processing": ("shipped", "cancelled"),
    "shipped": ("delivered",),
    "delivered": ("returned",),
}
