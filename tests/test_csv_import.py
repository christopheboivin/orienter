from datetime import date

import pytest

from orienter.sources.csv_import import CsvImportError, parse_steps_csv

# Shape of a real Garmin Connect export (French UI, US date order, BOM, no final newline).
GARMIN_FR = "﻿,Réel,Objectif\n09/16/2026,15663,12740\n09/17/2026,3708,13040\n10/13/2026,10155,12640"


def test_garmin_export():
    counts = parse_steps_csv(GARMIN_FR)
    assert counts[date(2026, 9, 16)] == 15663
    assert counts[date(2026, 10, 13)] == 10155
    assert len(counts) == 3


def test_day_first_order_detected_from_consecutive_days():
    text = ",Actual,Goal\n12/10/2026,100,0\n13/10/2026,200,0\n14/10/2026,300,0\n"
    assert parse_steps_csv(text) == {
        date(2026, 10, 12): 100,
        date(2026, 10, 13): 200,
        date(2026, 10, 14): 300,
    }


def test_ambiguous_dates_prefer_consecutive_reading():
    # 10/11 then 10/12 is consecutive as MM/DD, not as DD/MM
    counts = parse_steps_csv(",Actual\n10/11/2026,1\n10/12/2026,2\n")
    assert set(counts) == {date(2026, 10, 11), date(2026, 10, 12)}


def test_thousands_separators():
    assert parse_steps_csv(',Actual\n2026-10-01,"15,663"\n')[date(2026, 10, 1)] == 15663


def test_weekly_export_rejected():
    with pytest.raises(CsvImportError, match="4-week"):
        parse_steps_csv(",Actual\n2026-09-01,1\n2026-09-08,2\n2026-09-15,3\n")


def test_garbage_rejected():
    with pytest.raises(CsvImportError):
        parse_steps_csv("hello,world\n")
