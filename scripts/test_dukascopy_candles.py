#!/usr/bin/env python3
"""Dukascopy 収集のオフラインテスト。ネットワークは使わない。"""

from __future__ import annotations

import lzma
import struct
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

import collect_dukascopy_candles as collector


def bi5(records: list[tuple[int, int, int, int, int, float]]) -> bytes:
    raw = b"".join(struct.pack(">5if", sec, open_, close, low, high, volume) for sec, open_, close, low, high, volume in records)
    return lzma.compress(raw)


def bar(minute: int, price: int, volume: float = 1.0) -> dict[str, object]:
    opened = datetime(2026, 10, 5, tzinfo=timezone.utc) + timedelta(minutes=minute)
    return {
        "time_utc": opened,
        "open": price,
        "high": price + 10,
        "low": price - 10,
        "close": price + 1,
        "tick_volume": volume,
    }


def frame_from(rows: list[dict[str, object]]) -> pd.DataFrame:
    frame = pd.DataFrame(rows)
    frame["time_utc"] = pd.to_datetime(frame["time_utc"], utc=True)
    return frame


class DecodeTests(unittest.TestCase):
    def test_field_order_scales_prices_and_drops_zero_volume(self) -> None:
        now = datetime(2026, 10, 6, tzinfo=timezone.utc)
        content = bi5(
            [
                (0, 62_655_366, 62_668_468, 62_648_315, 62_683_519, 1.638),
                (60, 1000, 1000, 1000, 1000, 0.0),
                (120, 10_000, 10_500, 9_500, 11_000, 0.25),
            ]
        )
        frame = collector.decode_m1(content, date(2026, 10, 5), now)
        self.assertEqual(len(frame), 2)
        self.assertEqual(collector.fmt_price(int(frame.iloc[0]["open"])), "62655.366")
        self.assertEqual(collector.fmt_price(int(frame.iloc[0]["close"])), "62668.468")
        self.assertEqual(collector.fmt_price(int(frame.iloc[0]["low"])), "62648.315")
        self.assertEqual(collector.fmt_price(int(frame.iloc[0]["high"])), "62683.519")
        # 2026-10-05 は米国東部の夏時間なので、保存時刻は UTC より 3 時間進む。
        self.assertEqual(collector.fmt_time(frame.iloc[0]["time_utc"]), "2026-10-05T03:00:00Z")
        self.assertEqual(collector.fmt_time(frame.iloc[1]["time_utc"]), "2026-10-05T03:02:00Z")

    def test_clock_shift_follows_us_eastern_dst(self) -> None:
        winter = datetime(2026, 1, 20, 12, 0, tzinfo=timezone.utc)
        summer = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)
        november = datetime(2026, 11, 2, 1, 0, tzinfo=timezone.utc)
        self.assertEqual(collector.axiory_clock_shift(winter), timedelta(hours=2))
        self.assertEqual(collector.axiory_clock_shift(summer), timedelta(hours=3))
        self.assertEqual(collector.axiory_clock_shift(november), timedelta(hours=2))

    def test_unconfirmed_bar_is_dropped(self) -> None:
        now = datetime(2026, 10, 5, 0, 1, 30, tzinfo=timezone.utc)
        content = bi5(
            [
                (0, 1000, 1000, 1000, 1000, 1.0),
                (60, 2000, 2000, 2000, 2000, 1.0),
            ]
        )
        frame = collector.decode_m1(content, date(2026, 10, 5), now)
        self.assertEqual(list(frame["open"]), [1000])

    def test_bad_ohlc_raises(self) -> None:
        now = datetime(2026, 10, 6, tzinfo=timezone.utc)
        content = bi5([(0, 1000, 5000, 4000, 3000, 1.0)])
        with self.assertRaises(collector.DayFetchError):
            collector.decode_m1(content, date(2026, 10, 5), now)


class ResampleTests(unittest.TestCase):
    def test_m5_uses_first_open_and_last_close(self) -> None:
        rows = [bar(0, 1000), bar(1, 1100), bar(4, 1200), bar(5, 2000)]
        frame = frame_from(rows)
        grouped = collector.aggregate(frame, frame["time_utc"].dt.floor("5min"))
        self.assertEqual(len(grouped), 2)
        self.assertEqual(int(grouped.iloc[0]["open"]), 1000)
        self.assertEqual(int(grouped.iloc[0]["close"]), 1201)
        self.assertEqual(int(grouped.iloc[0]["high"]), 1210)
        self.assertEqual(int(grouped.iloc[0]["low"]), 990)
        self.assertEqual(collector.fmt_time(grouped.iloc[1]["time_utc"]), "2026-10-05T00:05:00Z")

    def test_d1_starts_at_utc_midnight_and_w1_starts_sunday(self) -> None:
        # 2026-10-04 is Sunday. Include the following Sunday so the first week can be complete.
        start = datetime(2026, 10, 4, 23, 0, tzinfo=timezone.utc)
        rows = []
        for offset in range(8):
            opened = start + timedelta(days=offset)
            rows.append(
                {
                    "time_utc": opened,
                    "open": 1000 + offset,
                    "high": 2000 + offset,
                    "low": 500 + offset,
                    "close": 1500 + offset,
                    "tick_volume": 1.5,
                }
            )
        frame = frame_from(rows)
        now = datetime(2026, 10, 20, tzinfo=timezone.utc)
        covered = {date(2026, 10, 3) + timedelta(days=offset) for offset in range(9)}
        higher = collector.build_higher_timeframes(frame, covered, now)
        d1_times = [collector.fmt_time(value) for value in higher["D1"]["time_utc"]]
        self.assertEqual(d1_times[0], "2026-10-04T00:00:00Z")
        self.assertEqual(collector.fmt_time(higher["W1"]["time_utc"].iloc[0]), "2026-10-04T00:00:00Z")
        self.assertEqual(len(higher["W1"]), 1)
        self.assertEqual(int(higher["W1"].iloc[0]["open"]), 1000)
        self.assertEqual(int(higher["W1"].iloc[0]["close"]), 1506)

    def test_incomplete_week_is_omitted(self) -> None:
        opened = datetime(2026, 10, 5, 0, 0, tzinfo=timezone.utc)  # Monday
        frame = frame_from(
            [
                {
                    "time_utc": opened,
                    "open": 1000,
                    "high": 1000,
                    "low": 1000,
                    "close": 1000,
                    "tick_volume": 1,
                }
            ]
        )
        now = datetime(2026, 10, 20, tzinfo=timezone.utc)
        covered = {date(2026, 10, 4), date(2026, 10, 5)}
        higher = collector.build_higher_timeframes(frame, covered, now)
        self.assertTrue(higher["W1"].empty)
        self.assertEqual(collector.fmt_time(higher["D1"]["time_utc"].iloc[0]), "2026-10-05T00:00:00Z")

    def test_partial_axiory_day_is_not_a_daily_bar(self) -> None:
        opened = datetime(2026, 11, 3, 0, 30, tzinfo=timezone.utc)
        frame = frame_from(
            [
                {
                    "time_utc": opened,
                    "open": 1000,
                    "high": 1000,
                    "low": 1000,
                    "close": 1000,
                    "tick_volume": 1,
                }
            ]
        )
        now = datetime(2026, 11, 20, tzinfo=timezone.utc)
        higher = collector.build_higher_timeframes(frame, {date(2026, 11, 2)}, now)
        self.assertTrue(higher["D1"].empty)

    def test_sunday_alignment_matches_axiory_sample(self) -> None:
        times = pd.to_datetime(
            ["2026-10-25T00:00:00Z", "2026-10-31T23:59:00Z", "2026-11-01T00:00:00Z"],
            utc=True,
        )
        starts = collector.week_start_sunday(pd.Series(times))
        self.assertEqual([collector.fmt_time(value) for value in starts], [
            "2026-10-25T00:00:00Z",
            "2026-10-25T00:00:00Z",
            "2026-11-01T00:00:00Z",
        ])


class StoreTests(unittest.TestCase):
    def test_merge_keeps_latest_duplicate_and_month_files_are_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = frame_from([bar(0, 1000, 1.25)])
            second = frame_from([bar(0, 3000, 2.5), bar(10, 4000, 1)])
            merged = collector.merge_bars(first, second)
            self.assertEqual(len(merged), 2)
            self.assertEqual(int(merged.iloc[0]["open"]), 3000)
            months = set(merged["time_utc"].dt.strftime("%Y-%m"))
            self.assertTrue(collector.flush_months(root, "JP225", merged, months))
            self.assertFalse(collector.flush_months(root, "JP225", merged, months))
            text = (root / "m1" / "JP225_M1_2026-10.csv").read_text(encoding="utf-8")
            self.assertTrue(text.startswith("time_utc,open,high,low,close,tick_volume,spread,real_volume\n"))
            self.assertIn("2026-10-05T00:00:00Z,3,3.01,2.99,3.001,2.5,0,0\n", text)
            reloaded = collector.load_m1(root, "JP225")
            self.assertEqual(int(reloaded.iloc[0]["open"]), 3000)

    def test_plan_catches_recent_gap_but_not_an_old_hole(self) -> None:
        covered = {date(2026, 10, 1) + timedelta(days=offset) for offset in range(10)}
        covered.remove(date(2026, 10, 5))
        days = collector.plan_dates(
            covered,
            backfill_from=None,
            default_start=date(2026, 10, 1),
            end=date(2026, 10, 12),
        )
        self.assertNotIn(date(2026, 10, 5), days)
        self.assertIn(date(2026, 10, 11), days)
        self.assertIn(date(2026, 10, 12), days)
        self.assertNotIn(date(2026, 10, 1), days)

    def test_max_days_takes_the_oldest_pending_days(self) -> None:
        days = collector.plan_dates(
            set(),
            backfill_from=date(2026, 10, 1),
            default_start=date(2026, 10, 1),
            end=date(2026, 10, 20),
            max_days=5,
        )
        self.assertEqual(
            days,
            [
                date(2026, 9, 30),
                date(2026, 10, 1),
                date(2026, 10, 2),
                date(2026, 10, 3),
                date(2026, 10, 4),
            ],
        )

    def test_backfill_includes_previous_utc_day(self) -> None:
        days = collector.plan_dates(
            set(),
            backfill_from=date(2026, 10, 1),
            default_start=date(2026, 10, 1),
            end=date(2026, 10, 3),
        )
        self.assertEqual(days[0], date(2026, 9, 30))
        self.assertEqual(days[-1], date(2026, 10, 3))

    def test_backfill_skips_covered_days_except_overlap(self) -> None:
        days = collector.plan_dates(
            {date(2026, 10, 1), date(2026, 10, 2)},
            backfill_from=date(2026, 10, 1),
            default_start=date(2026, 10, 1),
            end=date(2026, 10, 4),
        )
        self.assertIn(date(2026, 9, 30), days)
        self.assertNotIn(date(2026, 10, 1), days)
        self.assertNotIn(date(2026, 10, 2), days)
        self.assertIn(date(2026, 10, 3), days)
        self.assertIn(date(2026, 10, 4), days)

    def test_recent_404_is_not_frozen_as_empty(self) -> None:
        end = date(2026, 11, 3)
        self.assertFalse(collector.missing_file_is_trusted_empty(date(2026, 11, 3), end))
        self.assertFalse(collector.missing_file_is_trusted_empty(date(2026, 11, 2), end))
        self.assertTrue(collector.missing_file_is_trusted_empty(date(2026, 11, 1), end))

    def test_refuses_axiory_directory(self) -> None:
        with self.assertRaises(ValueError):
            collector.refuse_axiory_output(collector.AXIORY_DIR)


class ReportFallbackTests(unittest.TestCase):
    def test_auto_source_keeps_axiory_when_present(self) -> None:
        import update_market_context as journal

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            axiory = root / "axiory"
            duka = root / "dukascopy"
            axiory.mkdir()
            duka.mkdir()
            header = "time_utc,open,high,low,close,tick_volume,spread,real_volume\n"
            row = "2026-10-05T00:00:00Z,1,2,0.5,1.5,3,0,0\n"
            for timeframe in ("M5", "H1", "D1", "W1"):
                (axiory / f"JP225_{timeframe}.csv").write_text(header + row, encoding="utf-8")
                (duka / f"JP225_{timeframe}.csv").write_text(header + row, encoding="utf-8")
            original_bars = journal.BARS_DIR
            original_duka = journal.DUKASCOPY_BARS_DIR
            journal.BARS_DIR = axiory
            journal.DUKASCOPY_BARS_DIR = duka
            try:
                self.assertEqual(journal.resolve_bar_source("JP225", "auto"), "axiory")
                self.assertEqual(journal.resolve_bar_source("JP225", "dukascopy"), "dukascopy")
            finally:
                journal.BARS_DIR = original_bars
                journal.DUKASCOPY_BARS_DIR = original_duka

    def test_auto_source_falls_back_when_axiory_missing(self) -> None:
        import update_market_context as journal

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            axiory = root / "axiory"
            duka = root / "dukascopy"
            axiory.mkdir()
            duka.mkdir()
            header = "time_utc,open,high,low,close,tick_volume,spread,real_volume\n"
            row = "2026-10-05T00:00:00Z,1,2,0.5,1.5,3,0,0\n"
            for timeframe in ("M5", "H1", "D1", "W1"):
                (duka / f"US100_{timeframe}.csv").write_text(header + row, encoding="utf-8")
            original_bars = journal.BARS_DIR
            original_duka = journal.DUKASCOPY_BARS_DIR
            journal.BARS_DIR = axiory
            journal.DUKASCOPY_BARS_DIR = duka
            try:
                self.assertEqual(journal.resolve_bar_source("US100", "auto"), "dukascopy")
            finally:
                journal.BARS_DIR = original_bars
                journal.DUKASCOPY_BARS_DIR = original_duka


if __name__ == "__main__":
    unittest.main()
