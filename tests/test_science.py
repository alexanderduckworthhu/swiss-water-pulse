import math
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from src.credits import footer_line, load_credit_table
import xarray as xr

from src.process.anomaly import log_anomaly, year_median
from src.process.captions import assign_river_names, _callout_text
from src.process.series import _stamp_as_accumulation_day
from src.process.snap import apply_discharge, donor_reaches, snap_reaches
from src.render.colors import anomaly_rgb, rgb


class SnapTests(unittest.TestCase):
    def test_accepts_a_close_area_and_rejects_a_far_one(self):
        grid_lons = np.array([8.0, 8.05, 8.10])
        grid_lats = np.array([47.0, 47.05, 47.10])
        grid = np.full((3, 3), np.nan)
        grid[1, 1] = 1000.0
        grid[0, 0] = 50.0
        result = snap_reaches(
            np.array([8.05, 8.0]),
            np.array([47.05, 47.0]),
            np.array([1100.0, 10.0]),
            grid_lons,
            grid_lats,
            grid,
            max_ratio=2.0,
            window=3,
        )
        self.assertTrue(result["matched"][0])
        self.assertFalse(result["matched"][1])
        self.assertAlmostEqual(result["cell_area"][0], 1000.0)

    def test_interpolation_scales_by_area(self):
        matched = np.array([False, True])
        nxt = np.array([1, -1])
        donor = donor_reaches(matched, nxt)
        self.assertEqual(int(donor[0]), 1)
        cell = np.zeros((1, 2, 2))
        cell[0, 0, 1] = 100.0
        q = apply_discharge(
            cell,
            cell_i=np.array([-1, 0]),
            cell_j=np.array([-1, 1]),
            donor=donor,
            areas=np.array([25.0, 100.0]),
            matched=matched,
        )
        self.assertAlmostEqual(float(q[0, 0]), 25.0)
        self.assertAlmostEqual(float(q[1, 0]), 100.0)


class CaptionTests(unittest.TestCase):
    def test_names_follow_the_network(self):
        names = assign_river_names(["", "Rhine", ""], [1, -1, 1])
        self.assertEqual(list(names), ["Rhine", "Rhine", "Rhine"])
        kept = assign_river_names(["Emme", "Rhine"], [1, -1])
        self.assertEqual(list(kept), ["Emme", "Rhine"])

    def test_unnamed_place_is_dropped(self):
        self.assertEqual(_callout_text("Flood peak — {river} near {place}", "Rhine", ""), "Flood peak — Rhine")
        self.assertEqual(
            _callout_text("Flood peak — {river} near {place}", "Rhine", "Basel"),
            "Flood peak — Rhine near Basel",
        )


class SeriesTests(unittest.TestCase):
    def test_midnight_stamp_is_the_previous_day(self):
        array = xr.DataArray(
            np.ones((1, 1, 1)),
            dims=("time", "latitude", "longitude"),
            coords={"time": [np.datetime64("2025-06-16T00:00:00")]},
        )
        stamped = _stamp_as_accumulation_day(array)
        self.assertEqual(pd.Timestamp(stamped.time.values[0]), pd.Timestamp("2025-06-15"))


class AnomalyTests(unittest.TestCase):
    def test_clip_and_usual(self):
        q = np.array([[10.0, 100.0, 1.0]])
        anomaly = log_anomaly(q, np.array([10.0]), clip=1.0)
        self.assertAlmostEqual(float(anomaly[0, 0]), 0.0, places=5)
        self.assertAlmostEqual(float(anomaly[0, 1]), 1.0, places=5)
        self.assertAlmostEqual(float(anomaly[0, 2]), -1.0, places=5)

    def test_year_median(self):
        q = np.array([[1.0, 3.0, np.nan, 5.0]])
        self.assertAlmostEqual(float(year_median(q)[0]), 3.0)


class ColorTests(unittest.TestCase):
    def test_ends_of_the_ramp(self):
        low, mid, high = rgb("#7a4e32"), rgb("#f3e6c8"), rgb("#1b3a6b")
        self.assertEqual(anomaly_rgb(0, low, mid, high), mid)
        self.assertEqual(anomaly_rgb(-1, low, mid, high), low)
        self.assertEqual(anomaly_rgb(1, low, mid, high), high)
        self.assertEqual(anomaly_rgb(4, low, mid, high), high)


class CreditTests(unittest.TestCase):
    def test_every_credit_is_documented(self):
        table = load_credit_table(Path(__file__).resolve().parents[1] / "SOURCES.md")
        body = (Path(__file__).resolve().parents[1] / "SOURCES.md").read_text().lower()
        for item in table["credits"]:
            self.assertIn(item["id"], body)
        line = footer_line(["swisstopo", "glamos"], copyright_text="© 2026 Test")
        self.assertIn("swisstopo", line)
        self.assertIn("© 2026 Test", line)

    def test_unknown_source_is_refused(self):
        with self.assertRaises(RuntimeError):
            footer_line(["not-a-source"])


class DayTests(unittest.TestCase):
    def test_span(self):
        from src.cli import _days

        days = _days("2025-06-01:2025-06-03", 2025)
        self.assertEqual(len(days), 3)
        self.assertEqual(days[0], __import__("datetime").date(2025, 6, 1))


if __name__ == "__main__":
    unittest.main()
