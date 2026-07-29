"""Small builders so tests read as scenarios rather than setup."""

import datetime as dt
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.utils import timezone

from apps.inventory.models import InventoryItem, ItemLot, LotStatus, UnitOfMeasure

User = get_user_model()


def make_user(username="tech", role="analyst", **kwargs):
    return User.objects.create_user(
        username=username, password="pw-not-used-in-tests", role=role, **kwargs
    )


def make_item(sku="PCR-MM-001", name="PCR Master Mix", reorder_point=0, **kwargs):
    return InventoryItem.objects.create(
        sku=sku,
        name=name,
        unit=kwargs.pop("unit", UnitOfMeasure.RXN),
        reorder_point=Decimal(reorder_point),
        reorder_quantity=Decimal(kwargs.pop("reorder_quantity", 100)),
        **kwargs,
    )


def make_lot(item, lot_number="L1", quantity=10, expires_in_days=90, received_days_ago=0, **kwargs):
    today = timezone.now().date()
    return ItemLot.objects.create(
        item=item,
        lot_number=lot_number,
        quantity_on_hand=Decimal(quantity),
        quantity_reserved=Decimal(kwargs.pop("reserved", 0)),
        expires_on=(today + dt.timedelta(days=expires_in_days)) if expires_in_days is not None else None,
        received_on=today - dt.timedelta(days=received_days_ago),
        status=kwargs.pop("status", LotStatus.AVAILABLE),
        **kwargs,
    )
