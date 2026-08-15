class ReauthenticationFailed(Exception):
    """A signature was attempted with a password that did not match the
    signer's account. No ElectronicSignature row is written when this is
    raised — a failed re-auth attempt is not itself a signing event."""
