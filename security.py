"""Helpers that keep credentials out of errors and logs."""

from collections.abc import Iterable


def redact(text: str, secrets: Iterable[str]) -> str:
    """Replace secret values with a fixed mask.

    Longer secrets are removed first so a short secret that happens to be
    part of a longer one cannot leave a fragment behind.
    """

    redacted = text
    unique = sorted({secret for secret in secrets if secret}, key=len, reverse=True)
    for secret in unique:
        redacted = redacted.replace(secret, "****")
    return redacted
