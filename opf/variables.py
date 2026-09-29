from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Sequence

import numpy as np

from .config import OPFConfig


@dataclass(frozen=True)
class DecisionVariable:
    """One scalar OPF decision variable.

    Values are stored in pyflow's native units. For generator and converter
    power setpoints this means per-unit on ``grid.S_base``.
    """

    name: str
    group: str
    index: int
    attribute: str
    initial_value: float
    lower_bound: float | None
    upper_bound: float | None
    unit: str = "p.u."


@dataclass
class DecisionVariableSet:
    """Stable decision-variable ordering for packing and unpacking vectors."""

    variables: list[DecisionVariable]

    def __len__(self) -> int:
        return len(self.variables)

    def pack(self, case: Any | None = None) -> np.ndarray:
        """Pack variable values into a vector using the stored ordering."""
        if case is None:
            return np.array([var.initial_value for var in self.variables], dtype=float)
        return np.array([self.get_value(case, var) for var in self.variables], dtype=float)

    def unpack(self, vector: Iterable[float]) -> dict[str, float]:
        """Return a name-to-value mapping from an optimizer vector."""
        values = list(vector)
        _check_vector_size(self.variables, values)
        return {var.name: float(value) for var, value in zip(self.variables, values)}

    def bounds(self) -> list[tuple[float | None, float | None]]:
        """Return optimizer bounds in the same order as ``pack``."""
        return [(var.lower_bound, var.upper_bound) for var in self.variables]

    def apply(self, case: Any, vector: Iterable[float]) -> None:
        """Apply optimizer values to a pyflow-compatible case in place."""
        values = list(vector)
        _check_vector_size(self.variables, values)
        for var, value in zip(self.variables, values):
            self.set_value(case, var, float(value))

    def get_value(self, case: Any, var: DecisionVariable) -> float:
        target = _find_target(case, var)
        return float(getattr(target, var.attribute))

    def set_value(self, case: Any, var: DecisionVariable, value: float) -> None:
        target = _find_target(case, var)
        setattr(target, var.attribute, value)


def build_decision_variables(case: Any, config: OPFConfig) -> DecisionVariableSet:
    """Discover OPF variables enabled by ``config`` from a pyflow case."""
    variables: list[DecisionVariable] = []

    if config.optimize_ac_generator_active_power:
        variables.extend(
            _generator_variables(
                case,
                ac=True,
                attribute="PGen",
                selected_indices=config.ac_generator_active_power_indices,
            )
        )
    if config.optimize_ac_generator_reactive_power:
        variables.extend(
            _generator_variables(
                case,
                ac=True,
                attribute="QGen",
                selected_indices=config.ac_generator_reactive_power_indices,
            )
        )
    if config.optimize_ac_generator_voltage:
        variables.extend(
            _ac_generator_voltage_variables(
                case,
                selected_node_indices=config.ac_generator_voltage_node_indices,
            )
        )
    if config.optimize_dc_generator_active_power:
        variables.extend(
            _generator_variables(
                case,
                ac=False,
                attribute="PGen",
                selected_indices=config.dc_generator_active_power_indices,
            )
        )
    if config.optimize_converter_active_power:
        variables.extend(
            _converter_variables(
                case,
                attribute="P_AC",
                selected_indices=config.converter_active_power_indices,
            )
        )
    if config.optimize_converter_reactive_power:
        variables.extend(
            _converter_variables(
                case,
                attribute="Q_AC",
                selected_indices=config.converter_reactive_power_indices,
            )
        )

    return DecisionVariableSet(variables)


def apply_decision_variables(
    case: Any,
    variable_set: DecisionVariableSet,
    vector: Iterable[float],
) -> None:
    """Apply a vector to a case using an existing variable set."""
    variable_set.apply(case, vector)


def _generator_variables(
    case: Any,
    *,
    ac: bool,
    attribute: str,
    selected_indices: Sequence[int] | None = None,
) -> list[DecisionVariable]:
    nodes = getattr(case, "nodes_AC" if ac else "nodes_DC", [])
    group = "ac_gen" if ac else "dc_gen"
    variables: list[DecisionVariable] = []
    seen: set[int] = set()
    selected = _normalize_selected_indices(selected_indices)

    for node, gen in _iter_connected_generators(nodes):
        node_type = str(getattr(node, "type", ""))
        if ac and attribute == "PGen" and node_type == "Slack":
            continue
        if ac and attribute == "QGen" and node_type in {"Slack", "PV"}:
            continue

        gen_number = int(getattr(gen, "genNumber"))
        if gen_number in seen or not hasattr(gen, attribute):
            continue
        if not _is_selected(gen_number, selected):
            continue
        seen.add(gen_number)

        if attribute == "PGen":
            lower = _finite_or_none(getattr(gen, "Min_pow_gen", None))
            upper = _finite_or_none(getattr(gen, "Max_pow_gen", None))
        else:
            lower = _finite_or_none(getattr(gen, "Min_pow_genR", None))
            upper = _finite_or_none(getattr(gen, "Max_pow_genR", None))

        variables.append(
            DecisionVariable(
                name=f"{group}[{gen_number}].{attribute}",
                group=group,
                index=gen_number,
                attribute=attribute,
                initial_value=float(getattr(gen, attribute)),
                lower_bound=lower,
                upper_bound=upper,
            )
        )
    return variables


def _ac_generator_voltage_variables(
    case: Any,
    *,
    selected_node_indices: Sequence[int] | None = None,
) -> list[DecisionVariable]:
    variables: list[DecisionVariable] = []
    seen_nodes: set[int] = set()
    selected = _normalize_selected_indices(selected_node_indices)

    for node in getattr(case, "nodes_AC", []):
        node_type = str(getattr(node, "type", ""))
        if node_type == "Slack" or not getattr(node, "connected_gen", []):
            continue
        if not hasattr(node, "V"):
            continue

        node_number = int(getattr(node, "nodeNumber"))
        if node_number in seen_nodes:
            continue
        if not _is_selected(node_number, selected):
            continue
        seen_nodes.add(node_number)

        variables.append(
            DecisionVariable(
                name=f"ac_voltage_setpoint[{node_number}].V",
                group="ac_voltage_setpoint",
                index=node_number,
                attribute="V",
                initial_value=float(getattr(node, "V")),
                lower_bound=_finite_or_none(getattr(node, "Umin", None)),
                upper_bound=_finite_or_none(getattr(node, "Umax", None)),
            )
        )

    return variables


def _converter_variables(
    case: Any,
    *,
    attribute: str,
    selected_indices: Sequence[int] | None = None,
) -> list[DecisionVariable]:
    variables: list[DecisionVariable] = []
    s_base = float(getattr(case, "S_base", 1.0))
    selected = _normalize_selected_indices(selected_indices)

    for conv in getattr(case, "Converters_ACDC", []):
        # DC-slack converters balance the DC grid. Keep them fixed until the
        # dispatch policy for slack sharing is explicit.
        if attribute == "P_AC" and str(getattr(conv, "type", "")) == "Slack":
            continue
        # PV/slack AC-side converters control voltage, so Q is a solved output
        # of the PF problem rather than a meaningful reactive-power setpoint.
        if attribute == "Q_AC" and str(getattr(conv, "AC_type", "")) in {"PV", "Slack"}:
            continue

        conv_number = int(getattr(conv, "ConvNumber"))
        if not _is_selected(conv_number, selected):
            continue

        s_max_pu = _finite_or_none(getattr(conv, "MVA_max", None))
        if s_max_pu is not None:
            s_max_pu = s_max_pu / s_base

        variables.append(
            DecisionVariable(
                name=f"converter[{conv_number}].{attribute}",
                group="converter",
                index=conv_number,
                attribute=attribute,
                initial_value=float(getattr(conv, attribute)),
                lower_bound=-s_max_pu if s_max_pu is not None else None,
                upper_bound=s_max_pu,
            )
        )
    return variables


def _iter_connected_generators(nodes: Iterable[Any]) -> Iterable[tuple[Any, Any]]:
    for node in nodes:
        for gen in getattr(node, "connected_gen", []):
            yield node, gen


def _find_target(case: Any, var: DecisionVariable) -> Any:
    if var.group == "converter":
        for conv in getattr(case, "Converters_ACDC", []):
            if int(getattr(conv, "ConvNumber")) == var.index:
                return conv
    elif var.group == "ac_voltage_setpoint":
        for node in getattr(case, "nodes_AC", []):
            if int(getattr(node, "nodeNumber")) == var.index:
                return node
    elif var.group == "ac_gen":
        return _find_generator(getattr(case, "nodes_AC", []), var.index)
    elif var.group == "dc_gen":
        return _find_generator(getattr(case, "nodes_DC", []), var.index)
    raise KeyError(f"Decision variable target not found: {var.name}")


def _find_generator(nodes: Iterable[Any], gen_number: int) -> Any:
    for _, gen in _iter_connected_generators(nodes):
        if int(getattr(gen, "genNumber")) == gen_number:
            return gen
    raise KeyError(f"Generator {gen_number} not found.")


def _finite_or_none(value: Any) -> float | None:
    if value is None:
        return None
    value = float(value)
    if not np.isfinite(value):
        return None
    if abs(value) >= 1e8:
        return None
    return value


def _normalize_selected_indices(indices: Sequence[int] | None) -> set[int] | None:
    if indices is None:
        return None
    return {int(index) for index in indices}


def _is_selected(index: int, selected_indices: set[int] | None) -> bool:
    return selected_indices is None or int(index) in selected_indices


def _check_vector_size(variables: list[DecisionVariable], values: list[float]) -> None:
    if len(values) != len(variables):
        raise ValueError(
            f"Expected {len(variables)} decision values, got {len(values)}."
        )
