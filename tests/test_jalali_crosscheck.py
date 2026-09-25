"""Cross-check: jdatetime vs an independent hand-written Jalali rule, over the catalog's date range.

The hand-written rule below exists ONLY in this test. Project code must use jdatetime.
The catalog's dates run from 1401-07-24 to 1405-07-01 (docs/data_dictionary.md §2); the check
covers every calendar day of 1401-1405, plus every impossible (month, day) combination.
Any disagreement fails the test.
"""
import jdatetime

YEARS = range(1401, 1406)
LEAP_REMAINDERS = {1, 5, 9, 13, 17, 22, 26, 30}  # 33-year-cycle approximation, test only


def hand_is_leap(y):
    return y % 33 in LEAP_REMAINDERS


def hand_month_len(y, m):
    return 31 if m <= 6 else 30 if m <= 11 else (30 if hand_is_leap(y) else 29)


def hand_next_day(y, m, d):
    if d < hand_month_len(y, m):
        return y, m, d + 1
    if m < 12:
        return y, m + 1, 1
    return y + 1, 1, 1


def lib_is_valid(y, m, d):
    try:
        jdatetime.date(y, m, d)
        return True
    except ValueError:
        return False


def test_leap_years_agree():
    for y in YEARS:
        assert jdatetime.date(y, 1, 1).isleap() == hand_is_leap(y), y


def test_validity_agrees_for_every_month_day_combination():
    for y in YEARS:
        for m in range(1, 13):
            for d in range(1, 32):
                hand = d <= hand_month_len(y, m)
                assert lib_is_valid(y, m, d) == hand, (y, m, d)


def test_next_day_agrees_for_every_valid_date():
    checked = 0
    for y in YEARS:
        for m in range(1, 13):
            for d in range(1, hand_month_len(y, m) + 1):
                nxt = jdatetime.date(y, m, d) + jdatetime.timedelta(days=1)
                assert (nxt.year, nxt.month, nxt.day) == hand_next_day(y, m, d), (y, m, d)
                checked += 1
    # 5 years, one of them (1403) leap: 4 * 365 + 366
    assert checked == 4 * 365 + 366
