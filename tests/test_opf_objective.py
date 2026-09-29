from __future__ import annotations

import unittest

import numpy as np

from acdcpf_opf.opf.config import OPFConfig
from acdcpf_opf.opf.objective import compute_total_active_losses, total_active_loss_objective
from acdcpf_opf.powerflow.result import PFResult


class OPFObjectiveTests(unittest.TestCase):
    def test_loss_components_sum_into_total_active_losses(self) -> None:
        result = PFResult(
            converged=True,
            ac_branch_active_losses=np.array([1.0, 2.0]),
            dc_branch_active_losses=np.array([0.5]),
            converter_active_losses=np.array([0.25, 0.75]),
            additional_active_losses={"dcdc_converter_mw": np.array([0.1])},
        )

        self.assertAlmostEqual(compute_total_active_losses(result), 4.6)

    def test_nonconverged_pf_returns_penalty(self) -> None:
        config = OPFConfig(penalty_value=12345.0)
        result = PFResult(converged=False, message="failed")

        evaluation = total_active_loss_objective(result, config)

        self.assertEqual(evaluation.value, 12345.0)
        self.assertIn("PF did not converge", evaluation.message)


if __name__ == "__main__":
    unittest.main()
