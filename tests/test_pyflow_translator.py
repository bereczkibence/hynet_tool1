from types import SimpleNamespace

import pytest

from acdcpf_pyflow_backend.translator import (
    _build_vsc_kwargs,
    _map_converter_control_mode,
)


def _fake_converter(dc_type: str, ac_type: str = "PQ") -> SimpleNamespace:
    return SimpleNamespace(
        type=dc_type,
        AC_type=ac_type,
        Node_AC=SimpleNamespace(nodeNumber=10, V=1.01),
        Node_DC=SimpleNamespace(nodeNumber=20, V=0.99, V_ini=1.0, kV_base=345.0),
        P_AC=-0.6,
        Q_AC=-0.4,
        P_DC=-0.586274,
        MVA_max=100.0,
        a_conv_og=1.1033,
        b_conv_og=0.887,
        c_rect_og=2.885,
        c_inver_og=2.885,
        R_t=0.01,
        X_t=0.01,
        PR_R=0.01,
        PR_X=0.01,
        Bf=0.01,
        AC_kV_base=345.0,
        name="Conv_1",
    )


def test_pyflow_p_converter_maps_to_fixed_dc_power_control():
    conv = _fake_converter("P")

    control = _map_converter_control_mode(conv)
    kwargs = _build_vsc_kwargs(
        SimpleNamespace(S_base=100.0),
        conv,
        control,
        ac_bus_map={10: 0},
        dc_bus_map={20: 0},
    )

    assert control == "pdc_q"
    assert kwargs["control_mode"] == "pdc_q"
    assert kwargs["p_dc_set_mw"] == pytest.approx(-58.6274)
    assert kwargs["p_mw"] == pytest.approx(-58.6274)
    assert kwargs["q_mvar"] == pytest.approx(40.0)


def test_pyflow_pac_converter_keeps_ac_side_power_control():
    conv = _fake_converter("PAC")

    control = _map_converter_control_mode(conv)
    kwargs = _build_vsc_kwargs(
        SimpleNamespace(S_base=100.0),
        conv,
        control,
        ac_bus_map={10: 0},
        dc_bus_map={20: 0},
    )

    assert control == "p_q"
    assert kwargs["control_mode"] == "p_q"
    assert kwargs["p_mw"] == pytest.approx(60.0)
    assert "p_dc_set_mw" not in kwargs
