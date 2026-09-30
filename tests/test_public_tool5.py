from pathlib import Path
import copy
import os
import subprocess
import sys
import numpy as np
import pandas as pd
import pytest
from acdcpf_pyflow_backend._bootstrap import backend_info
from acdcpf_pyflow_backend.network_factory import NetworkFactory
from acdcpf_opf.data.custom_network import load_custom_network
from acdcpf_opf.powerflow.acdcpf_network_adapter import ACDCPFNetworkAdapter

ROOT = Path(__file__).resolve().parents[1]


def test_public_source_is_not_installed_or_patched():
    import acdcpf
    assert backend_info()['source'] == str(Path(acdcpf.__file__).resolve())
    if backend_info()['api_version'] == 'public-run_pf':
        assert not hasattr(acdcpf, 'PFOptions')
        assert not hasattr(acdcpf, 'solve')


def test_bad_explicit_source_does_not_fall_back(tmp_path):
    env = {**os.environ, 'TOOL1_TOOL5_PATH': str(tmp_path)}
    p = subprocess.run([sys.executable, '-c', 'from acdcpf_pyflow_backend._bootstrap import ensure_acdcpf_importable; ensure_acdcpf_importable()'], env=env, capture_output=True, text=True)
    assert p.returncode != 0
    assert 'Tool5 source not found' in p.stderr


def test_custom_ac_pf_preserves_inputs():
    imported = load_custom_network(ROOT/'examples/two_bus_ac.py')
    before = copy.deepcopy(imported.network)
    result = ACDCPFNetworkAdapter().solve(imported.network)
    assert result.converged, result.message
    for name in ('ac_bus','ac_gen','ac_line'):
        pd.testing.assert_frame_equal(getattr(imported.network,name), getattr(before,name))
    assert result.total_active_losses > 0


def test_external_ids_storage_shunt_and_slack():
    pf = NetworkFactory()
    net = pf.create_empty_network(s_base=100.)
    a = pf.create_ac_bus(net, 110., gs_pu=.01, bs_pu=.02)
    b = pf.create_ac_bus(net, 110., is_slack=True)
    pf.create_ac_gen(net, a, p_mw=3., v_pu=1., q_min_mvar=-100., q_max_mvar=100.)
    pf.create_ac_gen(net, b, v_pu=1., q_min_mvar=-100., q_max_mvar=100.)
    pf.create_ac_line(net,a,b,1.,1.,5.)
    pf.create_ac_load(net,a,10.,2.)
    pf.create_storage(net,a,bus_type='ac',p_mw=2.,q_mvar=1.,sn_mva=5.,energy_mwh=10.)
    net.ac_bus.index=[10,40]
    for name in ('ac_gen','ac_load','storage'):
        table=getattr(net,name); table['bus']=table.bus.map({0:10,1:40})
    net.ac_line['from_bus']=10; net.ac_line['to_bus']=40
    net.ac_gen.index=[8,19]
    result=ACDCPFNetworkAdapter().solve(net)
    assert result.converged, result.message
    solved=result.raw_result
    assert list(solved.res_ac_bus.index)==[10,40]
    assert list(solved.res_ac_gen.index)==[8,19]
    assert solved.res_ac_gen.at[8,'p_mw']==pytest.approx(3.)
    # 10 MW demand - 2 MW storage - 3 MW fixed generation + shunt + line loss.
    assert 5.5 < solved.res_ac_gen.at[19,'p_mw'] < 7.
    assert solved.res_storage.iloc[0].p_mw==2.
    assert net.ac_bus.at[10,'gs_pu']==.01


@pytest.mark.parametrize("shift", [0., 5.])
def test_transformer_preserves_tap_and_results(shift):
    pf=NetworkFactory(); net=pf.create_empty_network()
    a=pf.create_ac_bus(net,110.,is_slack=True); b=pf.create_ac_bus(net,20.)
    pf.create_ac_gen(net,a,v_pu=1.,q_min_mvar=-100.,q_max_mvar=100.)
    pf.create_ac_load(net,b,10.,2.)
    pf.create_transformer(net,a,b,50.,.01,.08,tap=1.03,shift_deg=shift)
    result=ACDCPFNetworkAdapter().solve(net)
    assert result.converged, result.message
    assert len(result.raw_result.res_trafo)==1
    assert len(result.raw_result.res_ac_line)==0
    assert result.raw_result.res_trafo.iloc[0].p_loss_mw>0
    assert net.trafo.iloc[0].tap==1.03
    assert result.raw_result.res_trafo.iloc[0].p_to_mw==pytest.approx(-10., abs=1e-6)
    assert result.raw_result.res_trafo.iloc[0].q_to_mvar==pytest.approx(-2., abs=1e-6)


def test_public_fixed_pdc_rejected():
    if backend_info()['api_version']!='public-run_pf':
        pytest.skip('Public backend-specific limitation')
    from acdcpf.networks import create_case5_stagg_mtdc_slack
    net=create_case5_stagg_mtdc_slack(); net.vsc.at[0,'control_mode']='pdc_q'
    result=ACDCPFNetworkAdapter().solve(net)
    assert not result.converged
    assert 'fixed-Pdc' in result.message

@pytest.mark.parametrize('factory_name', [
    'create_2terminal_hvdc', 'create_case5_stagg_hvdc_ptp',
    'create_case5_stagg_mtdc_slack', 'create_case5_stagg_mtdc_droop',
    'create_case33_ieee', 'create_case33_ieee_ext', 'create_case24_ieee_rts_mtdc',
])
def test_adapter_matches_unmodified_public_pf(factory_name):
    import acdcpf
    import acdcpf.networks as networks
    if backend_info()['api_version'] != 'public-run_pf':
        pytest.skip('Public implementation comparison')
    original = getattr(networks, factory_name)()
    reference = copy.deepcopy(original)
    # Public run_pf consumes shunts as MW/MVAr; Tool1 tables declare per-unit.
    for column in ("gs_pu", "bs_pu"):
        reference.ac_bus[column] = reference.ac_bus[column].astype(float)*reference.s_base
    converged = acdcpf.run_pf(reference, enforce_limits=False)
    adapted = ACDCPFNetworkAdapter().solve(original)
    assert adapted.converged == converged
    assert converged
    np.testing.assert_allclose(adapted.raw_result.res_ac_bus.v_pu, reference.res_ac_bus.v_pu, atol=1e-8)
    np.testing.assert_allclose(adapted.raw_result.res_dc_bus.v_dc_pu, reference.res_dc_bus.v_dc_pu, atol=1e-8)
    np.testing.assert_allclose(adapted.raw_result.res_vsc.p_ac_mw, reference.res_vsc.p_ac_mw, atol=1e-7)


def test_opf_uses_selected_tool5_filter_convention():
    import pyomo.environ as pyo
    from acdcpf.networks import create_case5_stagg_mtdc_slack
    from acdcpf_opf.data.acdcpf_to_pyomo import convert_acdcpf_network_to_opf_data
    from acdcpf_opf.opf.formulations.acdc_opf_pyomo_loss_min import build_acdc_opf_model
    from acdcpf_pyflow_backend._bootstrap import filter_balance_sign
    net=create_case5_stagg_mtdc_slack()
    data=convert_acdcpf_network_to_opf_data(net)
    model=build_acdc_opf_model(data)
    sign=filter_balance_sign()
    assert data['converters']['CONV0']['filter']['b']==net.vsc.at[0,'b_filter_pu']
    model.Q_filter['CONV0'].set_value(-.03)
    model.Qtf_fi['CONV0'].set_value(-.2)
    model.Qpr_fc['CONV0'].set_value(.2+sign*.03)
    assert pyo.value(model.filter_reactive_balance['CONV0'].body)==pytest.approx(0.)
