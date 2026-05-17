import pytest

from baseball_math import decimal_innings_to_outs, parse_mlb_innings_pitched


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("0.0", 0.0),
        ("0.1", 1 / 3),
        ("0.2", 2 / 3),
        ("6.0", 6.0),
        ("6.1", 6 + 1 / 3),
        ("6.2", 6 + 2 / 3),
        (None, 0.0),
        ("", 0.0),
    ],
)
def test_parse_mlb_innings_pitched(raw, expected):
    assert parse_mlb_innings_pitched(raw) == pytest.approx(expected)


def test_parse_mlb_innings_pitched_rejects_impossible_out_digit():
    with pytest.raises(ValueError):
        parse_mlb_innings_pitched("4.3")


def test_decimal_innings_to_outs():
    assert decimal_innings_to_outs(6 + 2 / 3) == 20
    assert decimal_innings_to_outs(None) == 0
