"""Workbench request contracts. Legacy field names remain wire-compatible."""
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field, StrictBool


class RequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class ImportRequest(RequestModel):
    ac_name: str = "ac_case.py"
    ac_text: str = Field(min_length=1)
    dc_name: str | None = "dc_case.py"
    dc_text: str | None = None
    format: Literal["standard", "tool7"] | None = None
    loss_units: Literal["physical", "per_unit"] | None = None
    dc_voltage_convention: Literal["legacy", "per_pole", "pole_to_pole"] = "legacy"


class RunRequest(RequestModel):
    grid_case: str = "original"
    vsc_setpoint_scenario: str = "original"
    opf_controls: dict[str, StrictBool] | list[str] | str | None = None
    control_device_scope: str = "benchmark"
    control_margin_percent: float | None = Field(default=None, ge=0)
    profile_key: str = "none"
    profile_path: str = ""
    table_overrides: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    skip_opf: StrictBool | None = None
    pf_policy: Literal["unconstrained", "converter_limited"] = "unconstrained"
    include_pyflow_reference: StrictBool = False
    write_exports: StrictBool = True
    include_markdown: StrictBool = True
    include_csv: StrictBool = True
    include_xlsx: StrictBool = True
    include_html: StrictBool = True


class LimitsRequest(RequestModel):
    table_overrides: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
