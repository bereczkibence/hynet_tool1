from __future__ import annotations

from types import SimpleNamespace
import unittest

import numpy as np

from acdcpf_opf.opf.config import OPFConfig
from acdcpf_opf.opf.constraints import compute_constraint_violations, violated_constraints
from acdcpf_opf.powerflow.result import PFResult


class OPFConstraintTests(unittest.TestCase):
    def test_voltage_branch_converter_and_generator_violations_are_detected(self) -> None:
        generator = SimpleNamespace(
            name="G1",
            genNumber=0,
            PGen=2.0,
            QGen=0.0,
            Min_pow_gen=0.0,
            Max_pow_gen=1.0,
            Min_pow_genR=-1.0,
            Max_pow_genR=1.0,
        )
        case = SimpleNamespace(
            nodes_AC=[
                SimpleNamespace(name="AC1", Umin=0.95, Umax=1.05, connected_gen=[generator])
            ],
            nodes_DC=[SimpleNamespace(name="DC1", Umin=0.95, Umax=1.05, connected_gen=[])],
            lines_AC=[SimpleNamespace(name="L1")],
            lines_DC=[SimpleNamespace(name="D1", MW_rating=100.0)],
            Converters_ACDC=[SimpleNamespace(name="C1", MVA_max=50.0, Ucmin=0.9, Ucmax=1.1)],
        )
        pf_result = PFResult(
            converged=True,
            voltage_magnitude=np.array([1.10]),
            dc_voltage_magnitude=np.array([0.90]),
            dc_active_power_flow=np.array([[120.0, -119.0]]),
            ac_branch_loading_percent=np.array([125.0]),
            converter_active_power_ac=np.array([40.0]),
            converter_reactive_power_ac=np.array([40.0]),
            converter_active_power_dc=np.array([60.0]),
            converter_internal_voltage=np.array([1.20]),
            converter_ac_current=np.array([0.20]),
        )

        violations = violated_constraints(
            compute_constraint_violations(case, pf_result, OPFConfig())
        )
        names = {violation.name for violation in violations}

        self.assertIn("ac_voltage[AC1]", names)
        self.assertIn("dc_voltage[DC1]", names)
        self.assertIn("ac_branch_loading[L1]", names)
        self.assertIn("dc_branch_loading[D1]", names)
        self.assertIn("converter_ac_mva[C1]", names)
        self.assertIn("converter_dc_mw[C1]", names)
        self.assertIn("converter_internal_voltage[C1]", names)
        self.assertIn("ac_generator_p[G1]", names)

    def test_missing_mismatch_output_is_marked_unavailable_not_faked(self) -> None:
        case = SimpleNamespace(
            nodes_AC=[],
            nodes_DC=[],
            lines_AC=[],
            lines_DC=[],
            Converters_ACDC=[],
        )
        pf_result = PFResult(converged=True, max_mismatch=None)

        violations = compute_constraint_violations(case, pf_result, OPFConfig())
        mismatch = [item for item in violations if item.name == "power_balance_mismatch"]

        self.assertEqual(len(mismatch), 1)
        self.assertFalse(mismatch[0].available)
        self.assertIn("TODO", mismatch[0].message)


if __name__ == "__main__":
    unittest.main()
