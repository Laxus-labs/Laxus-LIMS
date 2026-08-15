class SampleError(Exception):
    """Base class for sample domain errors."""


class IllegalTransition(SampleError):
    def __init__(self, sample_id, from_state, to_state):
        self.sample_id = sample_id
        self.from_state = from_state
        self.to_state = to_state
        super().__init__(
            f"Sample {sample_id} cannot move from '{from_state}' to '{to_state}'."
        )


class PositionOccupied(SampleError):
    """Another sample already holds that coordinate."""


class InvalidPosition(SampleError):
    """The coordinate does not exist in the target container's grid."""


class InsufficientVolume(SampleError):
    def __init__(self, sample_id, requested, available):
        super().__init__(
            f"Sample {sample_id} has {available} remaining; {requested} requested."
        )


class PermissionDenied(SampleError):
    """Actor lacks the capability for this operation."""


class ReasonRequired(SampleError):
    """A regulated action was attempted without a reason code."""


class RejectionReasonRequired(SampleError):
    """A rejection was attempted without a rejection reason code, or with
    a code that requires details (e.g. 'Other') but no details given."""
