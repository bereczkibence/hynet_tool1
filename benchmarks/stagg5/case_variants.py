from __future__ import annotations

import contextlib
from dataclasses import dataclass, field
import io
import math
from typing import Any, Callable

import pandas as pd
from acdcpf_pyflow_backend._bootstrap import ensure_acdcpf_importable


STAGG5_ORIGINAL = "original"
STAGG5_HYBRID_DCDC = "hybrid_dcdc"
PYFLOW_IEEE39_ACDC = "ieee39_acdc"
STAGG5_TWO_AREA_TRANSFORMER_DCDC = "two_area_stagg5_dcdc"
STAGG5_AC_LINE_RATINGS_MVA = (150.0, 100.0, 100.0, 100.0, 100.0, 100.0, 100.0)
STAGG5_DC_LINE_RATINGS_MW = (100.0, 100.0, 100.0)
PYFLOW_IEEE39_SLACK_VSC_RATING_MVA = 600.0
TWO_AREA_TRANSFORMER_RATING_MVA = 300.0
TWO_AREA_TRANSFORMER_R_PU = 0.002
TWO_AREA_TRANSFORMER_X_PU = 0.08
TWO_AREA_DCDC_RATING_MW = 100.0
TWO_AREA_DCDC_R_PU = 0.02
TWO_AREA_VSC_FACTORY_SETPOINTS_MW_MVAR = {
    0: {"p_ac_mw": 70.0, "q_ac_mvar": 42.0},
    2: {"p_ac_mw": -45.0, "q_ac_mvar": -2.0},
    3: {"p_ac_mw": 68.0, "q_ac_mvar": 42.0},
    5: {"p_ac_mw": -42.0, "q_ac_mvar": -2.0},
}


@dataclass(frozen=True)
class Stagg5GridCase:
    """Selectable benchmark topology for the PF/OPF comparison runner."""

    key: str
    case_name: str
    display_name: str
    description: str
    source: str
    supports_pyflow: bool
    supports_matacdc_reference: bool
    converter_active_power_indices: tuple[int, ...] | None = (0, 2)
    converter_reactive_power_indices: tuple[int, ...] | None = (0, 2)
    optimize_dcdc_voltage_ratio: bool = False
    dcdc_voltage_ratio_indices: tuple[int, ...] = ()
    network_factory: Callable[[], Any] | None = field(default=None, repr=False, compare=False)
    import_metadata: dict | None = field(default=None, repr=False, compare=False)


ORIGINAL_STAGG5_CASE = Stagg5GridCase(
    key=STAGG5_ORIGINAL,
    case_name="case5_stagg_mtdc_slack",
    display_name="Original Stagg5 MTDC",
    description="Original 5-bus Stagg AC system with the 3-terminal MTDC grid.",
    source="acdcpf",
    supports_pyflow=True,
    supports_matacdc_reference=True,
)

HYBRID_DCDC_STAGG5_CASE = Stagg5GridCase(
    key=STAGG5_HYBRID_DCDC,
    case_name="case5_stagg_mtdc_hybrid_dcdc",
    display_name="Hybrid Stagg5 with DC PV and battery",
    description=(
        "Original Stagg5 MTDC grid with two 20 kV DC resource buses: "
        "a fixed DC PV source and a controllable one-timestamp battery, each connected to "
        "the 345 kV DC grid through a bounded-ratio DCDC converter."
    ),
    source="acdcpf",
    supports_pyflow=False,
    supports_matacdc_reference=False,
    optimize_dcdc_voltage_ratio=True,
    dcdc_voltage_ratio_indices=(0, 1),
)

PYFLOW_IEEE39_ACDC_CASE = Stagg5GridCase(
    key=PYFLOW_IEEE39_ACDC,
    case_name="pyflow_case39_acdc_normalized",
    display_name="PyFlow IEEE 39 AC/DC",
    description=(
        "PyFlow IEEE 39-bus AC/DC benchmark with 39 AC buses, 10 DC buses, "
        "12 DC branches, and 10 VSCs. The raw PyFlow case has no DC slack; "
        "for PF/OPF benchmarking the first VSC/DC node is normalized to DC slack "
        f"with a {PYFLOW_IEEE39_SLACK_VSC_RATING_MVA:g} MVA balancing rating."
    ),
    source="pyflow",
    supports_pyflow=True,
    supports_matacdc_reference=False,
    converter_active_power_indices=None,
    converter_reactive_power_indices=None,
)

TWO_AREA_TRANSFORMER_DCDC_CASE = Stagg5GridCase(
    key=STAGG5_TWO_AREA_TRANSFORMER_DCDC,
    case_name="two_area_stagg5_dcdc",
    display_name="Two-area Stagg5 transformer-DCDC",
    description=(
        "Synthetic two-area benchmark made from two Stagg5 MTDC systems. "
        "Both areas connect to one 345 kV external slack bus through standalone "
        "transformers, and their DC grids are coupled by a bounded-ratio DCDC "
        "converter. Area B loads are scaled by 1.10 to create an asymmetric "
        "loss-minimization problem. The factory VSC setpoints are mildly "
        "perturbed so OPF movement is visible with generator dispatch fixed."
    ),
    source="acdcpf",
    supports_pyflow=False,
    supports_matacdc_reference=False,
    converter_active_power_indices=(0, 2, 3, 5),
    converter_reactive_power_indices=(0, 2, 3, 5),
    optimize_dcdc_voltage_ratio=True,
    dcdc_voltage_ratio_indices=(0,),
)


STAGG5_GRID_CASES = {
    ORIGINAL_STAGG5_CASE.key: ORIGINAL_STAGG5_CASE,
    HYBRID_DCDC_STAGG5_CASE.key: HYBRID_DCDC_STAGG5_CASE,
    PYFLOW_IEEE39_ACDC_CASE.key: PYFLOW_IEEE39_ACDC_CASE,
    TWO_AREA_TRANSFORMER_DCDC_CASE.key: TWO_AREA_TRANSFORMER_DCDC_CASE,
}


def _ensure_native_acdcpf_source() -> None:
    """Verify that the installed backend supports the native benchmark models."""
    ensure_acdcpf_importable()


def get_stagg5_grid_case(key: str | Stagg5GridCase | None) -> Stagg5GridCase:
    """Return a benchmark case definition from a user-facing key."""

    if isinstance(key, Stagg5GridCase):
        return key
    if key is None:
        return ORIGINAL_STAGG5_CASE
    normalized = str(key).strip().lower().replace("-", "_")
    aliases = {
        "stagg5": STAGG5_ORIGINAL,
        "base": STAGG5_ORIGINAL,
        "original_stagg5": STAGG5_ORIGINAL,
        "hybrid": STAGG5_HYBRID_DCDC,
        "dcdc": STAGG5_HYBRID_DCDC,
        "hybrid_dcdc": STAGG5_HYBRID_DCDC,
        "ieee39": PYFLOW_IEEE39_ACDC,
        "ieee_39": PYFLOW_IEEE39_ACDC,
        "case39": PYFLOW_IEEE39_ACDC,
        "case39_acdc": PYFLOW_IEEE39_ACDC,
        "pyflow_case39_acdc": PYFLOW_IEEE39_ACDC,
        "two_area": STAGG5_TWO_AREA_TRANSFORMER_DCDC,
        "two_area_stagg5": STAGG5_TWO_AREA_TRANSFORMER_DCDC,
        "two_area_stagg5_dcdc": STAGG5_TWO_AREA_TRANSFORMER_DCDC,
        "transformer_dcdc": STAGG5_TWO_AREA_TRANSFORMER_DCDC,
    }
    normalized = aliases.get(normalized, normalized)
    if normalized not in STAGG5_GRID_CASES:
        supported = ", ".join(sorted(STAGG5_GRID_CASES))
        raise ValueError(f"Unknown Stagg5 grid case {key!r}. Supported cases: {supported}.")
    return STAGG5_GRID_CASES[normalized]


def create_acdcpf_network_for_stagg5_case(case: str | Stagg5GridCase | None = None) -> Any:
    """Create the selected native ACDCPF Stagg5 benchmark network."""

    _ensure_native_acdcpf_source()
    selected = get_stagg5_grid_case(case)
    if selected.source != "acdcpf":
        raise NotImplementedError(
            f"{selected.display_name} is sourced from PyFlow; no native ACDCPF builder is registered."
        )
    if selected.network_factory is not None:
        return selected.network_factory()
    if selected.key == STAGG5_HYBRID_DCDC:
        return create_case5_stagg_mtdc_hybrid_dcdc()
    if selected.key == STAGG5_TWO_AREA_TRANSFORMER_DCDC:
        return create_two_area_stagg5_transformer_dcdc()
    if selected.key != STAGG5_ORIGINAL:
        raise ValueError(f"No network factory registered for {selected.key!r}.")

    from acdcpf.networks import create_case5_stagg_mtdc_slack

    net = create_case5_stagg_mtdc_slack()
    _apply_stagg5_reference_line_ratings(net)
    return net


def custom_grid_case(imported, *, key: str = "custom") -> Stagg5GridCase:
    """Adapt an import to the existing service without global case registration."""
    import copy

    snapshot = copy.deepcopy(imported)
    active = snapshot.network.vsc
    p_indices = tuple(int(i) for i, row in active.iterrows() if row.in_service and str(row.control_mode).startswith("p_"))
    q_indices = tuple(int(i) for i, row in active.iterrows() if row.in_service and str(row.control_mode).endswith("_q"))
    meta = snapshot.report
    sources = ", ".join(str(name) for name in meta["source_files"].values() if name)
    return Stagg5GridCase(
        key=key, case_name=key, display_name=f"Custom: {meta['source_files']['ac']}",
        description=f"Imported {sources}; format={meta['format']}, losses={meta['loss_units'] or 'n/a'}.",
        source="acdcpf", supports_pyflow=False, supports_matacdc_reference=False,
        converter_active_power_indices=p_indices, converter_reactive_power_indices=q_indices,
        network_factory=snapshot.fresh_network, import_metadata=copy.deepcopy(meta),
    )


def create_pyflow_grid_for_stagg5_case(case: str | Stagg5GridCase | None = None) -> Any:
    """Create the selected PyFlow Stagg5 grid when that topology exists."""

    selected = get_stagg5_grid_case(case)
    if not selected.supports_pyflow:
        raise NotImplementedError(
            f"{selected.display_name} is native-ACDCPF only; no equivalent PyFlow case exists."
        )

    import pyflow_acdc as pyf

    pyf.initialize_pyflowacdc()
    if selected.key == PYFLOW_IEEE39_ACDC:
        with contextlib.redirect_stdout(io.StringIO()):
            grid, _ = pyf.case39_acdc()
        _normalize_pyflow_case39_acdc_for_pf(grid)
        return grid

    grid, _ = pyf.Stagg5MATACDC()
    return grid


def _normalize_pyflow_case39_acdc_for_pf(grid: Any) -> None:
    """Make PyFlow's IEEE 39 AC/DC example usable as a PF benchmark.

    The upstream example defines all DC nodes and converters as active-power
    controlled, so the DC subsystem has no voltage reference and sequential PF
    fails with a singular matrix. Promoting the first VSC/DC node to DC slack is
    the smallest deterministic normalization needed for PF and OPF tests.

    The balancing converter also needs a larger apparent-power rating. With the
    original 100 MVA rating, the normalized slack converter can exceed its own
    rating during the PF balancing step, which makes tight control-margin OPF
    runs infeasible before IPOPT starts.
    """

    converters = list(getattr(grid, "Converters_ACDC", []))
    dc_nodes = list(getattr(grid, "nodes_DC", []))
    if not converters or not dc_nodes:
        return
    slack_converter = converters[0]
    slack_converter.type = "Slack"
    slack_converter.Node_DC.type = "Slack"
    slack_converter.MVA_max = PYFLOW_IEEE39_SLACK_VSC_RATING_MVA


def create_case5_stagg_mtdc_hybrid_dcdc() -> Any:
    """Create Stagg5 with DCDC-connected DC PV and battery benchmark resources.

    The original 345 kV MTDC grid is preserved. Two medium-voltage 20 kV DC
    resource buses are added:

    - `DC PV Bus`, with a fixed 20 MW DC generator.
    - `DC Battery Bus`, with a 25 MVA / 50 MWh battery initialized at
      15 MW charging and 50% SOC.

    Each resource bus is connected to the 345 kV DC grid through one DCDC
    converter. The ACDCPF DCDC convention is `from_bus = high-voltage side`,
    `to_bus = low-voltage side`, and `d_ratio = V_low / V_high`. Ratio bounds
    are included so Tool1 (acdcopf) can optimize the DCDC voltage ratios explicitly.
    The battery is represented as one native signed storage row where positive
    P discharges into the DC bus and negative P charges from the DC bus.
    """

    _ensure_native_acdcpf_source()
    from acdcpf.create.converters import create_dcdc
    from acdcpf.create.dc import create_dc_bus, create_dc_gen
    from acdcpf.create.storage import create_storage
    from acdcpf.networks import create_case5_stagg_mtdc_slack

    net = create_case5_stagg_mtdc_slack()
    net.name = "Case5 Stagg Hybrid DCDC"
    _apply_stagg5_reference_line_ratings(net)

    hv_base_kv = 345.0
    mv_base_kv = 20.0
    dcdc_ratio = mv_base_kv / hv_base_kv
    dcdc_ratio_margin = 0.05

    pv_bus = create_dc_bus(
        net,
        v_base=mv_base_kv,
        dc_grid=1,
        bus_type="p",
        v_dc_pu=1.0,
        v_min=0.90,
        v_max=1.10,
        name="DC PV Bus",
    )
    battery_bus = create_dc_bus(
        net,
        v_base=mv_base_kv,
        dc_grid=1,
        bus_type="p",
        v_dc_pu=1.0,
        v_min=0.90,
        v_max=1.10,
        name="DC Battery Bus",
    )

    create_dc_gen(net, bus=pv_bus, p_mw=20.0, name="PV DC Source")
    create_storage(
        net,
        bus=battery_bus,
        bus_type="dc",
        p_mw=-15.0,
        q_mvar=0.0,
        sn_mva=25.0,
        energy_mwh=50.0,
        soc_percent=50.0,
        soc_min_percent=0.0,
        soc_max_percent=100.0,
        eta_charge=0.95,
        eta_discharge=0.95,
        p_min_mw=-25.0,
        p_max_mw=25.0,
        q_min_mvar=0.0,
        q_max_mvar=0.0,
        name="Battery DC Storage",
    )

    create_dcdc(
        net,
        from_bus=0,
        to_bus=pv_bus,
        d_ratio=dcdc_ratio,
        r_ohm=0.5,
        g_us=0.0,
        name="DCDC PV",
    )
    create_dcdc(
        net,
        from_bus=2,
        to_bus=battery_bus,
        d_ratio=dcdc_ratio,
        r_ohm=0.5,
        g_us=0.0,
        name="DCDC Battery",
    )

    net.dcdc["d_ratio_min"] = net.dcdc["d_ratio"].astype(float) * (1.0 - dcdc_ratio_margin)
    net.dcdc["d_ratio_max"] = net.dcdc["d_ratio"].astype(float) * (1.0 + dcdc_ratio_margin)
    return net


def create_two_area_stagg5_transformer_dcdc() -> Any:
    """Create the synthetic two-area Stagg5 transformer-DCDC benchmark."""

    _ensure_native_acdcpf_source()
    from acdcpf.create.ac import create_ac_bus, create_ac_gen, create_transformer
    from acdcpf.create.converters import create_dcdc
    from acdcpf.network import create_empty_network
    from acdcpf.networks import create_case5_stagg_mtdc_slack

    base = create_case5_stagg_mtdc_slack()
    _apply_stagg5_reference_line_ratings(base)

    net = create_empty_network(
        name="Two-Area Stagg5 Transformer DCDC",
        s_base=float(base.s_base),
        f_hz=float(base.f_hz),
        pol=int(getattr(base, "pol", 2)),
    )

    external_bus = create_ac_bus(
        net,
        vr_kv=345.0,
        name="External Grid 345 kV",
        v_min_pu=0.90,
        v_max_pu=1.10,
        is_slack=True,
    )
    create_ac_gen(
        net,
        bus=external_bus,
        p_mw=0.0,
        q_mvar=0.0,
        v_pu=1.06,
        q_min_mvar=-1000.0,
        q_max_mvar=1000.0,
        name="External Grid Slack",
    )

    area_a = _copy_stagg5_area(base, net, prefix="A", dc_grid=1, load_scale=1.0)
    area_b = _copy_stagg5_area(base, net, prefix="B", dc_grid=2, load_scale=1.10)

    create_transformer(
        net,
        from_bus=external_bus,
        to_bus=area_a["ac_buses"][0],
        vn_from_kv=345.0,
        vn_to_kv=345.0,
        sn_mva=TWO_AREA_TRANSFORMER_RATING_MVA,
        r_pu=TWO_AREA_TRANSFORMER_R_PU,
        x_pu=TWO_AREA_TRANSFORMER_X_PU,
        tap=1.0,
        shift_deg=0.0,
        tap_min=0.95,
        tap_max=1.05,
        tap_step_percent=1.25,
        tap_controllable=False,
        name="Transformer External-A",
    )
    create_transformer(
        net,
        from_bus=external_bus,
        to_bus=area_b["ac_buses"][0],
        vn_from_kv=345.0,
        vn_to_kv=345.0,
        sn_mva=TWO_AREA_TRANSFORMER_RATING_MVA,
        r_pu=TWO_AREA_TRANSFORMER_R_PU,
        x_pu=TWO_AREA_TRANSFORMER_X_PU,
        tap=1.0,
        shift_deg=0.0,
        tap_min=0.95,
        tap_max=1.05,
        tap_step_percent=1.25,
        tap_controllable=False,
        name="Transformer External-B",
    )

    dc_base_kv = 345.0
    z_base_dc = dc_base_kv**2 / float(net.s_base)
    dcdc_idx = create_dcdc(
        net,
        from_bus=area_a["dc_buses"][0],
        to_bus=area_b["dc_buses"][0],
        d_ratio=1.0,
        r_ohm=TWO_AREA_DCDC_R_PU * z_base_dc,
        g_us=0.0,
        name="DCDC Area A-B",
    )
    net.dcdc.at[dcdc_idx, "d_ratio_min"] = 0.95
    net.dcdc.at[dcdc_idx, "d_ratio_max"] = 1.05
    net.dcdc.at[dcdc_idx, "rate_mw"] = TWO_AREA_DCDC_RATING_MW
    _apply_vsc_factory_setpoints(net, TWO_AREA_VSC_FACTORY_SETPOINTS_MW_MVAR)
    return net


def _apply_vsc_factory_setpoints(net: Any, setpoints: dict[int, dict[str, float]]) -> None:
    for idx, values in setpoints.items():
        if idx not in net.vsc.index:
            raise ValueError(f"Two-area VSC factory setpoint references missing VSC {idx}.")
        net.vsc.at[idx, "p_mw"] = float(values["p_ac_mw"])
        net.vsc.at[idx, "q_mvar"] = float(values["q_ac_mvar"])


def _copy_stagg5_area(
    source: Any,
    target: Any,
    *,
    prefix: str,
    dc_grid: int,
    load_scale: float,
) -> dict[str, dict[int, int]]:
    """Copy one Stagg5 area into a target network with remapped indices."""

    _ensure_native_acdcpf_source()
    from acdcpf.create.ac import create_ac_bus, create_ac_line, create_ac_gen, create_ac_load
    from acdcpf.create.converters import create_vsc
    from acdcpf.create.dc import create_dc_bus, create_dc_line, create_dc_load, create_dc_gen

    ac_buses: dict[int, int] = {}
    dc_buses: dict[int, int] = {}

    for idx, row in source.ac_bus.iterrows():
        idx = int(idx)
        ac_buses[idx] = create_ac_bus(
            target,
            vr_kv=float(row["vr_kv"]),
            name=f"{prefix} {row.get('name', f'Bus {idx + 1}')}",
            v_min_pu=float(row.get("v_min_pu", 0.9)),
            v_max_pu=float(row.get("v_max_pu", 1.1)),
            gs_pu=float(row.get("gs_pu", 0.0)),
            bs_pu=float(row.get("bs_pu", 0.0)),
            is_slack=False,
            in_service=bool(row.get("in_service", True)),
        )

    for idx, row in source.dc_bus.iterrows():
        idx = int(idx)
        dc_buses[idx] = create_dc_bus(
            target,
            v_base=float(row["v_base"]),
            name=f"{prefix} {row.get('name', f'DC Bus {idx + 1}')}",
            dc_grid=dc_grid,
            bus_type=str(row.get("bus_type", "p")),
            v_dc_pu=float(row.get("v_dc_pu", 1.0)),
            v_min=float(row.get("v_min", 0.95)),
            v_max=float(row.get("v_max", 1.05)),
            in_service=bool(row.get("in_service", True)),
        )

    for _, row in source.ac_load.iterrows():
        create_ac_load(
            target,
            bus=ac_buses[int(row["bus"])],
            p_mw=float(row.get("p_mw", 0.0)) * load_scale,
            q_mvar=float(row.get("q_mvar", 0.0)) * load_scale,
            name=f"{prefix} {row.get('name', 'Load')}",
            in_service=bool(row.get("in_service", True)),
        )

    for _, row in source.ac_gen.iterrows():
        name = str(row.get("name", "Generator")).replace(" (Slack)", "")
        create_ac_gen(
            target,
            bus=ac_buses[int(row["bus"])],
            p_mw=float(row.get("p_mw", 0.0)),
            q_mvar=float(row.get("q_mvar", 0.0)),
            v_pu=_optional_float(row.get("v_pu")),
            q_min_mvar=float(row.get("q_min_mvar", -1000.0)),
            q_max_mvar=float(row.get("q_max_mvar", 1000.0)),
            name=f"{prefix} {name}",
            in_service=bool(row.get("in_service", True)),
        )

    for _, row in source.ac_line.iterrows():
        new_idx = create_ac_line(
            target,
            from_bus=ac_buses[int(row["from_bus"])],
            to_bus=ac_buses[int(row["to_bus"])],
            length_km=float(row.get("length_km", 1.0)),
            r_ohm_per_km=float(row.get("r_ohm_per_km", 0.0)),
            x_ohm_per_km=float(row.get("x_ohm_per_km", 0.0)),
            b_us_per_km=float(row.get("b_us_per_km", 0.0)),
            g_us_per_km=float(row.get("g_us_per_km", 0.0)),
            tap=float(row.get("tap", 1.0)),
            shift_deg=float(row.get("shift_deg", 0.0)),
            max_i_ka=_optional_float(row.get("max_i_ka")),
            name=f"{prefix} {row.get('name', 'AC line')}",
            in_service=bool(row.get("in_service", True)),
        )
        _copy_optional_column_value(source.ac_line, target.ac_line, row.name, new_idx, "rate_mva")

    for _, row in source.dc_line.iterrows():
        new_idx = create_dc_line(
            target,
            from_bus=dc_buses[int(row["from_bus"])],
            to_bus=dc_buses[int(row["to_bus"])],
            length_km=float(row.get("length_km", 1.0)),
            r_ohm_per_km=float(row.get("r_ohm_per_km", 0.0)),
            max_i_ka=_optional_float(row.get("max_i_ka")),
            name=f"{prefix} {row.get('name', 'DC line')}",
            in_service=bool(row.get("in_service", True)),
        )
        _copy_optional_column_value(source.dc_line, target.dc_line, row.name, new_idx, "rate_mw")

    for _, row in getattr(source, "dc_load", pd.DataFrame()).iterrows():
        create_dc_load(
            target,
            bus=dc_buses[int(row["bus"])],
            p_mw=float(row.get("p_mw", 0.0)),
            load_type=str(row.get("load_type", "constant_power")),
            name=f"{prefix} {row.get('name', 'DC load')}",
            in_service=bool(row.get("in_service", True)),
        )

    for _, row in getattr(source, "dc_gen", pd.DataFrame()).iterrows():
        create_dc_gen(
            target,
            bus=dc_buses[int(row["bus"])],
            p_mw=float(row.get("p_mw", 0.0)),
            name=f"{prefix} {row.get('name', 'DC generator')}",
            in_service=bool(row.get("in_service", True)),
        )

    for _, row in source.vsc.iterrows():
        create_vsc(
            target,
            ac_bus=ac_buses[int(row["ac_bus"])],
            dc_bus=dc_buses[int(row["dc_bus"])],
            s_mva=float(row.get("s_mva", 0.0)),
            control_mode=str(row.get("control_mode", "p_q")),
            p_mw=float(row.get("p_mw", 0.0)),
            q_mvar=float(row.get("q_mvar", 0.0)),
            v_ac_pu=_optional_float(row.get("v_ac_pu")),
            v_dc_pu=_optional_float(row.get("v_dc_pu")),
            droop_kv_per_mw=_optional_float(row.get("droop_kv_per_mw")),
            p_dc_set_mw=_optional_float(row.get("p_dc_set_mw")),
            v_dc_set_pu=_optional_float(row.get("v_dc_set_pu")),
            loss_a=float(row.get("loss_a", 0.0)),
            loss_b=float(row.get("loss_b", 0.0)),
            loss_c=float(row.get("loss_c", 0.0)),
            loss_c_inv=_optional_float(row.get("loss_c_inv")),
            r_tf_pu=float(row.get("r_tf_pu", 0.0)),
            x_tf_pu=float(row.get("x_tf_pu", 0.0)),
            r_c_pu=float(row.get("r_c_pu", 0.0)),
            x_c_pu=float(row.get("x_c_pu", 0.0)),
            b_filter_pu=float(row.get("b_filter_pu", 0.0)),
            loss_base_kv=_optional_float(row.get("loss_base_kv")),
            name=f"{prefix} {row.get('name', 'VSC')}",
            in_service=bool(row.get("in_service", True)),
        )

    return {"ac_buses": ac_buses, "dc_buses": dc_buses}


def _copy_optional_column_value(
    source_table: pd.DataFrame,
    target_table: pd.DataFrame,
    source_idx: Any,
    target_idx: int,
    column: str,
) -> None:
    if column in source_table.columns and _is_finite(source_table.at[source_idx, column]):
        target_table.at[target_idx, column] = float(source_table.at[source_idx, column])


def _optional_float(value: Any) -> float | None:
    return float(value) if _is_finite(value) else None


def _is_finite(value: Any) -> bool:
    if value is None:
        return False
    try:
        return bool(math.isfinite(float(value)))
    except (TypeError, ValueError):
        return False


def _apply_stagg5_reference_line_ratings(net: Any) -> None:
    """Attach the published/PyFlow Stagg5 line ratings to the native ACDCPF case.

    The upstream ACDCPF Stagg5 builder keeps only line impedances and leaves
    `max_i_ka` empty. PyFlow's built-in Stagg5 case includes AC line
    `MVA_rating` and DC line `MW_rating`, so the benchmark adds equivalent
    current limits here to keep PF/OPF loading reports and OPF constraints
    aligned with the reference case.
    """

    for idx, rating_mva in enumerate(STAGG5_AC_LINE_RATINGS_MVA):
        if idx not in net.ac_line.index:
            continue
        from_bus = int(net.ac_line.at[idx, "from_bus"])
        voltage_kv = float(net.ac_bus.at[from_bus, "vr_kv"])
        net.ac_line.at[idx, "rate_mva"] = rating_mva
        net.ac_line.at[idx, "max_i_ka"] = rating_mva / (math.sqrt(3.0) * voltage_kv)

    poles = float(getattr(net, "pol", 1.0))
    for idx, rating_mw in enumerate(STAGG5_DC_LINE_RATINGS_MW):
        if idx not in net.dc_line.index:
            continue
        from_bus = int(net.dc_line.at[idx, "from_bus"])
        voltage_kv = float(net.dc_bus.at[from_bus, "v_base"])
        net.dc_line.at[idx, "rate_mw"] = rating_mw
        net.dc_line.at[idx, "max_i_ka"] = rating_mw / (max(poles, 1e-12) * voltage_kv)
