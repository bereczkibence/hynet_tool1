"""Compare numerical results with an unchanged original checkout and environment."""
import argparse
import json
import math
import os
from pathlib import Path
import subprocess

parser = argparse.ArgumentParser()
parser.add_argument("original", type=Path)
args = parser.parse_args()
root = Path(__file__).resolve().parents[1]
code = r'''
import json
from pathlib import Path
from acdcpf_opf.benchmarks.stagg5.service import BenchmarkRequest, run_benchmark_request, editable_network_tables, changed_table_overrides
from acdcpf_opf.benchmarks.stagg5.case_variants import custom_grid_case
from acdcpf_opf.data.custom_network import load_custom_network, ImportOptions
from dataclasses import fields
results={}
skip_field=next(f.name for f in fields(BenchmarkRequest) if f.name.startswith("skip_opf"))
def capture(name, request):
    bundle=run_benchmark_request(request)
    results[name]=[{"label":run.label,"success":run.success,"losses":run.losses_mw,
        "residual":run.diagnostics.get("max_pyomo_constraint_residual"),
        "physics_failed":run.diagnostics.get("physics_validation",{}).get("failed")}
        for run in bundle.runs]
capture("stagg5",BenchmarkRequest(include_pyflow_reference=False,write_exports=False))
p=Path("inputs")
if (p/"case33h_ieee_ac.py").exists():
    case=custom_grid_case(load_custom_network(p/"case33h_ieee_ac.py",p/"case33h_ieee_dc.py",options=ImportOptions(format="tool7",loss_units="per_unit")))
    capture("hybrid_original",BenchmarkRequest(grid_case=case,include_pyflow_reference=False,write_exports=False))
    tables=editable_network_tables(case)
    edited={k:v.copy(deep=True) for k,v in tables.items()}
    edited["dc_gen"].loc[edited["dc_gen"].source_bus_id==35,"p_mw"]=.4
    capture("hybrid_dc35_04_pf",BenchmarkRequest(grid_case=case,**{skip_field:True},include_pyflow_reference=False,write_exports=False,table_overrides=changed_table_overrides(tables,edited)))
print(json.dumps(results,allow_nan=False))
'''
outputs = []
for checkout in (args.original.resolve(), root):
    python = checkout / ".venv" / "Scripts" / "python.exe"
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", TOOL1_REPORT_DIR=str(root / ".validation/reports"))
    result = subprocess.run([str(python), "-c", code], cwd=checkout, env=env, check=True, capture_output=True, text=True)
    outputs.append(json.loads(result.stdout.strip().splitlines()[-1].replace("OPF_BME", "Tool1 (acdcopf)")))

def compare(left, right):
    if isinstance(left, dict):
        assert left.keys() == right.keys()
        for key in left:
            compare(left[key], right[key])
    elif isinstance(left, list):
        assert len(left) == len(right)
        for a,b in zip(left,right):
            compare(a,b)
    elif isinstance(left, float):
        assert math.isclose(left, right, abs_tol=1e-8, rel_tol=1e-8), (left,right)
    else:
        assert left == right, (left,right)
compare(*outputs)
destination = root / ".validation"
destination.mkdir(exist_ok=True)
(destination / "numerical_comparison.json").write_text(json.dumps({"matched": True, "original": outputs[0], "tool1": outputs[1]}, indent=2))
print("Original and Tool1 numerical results match within 1e-8.")
