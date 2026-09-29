from __future__ import annotations

import copy
import logging
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

import numpy as np
from scipy.optimize import Bounds, minimize


LOGGER = logging.getLogger(__name__)


try:
    from pypower.idx_bus import PD, VM, VMAX, VMIN
    from pypower.idx_gen import PG, QG, VG, QMAX, QMIN, GEN_STATUS, PMAX, PMIN
    from pypower.idx_brch import RATE_A, PF, QF, PT, QT
except Exception:
    PD, VM, VMAX, VMIN = 2, 7, 11, 12
    PG, QG, QMAX, QMIN, VG, GEN_STATUS, PMAX, PMIN = 1, 2, 3, 4, 5, 7, 8, 9
    RATE_A, PF, QF, PT, QT = 5, 13, 14, 15, 16


try:
    from pyacdcpf.idx_busdc import VDC, VDCMAX, VDCMIN
except Exception:
    VDC, VDCMAX, VDCMIN = 4, 6, 7


try:
    from pyacdcpf.idx_convdc import PCONV, QCONV, VCONV, DROOP, PDCSET, VDCSET
except Exception:
    PCONV, QCONV, VCONV = 3, 4, 5
    DROOP, PDCSET, VDCSET = 20, 21, 22


PFRunner = Callable[[dict[str, Any], dict[str, Any]], Any]


@dataclass(frozen=True)
class MatrixControl:
    name: str
    system: str
    matrix: str
    row: int
    col: int
    lower: float
    upper: float
    scale: float = 1.0


@dataclass(frozen=True)
class OPFOptions:
    maxiter: int = 200
    ftol: float = 1e-7
    penalty_weight: float = 1e6
    failed_pf_penalty: float = 1e12
    verbose: bool = False


@dataclass
class PFOutput:
    ac: dict[str, Any]
    dc: dict[str, Any] | None
    converged: bool
    loss_mw: float | None = None
    raw: Any = None


@dataclass
class OPFResult:
    success: bool
    message: str
    objective_mw: float
    controls: dict[str, float]
    x: np.ndarray
    pf: PFOutput | None
    scipy_result: Any


def solve_acdc_loss_opf(
    case_ac: dict[str, Any],
    case_dc: dict[str, Any] | None,
    controls: Sequence[MatrixControl],
    pf_runner: PFRunner,
    options: OPFOptions | None = None,
) -> OPFResult:
    opts = options or OPFOptions()
    x0 = pack_initial_values(case_ac, case_dc, controls)
    bounds = Bounds(
        [control.lower / control.scale for control in controls],
        [control.upper / control.scale for control in controls],
    )

    cache: dict[tuple[float, ...], tuple[float, PFOutput | None]] = {}

    def objective(x: np.ndarray) -> float:
        key = tuple(np.round(x, 10))
        if key in cache:
            return cache[key][0]

        candidate_ac, candidate_dc = apply_controls(case_ac, case_dc, controls, x)

        try:
            pf = normalize_pf_output(pf_runner(candidate_ac, candidate_dc))
        except Exception as exc:
            LOGGER.debug("PF call failed: %s", exc)
            value = opts.failed_pf_penalty
            cache[key] = (value, None)
            return value

        if not pf.converged:
            value = opts.failed_pf_penalty
            cache[key] = (value, pf)
            return value

        loss_mw = compute_total_active_loss_mw(pf)
        penalty = constraint_penalty(pf.ac, pf.dc)
        value = loss_mw + opts.penalty_weight * penalty

        if opts.verbose:
            LOGGER.info("loss=%.6f MW, penalty=%.6e, objective=%.6f", loss_mw, penalty, value)

        cache[key] = (value, pf)
        return value

    scipy_result = minimize(
        objective,
        x0,
        method="SLSQP",
        bounds=bounds,
        options={
            "maxiter": opts.maxiter,
            "ftol": opts.ftol,
            "disp": opts.verbose,
        },
    )

    final_ac, final_dc = apply_controls(case_ac, case_dc, controls, scipy_result.x)

    try:
        final_pf = normalize_pf_output(pf_runner(final_ac, final_dc))
        final_loss = compute_total_active_loss_mw(final_pf) if final_pf.converged else float("inf")
    except Exception:
        final_pf = None
        final_loss = float("inf")

    return OPFResult(
        success=bool(scipy_result.success and final_pf is not None and final_pf.converged),
        message=str(scipy_result.message),
        objective_mw=float(final_loss),
        controls=unpack_controls(controls, scipy_result.x),
        x=np.asarray(scipy_result.x, dtype=float),
        pf=final_pf,
        scipy_result=scipy_result,
    )


def pack_initial_values(
    case_ac: dict[str, Any],
    case_dc: dict[str, Any] | None,
    controls: Sequence[MatrixControl],
) -> np.ndarray:
    values = []

    for control in controls:
        matrix = get_case_matrix(case_ac, case_dc, control)
        values.append(float(matrix[control.row, control.col]) / control.scale)

    return np.asarray(values, dtype=float)


def apply_controls(
    case_ac: dict[str, Any],
    case_dc: dict[str, Any] | None,
    controls: Sequence[MatrixControl],
    x: np.ndarray,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    new_ac = copy.deepcopy(case_ac)
    new_dc = copy.deepcopy(case_dc) if case_dc is not None else None

    for value, control in zip(x, controls):
        matrix = get_case_matrix(new_ac, new_dc, control)
        matrix[control.row, control.col] = float(value) * control.scale

        if control.system == "ac" and control.matrix == "gen" and control.col == VG:
            sync_generator_voltage_to_bus(new_ac, control.row)

    return new_ac, new_dc


def get_case_matrix(
    case_ac: dict[str, Any],
    case_dc: dict[str, Any] | None,
    control: MatrixControl,
) -> np.ndarray:
    if control.system == "ac":
        case = case_ac
    elif control.system == "dc":
        if case_dc is None:
            raise ValueError(f"Control {control.name} requires a DC case, but case_dc is None.")
        case = case_dc
    else:
        raise ValueError(f"Unknown control system: {control.system}")

    if control.matrix not in case:
        raise KeyError(f"Matrix {control.matrix!r} not found in {control.system} case.")

    matrix = case[control.matrix]

    if control.row >= matrix.shape[0] or control.col >= matrix.shape[1]:
        raise IndexError(
            f"Control {control.name} points outside {control.system}.{control.matrix}: "
            f"row={control.row}, col={control.col}, shape={matrix.shape}"
        )

    return matrix


def sync_generator_voltage_to_bus(case_ac: dict[str, Any], gen_row: int) -> None:
    gen = case_ac["gen"]
    bus = case_ac["bus"]

    gen_bus_number = int(gen[gen_row, 0])
    bus_rows = np.where(bus[:, 0].astype(int) == gen_bus_number)[0]

    if bus_rows.size:
        bus[bus_rows[0], VM] = gen[gen_row, VG]


def normalize_pf_output(raw: Any) -> PFOutput:
    if isinstance(raw, PFOutput):
        return raw

    if isinstance(raw, Mapping):
        ac = raw.get("ac") or raw.get("resultsac") or raw.get("results_ac")
        dc = raw.get("dc") or raw.get("resultsdc") or raw.get("results_dc")
        converged = bool(raw.get("converged", raw.get("success", True)))
        loss_mw = raw.get("loss_mw", raw.get("total_loss_mw"))
        return PFOutput(ac=ac, dc=dc, converged=converged, loss_mw=loss_mw, raw=raw)

    if isinstance(raw, tuple):
        if len(raw) >= 3:
            ac = raw[0]
            dc = raw[1]
            converged = bool(raw[2])
            loss_mw = None
            return PFOutput(ac=ac, dc=dc, converged=converged, loss_mw=loss_mw, raw=raw)

        if len(raw) == 2:
            ac = raw[0]
            converged = bool(raw[1])
            return PFOutput(ac=ac, dc=None, converged=converged, raw=raw)

    raise TypeError("Unsupported PF output format. Adapt normalize_pf_output() to the ACDCPF runner.")


def compute_total_active_loss_mw(pf: PFOutput) -> float:
    if pf.loss_mw is not None:
        return max(float(pf.loss_mw), 0.0)

    loss = 0.0
    loss += compute_ac_branch_loss_mw(pf.ac)

    if pf.dc is not None:
        loss += compute_dc_branch_loss_mw(pf.dc)
        loss += compute_converter_loss_mw(pf.dc)

    if loss > 1e-8:
        return float(loss)

    return max(compute_generation_load_balance_loss_mw(pf.ac), 0.0)


def compute_ac_branch_loss_mw(case_ac: dict[str, Any]) -> float:
    branch = case_ac.get("branch")

    if branch is None or branch.shape[1] <= max(PF, PT):
        return 0.0

    losses = branch[:, PF] + branch[:, PT]
    return float(np.sum(losses))


def compute_dc_branch_loss_mw(case_dc: dict[str, Any]) -> float:
    branchdc = case_dc.get("branchdc")

    if branchdc is None:
        return 0.0

    candidate_pairs = [
        ("PFDC", "PTDC"),
        ("PFD", "PTD"),
        ("PDC_F", "PDC_T"),
    ]

    for left_name, right_name in candidate_pairs:
        left = globals().get(left_name)
        right = globals().get(right_name)

        if isinstance(left, int) and isinstance(right, int) and branchdc.shape[1] > max(left, right):
            return float(np.sum(branchdc[:, left] + branchdc[:, right]))

    return 0.0


def compute_converter_loss_mw(case_dc: dict[str, Any]) -> float:
    convdc = case_dc.get("convdc")

    if convdc is None:
        return 0.0

    for key in ("converter_loss_mw", "conv_loss_mw", "lossconv_mw"):
        value = case_dc.get(key)
        if value is not None:
            return float(np.sum(value))

    return 0.0


def compute_generation_load_balance_loss_mw(case_ac: dict[str, Any]) -> float:
    bus = case_ac.get("bus")
    gen = case_ac.get("gen")

    if bus is None or gen is None:
        return 0.0

    online = gen[:, GEN_STATUS] > 0 if gen.shape[1] > GEN_STATUS else np.ones(gen.shape[0], dtype=bool)
    total_pg = float(np.sum(gen[online, PG]))
    total_pd = float(np.sum(bus[:, PD]))

    return total_pg - total_pd


def constraint_penalty(case_ac: dict[str, Any], case_dc: dict[str, Any] | None) -> float:
    penalty = 0.0

    penalty += ac_voltage_penalty(case_ac)
    penalty += ac_generator_penalty(case_ac)
    penalty += ac_branch_thermal_penalty(case_ac)

    if case_dc is not None:
        penalty += dc_voltage_penalty(case_dc)
        penalty += dc_branch_penalty(case_dc)
        penalty += converter_penalty(case_dc)

    return float(penalty)


def ac_voltage_penalty(case_ac: dict[str, Any]) -> float:
    bus = case_ac.get("bus")

    if bus is None or bus.shape[1] <= max(VM, VMIN, VMAX):
        return 0.0

    return bound_penalty(bus[:, VM], bus[:, VMIN], bus[:, VMAX])


def ac_generator_penalty(case_ac: dict[str, Any]) -> float:
    gen = case_ac.get("gen")

    if gen is None:
        return 0.0

    penalty = 0.0

    if gen.shape[1] > max(PG, PMIN, PMAX):
        penalty += bound_penalty(gen[:, PG], gen[:, PMIN], gen[:, PMAX])

    if gen.shape[1] > max(QG, QMIN, QMAX):
        penalty += bound_penalty(gen[:, QG], gen[:, QMIN], gen[:, QMAX])

    return penalty


def ac_branch_thermal_penalty(case_ac: dict[str, Any]) -> float:
    branch = case_ac.get("branch")

    if branch is None or branch.shape[1] <= max(RATE_A, PF, QF, PT, QT):
        return 0.0

    rate = branch[:, RATE_A]
    active_limit = rate > 0

    if not np.any(active_limit):
        return 0.0

    s_from = np.sqrt(branch[:, PF] ** 2 + branch[:, QF] ** 2)
    s_to = np.sqrt(branch[:, PT] ** 2 + branch[:, QT] ** 2)

    penalty = 0.0
    penalty += upper_bound_penalty(s_from[active_limit], rate[active_limit])
    penalty += upper_bound_penalty(s_to[active_limit], rate[active_limit])

    return penalty


def dc_voltage_penalty(case_dc: dict[str, Any]) -> float:
    busdc = case_dc.get("busdc")

    if busdc is None or busdc.shape[1] <= max(VDC, VDCMIN, VDCMAX):
        return 0.0

    return bound_penalty(busdc[:, VDC], busdc[:, VDCMIN], busdc[:, VDCMAX])


def dc_branch_penalty(case_dc: dict[str, Any]) -> float:
    return 0.0


def converter_penalty(case_dc: dict[str, Any]) -> float:
    return 0.0


def bound_penalty(values: np.ndarray, lower: np.ndarray, upper: np.ndarray) -> float:
    lower_violation = np.maximum(lower - values, 0.0)
    upper_violation = np.maximum(values - upper, 0.0)
    return float(np.sum(lower_violation**2 + upper_violation**2))


def upper_bound_penalty(values: np.ndarray, upper: np.ndarray) -> float:
    violation = np.maximum(values - upper, 0.0)
    return float(np.sum(violation**2))


def unpack_controls(
    controls: Sequence[MatrixControl],
    x: np.ndarray,
) -> dict[str, float]:
    return {
        control.name: float(value) * control.scale
        for control, value in zip(controls, x)
    }


def build_stagg5_initial_controls(
    case_ac: dict[str, Any],
    case_dc: dict[str, Any] | None = None,
) -> list[MatrixControl]:
    controls: list[MatrixControl] = []

    gen = case_ac["gen"]

    for gen_row in range(gen.shape[0]):
        is_slack_like = gen_row == 0

        if not is_slack_like:
            controls.append(
                MatrixControl(
                    name=f"gen_{gen_row}_pg_mw",
                    system="ac",
                    matrix="gen",
                    row=gen_row,
                    col=PG,
                    lower=float(gen[gen_row, PMIN]),
                    upper=float(gen[gen_row, PMAX]),
                    scale=100.0,
                )
            )

        controls.append(
            MatrixControl(
                name=f"gen_{gen_row}_vg_pu",
                system="ac",
                matrix="gen",
                row=gen_row,
                col=VG,
                lower=0.95,
                upper=1.08,
                scale=1.0,
            )
        )

    if case_dc is not None and "convdc" in case_dc:
        convdc = case_dc["convdc"]

        for conv_row in range(convdc.shape[0]):
            controls.append(
                MatrixControl(
                    name=f"conv_{conv_row}_p_mw",
                    system="dc",
                    matrix="convdc",
                    row=conv_row,
                    col=PCONV,
                    lower=-100.0,
                    upper=100.0,
                    scale=100.0,
                )
            )

            controls.append(
                MatrixControl(
                    name=f"conv_{conv_row}_q_mvar",
                    system="dc",
                    matrix="convdc",
                    row=conv_row,
                    col=QCONV,
                    lower=-100.0,
                    upper=100.0,
                    scale=100.0,
                )
            )

    return controls


def run_stagg5_example() -> OPFResult:
    from case5_stagg import case5_stagg
    from case5_stagg_MTDCdroop import case5_stagg_MTDCdroop
    from runacdcpf import runacdcpf

    case_ac = case5_stagg()
    case_dc = case5_stagg_MTDCdroop()

    def pf_runner(ac: dict[str, Any], dc: dict[str, Any]) -> Any:
        return runacdcpf(ac, dc)

    controls = build_stagg5_initial_controls(case_ac, case_dc)

    return solve_acdc_loss_opf(
        case_ac=case_ac,
        case_dc=case_dc,
        controls=controls,
        pf_runner=pf_runner,
        options=OPFOptions(maxiter=100, ftol=1e-6, penalty_weight=1e6, verbose=True),
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    result = run_stagg5_example()

    print("Success:", result.success)
    print("Message:", result.message)
    print("Objective MW:", result.objective_mw)
    print("Controls:")
    for name, value in result.controls.items():
        print(f"  {name}: {value:.6f}")