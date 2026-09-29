"""Import fidelity, parser isolation, and the actual PF/OPF service workflow."""
import copy
import json
from pathlib import Path

import numpy as np
import pytest

from acdcpf_opf.data import case_indices as c
from acdcpf_opf.data.case_parser import read_case_text
from acdcpf_opf.data.custom_network import ImportOptions, convert_custom_network, load_custom_network
from acdcpf_opf.data.acdcpf_to_pyomo import convert_acdcpf_network_to_opf_data
from acdcpf_opf.benchmarks.stagg5.case_variants import custom_grid_case, create_acdcpf_network_for_stagg5_case
from acdcpf_opf.benchmarks.stagg5 import benchmark_pf_opf_comparison as benchmark
from acdcpf_opf.benchmarks.stagg5.service import BenchmarkRequest, run_benchmark_request, editable_network_tables
from acdcpf_opf.runtime_paths import ipopt_executable_path


@pytest.fixture
def ac():
    return {"version": "2", "baseMVA": 100.,
            "bus": np.array([[10,3,0,0,0,0,1,1,0,12.66,1,1.1,.9], [40,1,.5,.1,0,.003,1,1,0,12.66,1,1.1,.9]]),
            "gen": np.array([[10,.5,0,100,-100,1,100,1,100,0]]),
            "branch": np.array([[10,40,.01,.03,0,10,10,10,0,0,1]])}


@pytest.fixture
def dc():
    return {"baseMVAac": 100., "baseMVAdc": 50., "pol": 2,
            "busdc": np.array([[101,40,1,0,1,20,1.1,.9,0], [205,0,1,.1,1,20,1.1,.9,0]]),
            "branchdc": np.array([[101,205,.01,0,0,5,5,5,1,1]]),
            "convdc": np.array([[101,2,1,0,0,0,0,0,0,.005,.05,12.66,1.1,.9,.05,1,.001,.002,.003,.004]])}


def case_text(case):
    lines = ["import numpy as np", "def case():", "    data = {}"]
    for key, val in case.items():
        literal = f"np.array({val.tolist()!r}, dtype=np.float64)" if isinstance(val, np.ndarray) else repr(val)
        lines.append(f"    data[{key!r}] = {literal}")
    return "\n".join([*lines, "    return data", "alias = case"])


def test_literal_parser_and_alias(ac):
    source = case_text(ac) + '\nif __name__ == "__main__":\n    raise RuntimeError("must not execute")\n'
    parsed = read_case_text(source, function="alias")
    np.testing.assert_array_equal(parsed["bus"], ac["bus"])


@pytest.mark.parametrize("source", [
    "import os\ndef case():\n return {}",
    "def case():\n return __import__('os').getcwd()",
    "def case():\n data={}\n for x in range(2):\n  data[x]=1\n return data",
    "@print('executed')\ndef case():\n return {}",
    "def case():\n return {}\ndef other():\n return {}",
    "import numpy as np\ndef case():\n return {'bus': np.zeros((2,13))}",
    "def case():\n data={}\n data['bus']=open('not_allowed')\n return data",
])
def test_rejects_executable_and_ambiguous_cases(source):
    with pytest.raises(ValueError):
        read_case_text(source)


def test_ac_mapping_shunts_and_source_immutability(ac):
    original = copy.deepcopy(ac)
    imported = convert_custom_network(ac)
    assert imported.id_maps["ac_bus"] == {10:0,40:1}
    assert imported.network.ac_bus.at[1,"bs_pu"] == pytest.approx(.003/100)
    assert imported.network.ac_line.at[0,"r_ohm_per_km"] == pytest.approx(.01*12.66**2/100)
    imported.network.ac_load.at[0,"p_mw"] = 9
    for name in ("bus","gen","branch"):
        np.testing.assert_array_equal(ac[name], original[name])
        np.testing.assert_array_equal(imported.ac_case[name], original[name])


def test_transformer_and_inactive_branch(ac):
    ac["branch"][0,c.TAP] = 1.03
    ac["branch"][0,c.SHIFT] = 2
    ac["branch"][0,c.BR_B] = .002
    off = ac["branch"][0].copy()
    off[c.BR_STATUS], off[c.TAP], off[c.SHIFT] = 0,0,0
    ac["branch"] = np.vstack([ac["branch"],off])
    imported = convert_custom_network(ac)
    assert len(imported.network.trafo) == 1
    assert not imported.network.ac_line.at[0,"in_service"]
    data = convert_acdcpf_network_to_opf_data(imported.network)
    trafo = next(iter(data["ac_branches"].values()))
    assert trafo["r"] == pytest.approx(.01)
    assert trafo["b"] == pytest.approx(.002)
    assert trafo["tap"] == 1.03
    assert trafo["shift_degree"] == 2


@pytest.mark.parametrize("fmt,units", [(None,None),("tool7",None),("standard","per_unit"),("other",None)])
def test_hybrid_requires_unambiguous_units(ac,dc,fmt,units):
    with pytest.raises(ValueError):
        convert_custom_network(ac,dc,options=ImportOptions(format=fmt,loss_units=units))


@pytest.mark.parametrize("fmt,units", [("standard","physical"),("tool7","physical"),("tool7","per_unit")])
def test_converter_units_signs_limits_and_dc_power(ac,dc,fmt,units):
    dc["convdc"][0,c.CV_P], dc["convdc"][0,c.CV_Q] = -.2,.03
    imported = convert_custom_network(ac,dc,options=ImportOptions(format=fmt,loss_units=units))
    net = imported.network
    cv = net.vsc.loc[0]
    ibase = 100/(np.sqrt(3)*12.66)
    assert cv.p_mw == .2 and cv.q_mvar == -.03
    assert cv.control_mode == "vdc_q" and net.dc_bus.at[0,"bus_type"] == "vdc"
    assert cv.max_i_ac_ka == pytest.approx(.05*ibase if fmt == "standard" else .05)
    assert cv.s_mva == pytest.approx(5 if fmt == "standard" else np.sqrt(3)*12.66*.05)
    current = .07
    expected_loss = .001+.002*current+.003*current**2
    if units == "per_unit":
        expected_loss = 100*(.001+.002*current/ibase+.003*(current/ibase)**2)
    assert cv.loss_a+cv.loss_b*current+cv.loss_c*current**2 == pytest.approx(expected_loss)
    data = convert_acdcpf_network_to_opf_data(net)
    converter = next(iter(data["converters"].values()))
    assert converter["u_cv_min"] == .9
    assert converter["i_ac_max"] == pytest.approx(cv.max_i_ac_ka/ibase)
    # Different AC/DC bases must preserve both terminal powers and branch loss.
    r_native = net.dc_line.at[0,"r_ohm_per_km"] / (20**2/100)
    vi,vj = 1.,.99
    native_p = 100*2*vi*(vi-vj)/r_native
    source_p = 50*2*vi*(vi-vj)/.01
    native_loss = 100*2*(vi-vj)**2/r_native
    assert native_p == pytest.approx(source_p)
    assert native_loss == pytest.approx(50*2*(vi-vj)**2/.01)


def test_negative_dc_demand_is_generation(ac,dc):
    dc["busdc"][1,c.DC_PD] = -.2
    imported = convert_custom_network(ac,dc,options=ImportOptions(format="standard"))
    assert imported.network.dc_gen.at[0,"p_mw"] == .2
    assert imported.network.dc_gen.at[0,"source_bus_id"] == 205


@pytest.mark.parametrize("matrix,row,col,val,message", [
    ("bus",1,c.BUS_I,10,"duplicate"), ("branch",0,c.T_BUS,99,"unknown"),
    ("bus",1,c.VMIN,1.2,"bounds"), ("bus",0,c.BUS_TYPE,1,"reference"),
    ("branch",0,c.BR_STATUS,0,"reference"), ("gen",0,c.GEN_STATUS,0,"no active generator"),
    ("gen",0,c.PMIN,200,"dispatch"), ("bus",0,c.BASE_KV,0,"positive"),
    ("branch",0,c.RATE_A,-1,"negative"), ("branch",0,c.BR_R,float('nan'),"finite"),
])
def test_invalid_ac_cases(ac,matrix,row,col,val,message):
    ac[matrix][row,col]=val
    with pytest.raises(ValueError,match=message):
        convert_custom_network(ac)


@pytest.mark.parametrize("matrix,row,col,val,message", [
    ("busdc",1,c.DC_BUS,101,"duplicate"), ("branchdc",0,c.DC_TO,88,"unknown"),
    ("convdc",0,c.CV_DC_TYPE,3,"code"), ("convdc",0,c.CV_DC_TYPE,1,"reference"),
    ("convdc",0,c.CV_IMAX,0,"positive"), ("branchdc",0,c.DC_RATIO,1.2,"unsupported"),
])
def test_invalid_dc_cases(ac,dc,matrix,row,col,val,message):
    dc[matrix][row,col]=val
    with pytest.raises(ValueError,match=message):
        convert_custom_network(ac,dc,options=ImportOptions(format="standard"))


def test_unknown_fields_and_matrix_width(ac):
    ac["branch"] = np.ones((1,12))
    with pytest.raises(ValueError,match="columns"):
        convert_custom_network(ac)
    ac["storage"] = []
    with pytest.raises(ValueError,match="Unsupported"):
        convert_custom_network(ac)


def test_custom_factory_isolation_and_table_ids(ac):
    imported = convert_custom_network(ac)
    case = custom_grid_case(imported)
    imported.network.ac_load.at[0,"p_mw"] = 200
    first = create_acdcpf_network_for_stagg5_case(case)
    first.ac_load.at[0,"p_mw"] = 100
    second = create_acdcpf_network_for_stagg5_case(case)
    assert second.ac_load.at[0,"p_mw"] == .5
    tables = editable_network_tables(case)
    assert tables["ac_load"].iloc[0].source_bus_id == 40


def test_multiple_element_ids_are_json_serializable(ac, dc):
    from fastapi.encoders import jsonable_encoder
    # Native append_row returns numpy.int64 after the first element.
    for case, key, status in ((ac,"gen",c.GEN_STATUS), (ac,"branch",c.BR_STATUS), (dc,"branchdc",c.DC_STATUS)):
        extra = case[key][0].copy()
        extra[status] = 0
        case[key] = np.vstack([case[key],extra])
    dc["busdc"][1,c.DC_AC_BUS] = 40
    extra = dc["convdc"][0].copy()
    extra[c.CV_BUS], extra[c.CV_STATUS] = 205, 0
    dc["convdc"] = np.vstack([dc["convdc"],extra])
    imported = convert_custom_network(ac,dc,options=ImportOptions(format="standard"))
    for table in ("gen","branchdc","convdc"):
        assert type(imported.id_maps[table][1]) is int
    assert type(imported.id_maps["branch"][1]["index"]) is int
    json.dumps(imported.report)
    json.dumps(jsonable_encoder(imported.report))
    case = custom_grid_case(imported)
    assert benchmark._custom_import_metadata(case)["import_report"]


def test_imported_dashboard_edit_preserves_provenance(ac, dc):
    from acdcpf_opf.benchmarks.stagg5.service import apply_table_overrides, changed_table_overrides
    dc["busdc"][1, c.DC_PD] = -1.
    case = custom_grid_case(convert_custom_network(ac, dc, options=ImportOptions(format="standard")))
    base = editable_network_tables(case)
    edited = {name: frame.copy(deep=True) for name, frame in base.items()}
    edited["dc_gen"].at[0, "p_mw"] = .4
    overrides = changed_table_overrides(base, edited)
    net = create_acdcpf_network_for_stagg5_case(case)
    apply_table_overrides(net, overrides)
    assert net.dc_gen.at[0, "p_mw"] == .4
    assert net.dc_gen.at[0, "source_bus_id"] == 205
    overrides["dc_gen"].loc[:, "source_bus_id"] = 999
    apply_table_overrides(net, overrides)
    assert net.dc_gen.at[0, "source_bus_id"] == 205
    overrides["dc_gen"]["source_unknown"] = 1
    with pytest.raises(ValueError, match="source_unknown"):
        apply_table_overrides(net, overrides)
    assert create_acdcpf_network_for_stagg5_case(case).dc_gen.at[0, "p_mw"] == 1.


def test_dashboard_import_helpers(ac,dc):
    from acdcpf_opf.dashboard import web
    result = web.import_network_payload({"ac_name":"demo.py","ac_text":case_text(ac)})
    key = result["import_id"]
    request = web.benchmark_request_from_payload({"grid_case":key})
    assert request.skip_opf and not request.include_pyflow_reference
    assert web.network_inputs_payload(key,"original")["editable"]
    assert any(item["key"] == key for item in web.dashboard_config_payload()["cases"])
    with pytest.raises(ValueError,match="perturbations"):
        web.benchmark_request_from_payload({"grid_case":key,"vsc_setpoint_scenario":"perturbed"})
    with pytest.raises(ValueError,match="Import the files again"):
        web.resolve_dashboard_case("custom_expired")
    hybrid = web.import_network_payload({"ac_text":case_text(ac),"dc_text":case_text(dc),"format":"standard"})
    req = web.benchmark_request_from_payload({"grid_case":hybrid["import_id"],"write_exports":False})
    assert not req.skip_opf
    assert req.grid_case.converter_active_power_indices == ()
    assert req.grid_case.converter_reactive_power_indices == (0,)


def test_pf_profiles_edits_and_exports(ac,tmp_path):
    case = custom_grid_case(convert_custom_network(ac))
    profile = tmp_path/"profile.csv"
    profile.write_text("time_index;element_type;element_id;scale\n0;ac_load;0;1\n1;ac_load;0;0.8\n")
    request = BenchmarkRequest(grid_case=case, profile_input=profile, skip_opf=True, include_pyflow_reference=False,
        table_overrides={"ac_load":[{"element_id":0,"p_mw":.6}]},output_directory=tmp_path/"reports")
    result = run_benchmark_request(request)
    assert result.is_profiled and len(result.time_points)==2
    assert all(point.runs[0].success for point in result.time_points)
    assert result.html_path.is_file() and result.xlsx_paths and result.csv_paths
    assert "Custom Network Import" in result.markdown
    assert "import_id_maps" in result.csv_paths[0].read_text(encoding="utf-8-sig")
    assert "custom_import" in result.html_path.read_text(encoding="utf-8")
    assert create_acdcpf_network_for_stagg5_case(case).ac_load.at[0,"p_mw"] == .5


def test_small_import_solves_through_ipopt(ac,dc,tmp_path):
    if not ipopt_executable_path().is_file():
        pytest.skip("IPOPT unavailable")
    case = custom_grid_case(convert_custom_network(ac,dc,options=ImportOptions(format="standard")))
    result = run_benchmark_request(BenchmarkRequest(grid_case=case,include_pyflow_reference=False,output_directory=tmp_path))
    opf = next(run for run in result.runs if run.label == benchmark.TOOL1_OBJECTIVE_LABEL)
    assert opf.diagnostics["ipopt_executed_through_pyomo"]
    assert opf.success, opf.message
    assert opf.diagnostics["max_pyomo_constraint_residual"] < 1e-6
    assert opf.diagnostics["max_variable_bound_violation"] < 1e-6
    assert opf.diagnostics["physics_validation"]["failed"] == 0
    assert opf.losses_mw["total_active_losses_mw"] >= 0


@pytest.mark.parametrize("hybrid,units", [(False,None),(True,"physical"),(True,"per_unit")])
def test_supplied_tool7_cases(hybrid,units):
    root = Path(__file__).resolve().parents[1]/"inputs"
    if not (root/"case33_ieee_AC.py").exists():
        pytest.skip("User-supplied Tool #7 inputs are not part of the distribution")
    imported = load_custom_network(root/("case33h_ieee_ac.py" if hybrid else "case33_ieee_AC.py"),root/"case33h_ieee_dc.py" if hybrid else None,options=ImportOptions(format="tool7",loss_units=units))
    counts = imported.report["counts"]
    json.dumps(imported.report)
    assert counts["ac_bus"] == (21 if hybrid else 33)
    assert counts["ac_line"]+counts["trafo"] == (20 if hybrid else 35)
    assert counts["dc_bus"] == (17 if hybrid else 0)
    assert counts["dc_line"] == (14 if hybrid else 0)
    assert counts["vsc"] == (3 if hybrid else 0)
    if not hybrid:
        assert (~imported.network.ac_line.in_service.astype(bool)).sum() == 3
    case = custom_grid_case(imported)
    pf,_ = benchmark._run_acdcpf_pf("original",case)
    assert pf.success, pf.message
    if not ipopt_executable_path().is_file():
        pytest.skip("IPOPT unavailable")
    benchmark._ensure_local_ipopt_on_path()
    controls = benchmark._benchmark_opf_control_selection(case) if hybrid else benchmark.OPFControlSelection(ac_gen_q=True)
    opf,_ = benchmark._run_tool1_pyomo_ipopt("original",case,opf_control_selection=controls)
    assert opf.diagnostics["ipopt_executed_through_pyomo"]
    assert "max_pyomo_constraint_residual" in opf.diagnostics
    assert "physics_validation" in opf.diagnostics
    if not opf.success:
        assert not opf.losses_mw  # failed iterates are never presented as physical outputs
        assert "infeasible" in opf.message or "validation failed" in opf.message


def test_import_over_http_and_run(ac, dc, tmp_path):
    import os
    import socket
    import subprocess
    import sys
    import time
    from urllib.error import HTTPError, URLError
    from urllib.request import Request, urlopen

    pytest.importorskip("uvicorn")
    pytest.importorskip("fastapi")
    extra = ac["branch"][0].copy()
    extra[c.BR_STATUS] = 0
    ac["branch"] = np.vstack([ac["branch"],extra])
    with socket.socket() as sock:
        sock.bind(("127.0.0.1",0))
        port = sock.getsockname()[1]
    endpoint = f"http://127.0.0.1:{port}"

    def call(path, payload=None):
        request = Request(endpoint+path, data=json.dumps(payload).encode() if payload is not None else None,
            headers={"Content-Type":"application/json"})
        with urlopen(request,timeout=15) as response:
            return json.loads(response.read())

    with (tmp_path/"server.log").open("w") as log:
        process = subprocess.Popen([sys.executable,"-m","uvicorn","acdcpf_opf.dashboard.web:create_app","--factory","--host","127.0.0.1","--port",str(port)],
            stdout=log,stderr=log,env=dict(os.environ,TOOL1_REPORT_DIR=str(tmp_path/"reports")))
        try:
            deadline = time.monotonic()+30
            while True:
                try:
                    call("/api/config")
                    break
                except URLError:
                    assert process.poll() is None and time.monotonic()<deadline
                    time.sleep(.1)
            payload={"ac_text":case_text(ac),"dc_text":case_text(dc),"ac_name":"custom.py","dc_name":"custom_dc.py"}
            with pytest.raises(HTTPError) as error:
                call("/api/networks/import",payload)
            assert error.value.code == 400
            assert b"explicit format" in error.value.read()
            imported=call("/api/networks/import",dict(payload,format="standard"))
            key=imported["import_id"]
            assert any(case["key"] == key for case in call("/api/config")["cases"])
            assert imported["report"]["counts"]["dc_bus"]==2
            assert call(f"/api/network-inputs/{key}/original")["editable"]
            assert call(f"/api/network-limits/{key}/original",{"table_overrides":{}})["rows"]
            # PF-only HTTP execution works without IPOPT too.
            call("/api/runs",{"grid_case":key,"skip_opf":True,"include_pyflow_reference":False})
            deadline=time.monotonic()+45
            while True:
                status=call("/api/runs/current")
                if status["state"] in {"succeeded","failed"}:
                    break
                assert time.monotonic()<deadline
                time.sleep(.1)
            assert status["state"]=="succeeded",status
            assert status["result"]["exports"]["csv"]
            source = Path(__file__).resolve().parents[1]/"inputs"
            if (source/"case33h_ieee_ac.py").exists():
                for ac_name, dc_name, units in [("case33_ieee_AC.py",None,None),("case33h_ieee_ac.py","case33h_ieee_dc.py","physical"),("case33h_ieee_ac.py","case33h_ieee_dc.py","per_unit")]:
                    uploaded=call("/api/networks/import",{
                        "ac_name":ac_name,"ac_text":(source/ac_name).read_text(encoding="utf-8-sig"),
                        "dc_name":dc_name,"dc_text":(source/dc_name).read_text(encoding="utf-8-sig") if dc_name else None,
                        "format":"tool7" if dc_name else None,"loss_units":units})
                    uploaded_key=uploaded["import_id"]
                    assert any(case["key"]==uploaded_key for case in call("/api/config")["cases"])
                    assert call(f"/api/network-inputs/{uploaded_key}/original")["editable"]
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)
