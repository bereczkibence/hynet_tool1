from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

import pandas as pd

from acdcpf_opf.data.time_profiles import (
    ProfileSnapshot,
    apply_profile_snapshot,
    load_time_profile,
    profile_snapshots_from_frame,
)


ONE_DAY_PROFILE_PATH = (
    Path(__file__).resolve().parents[1]
    / "benchmarks"
    / "stagg5"
    / "profiles"
    / "stagg5_one_day_ac_load_profile.csv"
)
HYBRID_ONE_DAY_PROFILE_PATH = (
    Path(__file__).resolve().parents[1]
    / "benchmarks"
    / "stagg5"
    / "profiles"
    / "hybrid_dcdc_one_day_load_pv_profile.csv"
)


def _build_stagg5_network():
    from acdcpf.networks import create_case5_stagg_mtdc_slack

    return create_case5_stagg_mtdc_slack()


def _build_hybrid_network():
    from acdcpf_opf.benchmarks.stagg5.case_variants import (
        create_case5_stagg_mtdc_hybrid_dcdc,
    )

    return create_case5_stagg_mtdc_hybrid_dcdc()


class TimeProfileParsingTests(unittest.TestCase):
    def test_parse_dataframe_groups_rows_by_time_index(self) -> None:
        frame = pd.DataFrame(
            [
                {
                    "time_index": 1,
                    "timestamp": "2026-01-01T01:00:00",
                    "element_type": "ac_load",
                    "element_id": 0,
                    "scale": 1.1,
                },
                {
                    "time_index": 0,
                    "timestamp": "2026-01-01T00:00:00",
                    "element_type": "dc_gen",
                    "element_name": "PV DC Source",
                    "p_mw": 12.0,
                },
            ]
        )

        snapshots = profile_snapshots_from_frame(frame)

        self.assertEqual([snapshot.time_index for snapshot in snapshots], [0, 1])
        self.assertEqual(snapshots[0].timestamp, "2026-01-01T00:00:00")
        self.assertEqual(snapshots[0].changes[0].element_name, "PV DC Source")
        self.assertEqual(snapshots[1].changes[0].scale, 1.1)

    def test_load_csv_profile_with_semicolon_delimiter(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "profile.csv"
            path.write_text(
                "\n".join(
                    [
                        "time_index;timestamp;element_type;element_id;scale",
                        "0;2026-01-01T00:00:00;ac_load;0;0.9",
                        "1;2026-01-01T01:00:00;ac_load;0;1.1",
                    ]
                ),
                encoding="utf-8",
            )

            snapshots = load_time_profile(path)

        self.assertEqual(len(snapshots), 2)
        self.assertEqual(snapshots[0].changes[0].scale, 0.9)
        self.assertEqual(snapshots[1].changes[0].scale, 1.1)

    def test_load_xlsx_profile(self) -> None:
        try:
            import openpyxl  # noqa: F401
        except ImportError:
            self.skipTest("openpyxl is not installed")

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "profile.xlsx"
            pd.DataFrame(
                [
                    {
                        "time_index": 0,
                        "element_type": "ac_load",
                        "element_id": 0,
                        "p_scale": 1.05,
                        "q_scale": 0.95,
                    }
                ]
            ).to_excel(path, index=False)

            snapshots = load_time_profile(path, profile_format="xlsx")

        self.assertEqual(len(snapshots), 1)
        self.assertEqual(snapshots[0].changes[0].p_scale, 1.05)
        self.assertEqual(snapshots[0].changes[0].q_scale, 0.95)

    def test_load_bundled_one_day_profile(self) -> None:
        snapshots = load_time_profile(ONE_DAY_PROFILE_PATH)

        self.assertEqual(len(snapshots), 24)
        self.assertTrue(all(len(snapshot.changes) == 4 for snapshot in snapshots))
        self.assertEqual(snapshots[0].timestamp, "2026-01-01T00:00:00")
        self.assertAlmostEqual(snapshots[0].changes[0].scale, 0.62)
        self.assertAlmostEqual(snapshots[19].changes[0].scale, 1.20)

    def test_load_bundled_hybrid_one_day_profile(self) -> None:
        snapshots = load_time_profile(HYBRID_ONE_DAY_PROFILE_PATH)

        self.assertEqual(len(snapshots), 24)
        self.assertTrue(all(len(snapshot.changes) == 5 for snapshot in snapshots))
        dc_pv_curve = [
            next(change.p_mw for change in snapshot.changes if change.element_type == "dc_gen")
            for snapshot in snapshots
        ]
        self.assertEqual(dc_pv_curve[0], 0.0)
        self.assertEqual(dc_pv_curve[12], 30.0)
        self.assertEqual(dc_pv_curve[23], 0.0)

    def test_rejects_ambiguous_scale_and_absolute_power(self) -> None:
        frame = pd.DataFrame(
            [
                {
                    "time_index": 0,
                    "element_type": "ac_load",
                    "element_id": 0,
                    "scale": 1.1,
                    "p_mw": 30.0,
                }
            ]
        )

        with self.assertRaisesRegex(ValueError, "ambiguous"):
            profile_snapshots_from_frame(frame)

    def test_rejects_duplicate_target_in_same_snapshot(self) -> None:
        frame = pd.DataFrame(
            [
                {"time_index": 0, "element_type": "ac_load", "element_id": 0, "p_scale": 1.1},
                {"time_index": 0, "element_type": "ac_load", "element_id": 0, "q_scale": 0.9},
            ]
        )

        with self.assertRaisesRegex(ValueError, "duplicates"):
            profile_snapshots_from_frame(frame)

    def test_rejects_unsupported_element_type(self) -> None:
        frame = pd.DataFrame(
            [{"time_index": 0, "element_type": "storage", "element_id": 0, "p_mw": 5.0}]
        )

        with self.assertRaisesRegex(ValueError, "unsupported element_type"):
            profile_snapshots_from_frame(frame)


class TimeProfileApplicationTests(unittest.TestCase):
    def test_applies_ac_load_scaling_and_keeps_omitted_loads(self) -> None:
        net = _build_stagg5_network()
        base_p = float(net.ac_load.at[0, "p_mw"])
        untouched_p = float(net.ac_load.at[1, "p_mw"])
        snapshot = profile_snapshots_from_frame(
            pd.DataFrame(
                [{"time_index": 0, "element_type": "ac_load", "element_id": 0, "scale": 1.25}]
            )
        )[0]

        apply_profile_snapshot(net, snapshot)

        self.assertAlmostEqual(float(net.ac_load.at[0, "p_mw"]), base_p * 1.25)
        self.assertAlmostEqual(float(net.ac_load.at[0, "q_mvar"]), 10.0 * 1.25)
        self.assertAlmostEqual(float(net.ac_load.at[1, "p_mw"]), untouched_p)

    def test_applies_bundled_one_day_profile_to_all_original_ac_loads(self) -> None:
        net = _build_stagg5_network()
        base_p = net.ac_load["p_mw"].astype(float).copy()
        base_q = net.ac_load["q_mvar"].astype(float).copy()
        snapshots = load_time_profile(ONE_DAY_PROFILE_PATH)

        apply_profile_snapshot(net, snapshots[0])

        for load_id in range(4):
            self.assertAlmostEqual(float(net.ac_load.at[load_id, "p_mw"]), base_p.at[load_id] * 0.62)
            self.assertAlmostEqual(
                float(net.ac_load.at[load_id, "q_mvar"]),
                base_q.at[load_id] * 0.62,
            )

    def test_applies_ac_load_absolute_pq_by_unique_name(self) -> None:
        net = _build_stagg5_network()
        snapshot = profile_snapshots_from_frame(
            pd.DataFrame(
                [
                    {
                        "time_index": 0,
                        "element_type": "ac_load",
                        "element_name": "Load 2",
                        "p_mw": 33.0,
                        "q_mvar": 7.0,
                    }
                ]
            )
        )[0]

        apply_profile_snapshot(net, snapshot)

        self.assertAlmostEqual(float(net.ac_load.at[0, "p_mw"]), 33.0)
        self.assertAlmostEqual(float(net.ac_load.at[0, "q_mvar"]), 7.0)

    def test_applies_dc_load_scaling(self) -> None:
        net = SimpleNamespace(
            dc_load=pd.DataFrame(
                [{"name": "DC Load", "bus": 0, "p_mw": 10.0, "in_service": True}]
            )
        )
        snapshot = profile_snapshots_from_frame(
            pd.DataFrame(
                [{"time_index": 0, "element_type": "dc_load", "element_id": 0, "p_scale": 1.5}]
            )
        )[0]

        apply_profile_snapshot(net, snapshot)

        self.assertAlmostEqual(float(net.dc_load.at[0, "p_mw"]), 15.0)

    def test_applies_dc_generator_absolute_power(self) -> None:
        net = _build_hybrid_network()
        snapshot = profile_snapshots_from_frame(
            pd.DataFrame(
                [{"time_index": 0, "element_type": "dc_gen", "element_id": 0, "p_mw": 14.0}]
            )
        )[0]

        apply_profile_snapshot(net, snapshot)

        self.assertAlmostEqual(float(net.dc_gen.at[0, "p_mw"]), 14.0)

    def test_applies_bundled_hybrid_one_day_profile_to_loads_and_dc_pv(self) -> None:
        net = _build_hybrid_network()
        base_p = net.ac_load["p_mw"].astype(float).copy()
        snapshots = load_time_profile(HYBRID_ONE_DAY_PROFILE_PATH)

        apply_profile_snapshot(net, snapshots[12])

        for load_id in range(4):
            self.assertAlmostEqual(float(net.ac_load.at[load_id, "p_mw"]), base_p.at[load_id] * 0.86)
        self.assertAlmostEqual(float(net.dc_gen.at[0, "p_mw"]), 30.0)

    def test_rejects_unknown_element_id(self) -> None:
        net = _build_stagg5_network()
        snapshot = ProfileSnapshot(
            time_index=0,
            timestamp="0",
            changes=profile_snapshots_from_frame(
                pd.DataFrame(
                    [{"time_index": 0, "element_type": "ac_load", "element_id": 999, "p_mw": 1.0}]
                )
            )[0].changes,
        )

        with self.assertRaisesRegex(ValueError, "Unknown ac_load element_id"):
            apply_profile_snapshot(net, snapshot)

    def test_rejects_dc_load_profile_when_case_has_no_dc_loads(self) -> None:
        net = _build_hybrid_network()
        snapshot = profile_snapshots_from_frame(
            pd.DataFrame(
                [{"time_index": 0, "element_type": "dc_load", "element_id": 0, "p_scale": 1.2}]
            )
        )[0]

        with self.assertRaisesRegex(ValueError, "no active `dc_load` table"):
            apply_profile_snapshot(net, snapshot)


if __name__ == "__main__":
    unittest.main()
