from pathlib import Path
import numpy as np
import pytest
from acdcpf_opf.data.custom_network import load_custom_network, ImportOptions
from acdcpf_opf.powerflow.acdcpf_network_adapter import ACDCPFNetworkAdapter
from acdcpf_opf.benchmarks.stagg5.service import BenchmarkRequest, run_benchmark_request
from acdcpf_opf.dashboard.schemas import ImportRequest

ROOT = Path(__file__).resolve().parents[1]


def test_import_voltage_convention_and_physical_scaling():
    ac, dc = ROOT/'inputs/case33h_ieee_ac.py', ROOT/'inputs/case33h_ieee_dc.py'
    if not ac.exists():
        pytest.skip("Private supplied cases not distributed")
    def load(convention):
        return load_custom_network(ac, dc, options=ImportOptions(format="tool7", loss_units="per_unit", dc_voltage_convention=convention))
    a, b = load("per_pole"), load("pole_to_pole")
    np.testing.assert_array_equal(a.dc_case['busdc'], b.dc_case['busdc'])
    np.testing.assert_allclose(a.network.dc_bus.v_base, 2*b.network.dc_bus.v_base)
    np.testing.assert_allclose(a.network.dc_line.r_ohm_per_km, 4*b.network.dc_line.r_ohm_per_km)
    x, y = ACDCPFNetworkAdapter().solve(a.network), ACDCPFNetworkAdapter().solve(b.network)
    assert x.converged and y.converged
    np.testing.assert_allclose(x.dc_voltage_magnitude, y.dc_voltage_magnitude, atol=1e-8)
    assert x.total_active_losses == pytest.approx(y.total_active_losses, abs=1e-8)
    np.testing.assert_allclose(y.raw_result.res_dc_line.i_ka, 2*x.raw_result.res_dc_line.i_ka)
    assert x.diagnostics['tool5']['policy'] == 'unconstrained'


def test_limited_policy_does_not_silently_change_opf_controls():
    with pytest.raises(ValueError, match="PF only"):
        run_benchmark_request(BenchmarkRequest(pf_policy="converter_limited", skip_opf=False))


def test_new_import_schema_rejects_unknown_convention():
    with pytest.raises(ValueError):
        ImportRequest(ac_text='test', dc_voltage_convention='auto')


def test_limited_service_reports_policy_and_preserves_result_structure():
    from acdcpf_opf.dashboard.web import result_payload
    bundle = run_benchmark_request(BenchmarkRequest(skip_opf=True, pf_policy="converter_limited",
                                    include_pyflow_reference=False, write_exports=False))
    assert bundle.success
    result = result_payload(bundle)
    assert result['runs'][0]['tool5']['policy'] == 'converter_limited'
    assert 'residuals' in result['runs'][0]['tool5']
