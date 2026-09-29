from __future__ import annotations

import unittest

import numpy as np

from acdcpf_opf.powerflow.acdcpf_adapter import ACDCPFAdapter
from acdcpf_opf.powerflow.result import PFResult


class ACDCPFAdapterTests(unittest.TestCase):
    def test_adapter_returns_standard_pf_result_and_preserves_input_case(self) -> None:
        try:
            import pyflow_acdc as pyf
        except ImportError as exc:  # pragma: no cover - depends on local environment
            self.skipTest(f"pyflow_acdc is not importable: {exc}")

        pyf.initialize_pyflowacdc()
        grid, _ = pyf.Stagg5MATACDC()
        original_ac_voltages = np.array([float(node.V) for node in grid.nodes_AC])

        result = ACDCPFAdapter().solve(grid, copy_case=True, write_back=False)

        self.assertIsInstance(result, PFResult)
        self.assertTrue(result.converged)
        self.assertGreater(result.voltage_magnitude.size, 0)
        self.assertGreater(result.total_active_losses, 0.0)

        explicit_total = sum(result.active_loss_breakdown().values())
        self.assertAlmostEqual(result.total_active_losses, explicit_total)
        np.testing.assert_allclose(
            np.array([float(node.V) for node in grid.nodes_AC]),
            original_ac_voltages,
        )


if __name__ == "__main__":
    unittest.main()
