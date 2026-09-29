from __future__ import annotations

import unittest

import numpy as np

from acdcpf_opf.powerflow.acdcpf_network_adapter import ACDCPFNetworkAdapter
from acdcpf_opf.powerflow.result import PFResult


class ACDCPFNetworkAdapterTests(unittest.TestCase):
    def test_native_network_adapter_returns_standard_pf_result(self) -> None:
        from acdcpf.networks import create_case5_stagg_mtdc_slack

        net = create_case5_stagg_mtdc_slack()
        original_vsc_p = net.vsc["p_mw"].astype(float).to_numpy(copy=True)

        result = ACDCPFNetworkAdapter().solve(net, copy_case=True, write_back=False)

        self.assertIsInstance(result, PFResult)
        self.assertTrue(result.converged)
        self.assertGreater(result.voltage_magnitude.size, 0)
        self.assertGreater(result.dc_voltage_magnitude.size, 0)
        self.assertGreater(result.converter_active_losses.size, 0)
        self.assertGreater(result.total_active_losses, 0.0)

        explicit_total = sum(result.active_loss_breakdown().values())
        self.assertAlmostEqual(result.total_active_losses, explicit_total)
        np.testing.assert_allclose(
            net.vsc["p_mw"].astype(float).to_numpy(),
            original_vsc_p,
        )


if __name__ == "__main__":
    unittest.main()
