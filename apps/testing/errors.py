class TestingError(Exception):
    """Base class for testing-domain errors."""


class IllegalOrderTransition(TestingError):
    def __init__(self, order_id, from_status, to_status):
        self.order_id = order_id
        self.from_status = from_status
        self.to_status = to_status
        super().__init__(
            f"Test order {order_id} cannot move from '{from_status}' to '{to_status}'."
        )


class ReasonRequired(TestingError):
    """A regulated action was attempted without a reason."""


class PermissionDenied(TestingError):
    """Actor lacks the capability for this operation."""


class ResultNotReady(TestingError):
    """An action was attempted on a TestResult in the wrong status."""


class IncompleteCertificate(TestingError):
    """A COA was requested for a sample with no approved results, or with
    outstanding (non-terminal) test orders."""
