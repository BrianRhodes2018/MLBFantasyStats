"""Shared baseball-specific numeric helpers."""


def parse_mlb_innings_pitched(value) -> float:
    """
    Convert MLB Stats API innings notation into true decimal innings.

    MLB returns innings as strings such as "6.1" and "6.2", where the
    fractional digit is outs, not tenths. This returns 6.333... and 6.666...
    so rate stats such as K/9, BB/9, HR/9, ERA, and WHIP are mathematically
    correct.
    """
    if value is None:
        return 0.0

    raw = str(value).strip()
    if not raw or raw == "-":
        return 0.0

    whole, separator, fraction = raw.partition(".")
    try:
        whole_innings = int(whole or "0")
    except ValueError:
        return 0.0

    if not separator:
        return float(whole_innings)

    outs = int((fraction or "0")[0])
    if outs not in (0, 1, 2):
        raise ValueError(f"Invalid MLB innings pitched value: {value!r}")

    return whole_innings + (outs / 3)


def decimal_innings_to_outs(value) -> int:
    """Convert true decimal innings to total outs."""
    if value is None:
        return 0
    return int(round(float(value) * 3))
