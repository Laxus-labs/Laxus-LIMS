class InventoryError(Exception):
    """Base class for inventory domain errors."""


class InsufficientStock(InventoryError):
    def __init__(self, item_sku: str, requested, available):
        self.item_sku = item_sku
        self.requested = requested
        self.available = available
        super().__init__(
            f"Insufficient stock for {item_sku}: requested {requested}, available {available}."
        )


class NoAllocatableLots(InventoryError):
    """Stock exists on paper but no lot is in an allocatable state."""


class ReservationNotHeld(InventoryError):
    """Attempted to consume or release a reservation that is already settled."""


class PermissionDenied(InventoryError):
    """Actor lacks the capability for this operation."""
