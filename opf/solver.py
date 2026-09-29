from __future__ import annotations

from dataclasses import dataclass
import copy
import logging
from typing import Any

import numpy as np

from acdcpf_opf.powerflow.acdcpf_adapter import ACDCPFAdapter
from acdcpf_opf.powerflow.base import PowerFlowSolver
from acdcpf_opf.powerflow.result import PFResult

from .config import OPFConfig
from .constraints import (
    ConstraintViolation,
    compute_constraint_violations,
    max_constraint_violation,
    violated_constraints,
)
from .objective import ObjectiveEvaluation, total_active_loss_objective
from .result import DecisionVariableChange, OPFResult
from .variables import DecisionVariableSet, build_decision_variables

logger = logging.getLogger(__name__)


@dataclass
class _CandidateEvaluation:
    x: np.ndarray
    objective: ObjectiveEvaluation
    violations: list[ConstraintViolation]
    max_violation: float
    trial_case: Any


class OPFSolver:
    """Outer-loop OPF solver that evaluates candidates through a PF backend."""

    def __init__(
        self,
        *,
        config: OPFConfig | None = None,
        pf_solver: PowerFlowSolver | None = None,
    ) -> None:
        self.config = config or OPFConfig()
        self.pf_solver = pf_solver or self._build_default_pf_solver(self.config)
        self._cache: _CandidateEvaluation | None = None
        self._evaluation_count = 0
        self._callback_count = 0
        logging.getLogger(__package__).setLevel(self.config.logging_level)

    def solve(self, case: Any) -> OPFResult:
        """Solve an OPF problem for a pyflow-compatible AC/DC case."""
        if self.config.objective != "total_active_loss_minimization":
            raise ValueError(f"Unsupported OPF objective: {self.config.objective}")

        variable_set = build_decision_variables(case, self.config)
        x0 = variable_set.pack(case)

        if len(variable_set) == 0:
            evaluation = self._evaluate_candidate(case, variable_set, x0)
            return self._result_from_evaluation(
                evaluation=evaluation,
                variable_set=variable_set,
                base_case=case,
                raw_optimizer_result={"status": "no_decision_variables"},
                optimizer_success=True,
                optimizer_status="no_decision_variables",
                message="No decision variables enabled; returned base PF evaluation.",
                iterations=0,
            )

        try:
            from scipy.optimize import minimize
        except ImportError as exc:  # pragma: no cover - scipy is present in the project venv
            raise RuntimeError("scipy.optimize is required by the default OPF optimizer.") from exc

        bounds = variable_set.bounds()
        constraints = []
        if self.config.enforce_constraints:
            constraints.append(
                {
                    "type": "ineq",
                    "fun": lambda x: -self._evaluate_candidate(case, variable_set, x).max_violation,
                }
            )

        result = minimize(
            fun=lambda x: self._objective_value(case, variable_set, x),
            x0=x0,
            method=self.config.optimizer_method,
            bounds=bounds,
            constraints=constraints,
            callback=lambda x: self._log_iteration(case, variable_set, x),
            options={
                "maxiter": self.config.max_iterations,
                "ftol": self.config.tolerance,
                "disp": bool(self.config.debug),
            },
        )

        final_eval = self._evaluate_candidate(case, variable_set, result.x)
        optimizer_success = bool(result.success)
        return self._result_from_evaluation(
            evaluation=final_eval,
            variable_set=variable_set,
            base_case=case,
            raw_optimizer_result=result,
            optimizer_success=optimizer_success,
            optimizer_status=result.status,
            message=str(result.message),
            iterations=getattr(result, "nit", None),
        )

    def _objective_value(
        self,
        base_case: Any,
        variable_set: DecisionVariableSet,
        x: np.ndarray,
    ) -> float:
        evaluation = self._evaluate_candidate(base_case, variable_set, x)
        value = float(evaluation.objective.value)
        if evaluation.max_violation > 0.0 and self.config.constraint_penalty_weight > 0.0:
            value += self.config.constraint_penalty_weight * evaluation.max_violation**2
        return value

    def _evaluate_candidate(
        self,
        base_case: Any,
        variable_set: DecisionVariableSet,
        x: np.ndarray,
    ) -> _CandidateEvaluation:
        x = np.array(x, dtype=float)
        if self._cache is not None and np.array_equal(self._cache.x, x):
            return self._cache

        self._evaluation_count += 1
        trial_case = copy.deepcopy(base_case)
        variable_set.apply(trial_case, x)

        pf_result = self.pf_solver.solve(
            trial_case,
            copy_case=False,
            write_back=False,
        )
        objective = total_active_loss_objective(pf_result, self.config)
        violations = compute_constraint_violations(trial_case, pf_result, self.config)
        max_violation = max_constraint_violation(violations)

        self._log_evaluation(self._evaluation_count, objective, pf_result, max_violation)

        self._cache = _CandidateEvaluation(
            x=x.copy(),
            objective=objective,
            violations=violations,
            max_violation=max_violation,
            trial_case=trial_case,
        )
        return self._cache

    def _result_from_evaluation(
        self,
        *,
        evaluation: _CandidateEvaluation,
        variable_set: DecisionVariableSet,
        base_case: Any,
        raw_optimizer_result: Any,
        optimizer_success: bool,
        optimizer_status: Any,
        message: str,
        iterations: int | None,
    ) -> OPFResult:
        pf_result = evaluation.objective.pf_result
        hard_violations = violated_constraints(evaluation.violations)
        total_active_losses = float(sum(evaluation.objective.loss_breakdown_mw.values()))
        success = (
            optimizer_success
            and pf_result.converged
            and evaluation.max_violation <= self.config.tolerance
        )

        return OPFResult(
            success=success,
            objective_value=float(evaluation.objective.value),
            decision_variables=variable_set.unpack(evaluation.x),
            pf_result=pf_result,
            decision_variable_changes=self._build_decision_variable_changes(
                variable_set=variable_set,
                optimized_x=evaluation.x,
                base_case=base_case,
            ),
            constraint_violations=evaluation.violations,
            total_active_losses=total_active_losses,
            ac_branch_active_losses=pf_result.ac_branch_active_losses,
            dc_branch_active_losses=pf_result.dc_branch_active_losses,
            converter_active_losses=pf_result.converter_active_losses,
            iterations=iterations,
            optimizer_status=optimizer_status,
            message=message,
            raw_optimizer_result=raw_optimizer_result,
            diagnostics={
                "evaluations": self._evaluation_count,
                "max_constraint_violation": evaluation.max_violation,
                "violated_constraints": [item.name for item in hard_violations],
                "loss_breakdown_mw": evaluation.objective.loss_breakdown_mw,
            },
        )

    def _log_iteration(
        self,
        base_case: Any,
        variable_set: DecisionVariableSet,
        x: np.ndarray,
    ) -> None:
        self._callback_count += 1
        evaluation = self._evaluate_candidate(base_case, variable_set, x)
        pf_result = evaluation.objective.pf_result
        total_active_losses = float(sum(evaluation.objective.loss_breakdown_mw.values()))
        logger.info(
            "OPF iteration %s: objective=%.9g, pf_converged=%s, max_mismatch=%s, "
            "total_losses=%.9g MW, max_constraint_violation=%.9g",
            self._callback_count,
            evaluation.objective.value,
            pf_result.converged,
            pf_result.max_mismatch,
            total_active_losses,
            evaluation.max_violation,
        )

    def _log_evaluation(
        self,
        evaluation_number: int,
        objective: ObjectiveEvaluation,
        pf_result: PFResult,
        max_violation: float,
    ) -> None:
        breakdown = objective.loss_breakdown_mw
        total_active_losses = float(sum(breakdown.values()))
        logger.debug(
            "OPF eval %s: objective=%.9g, pf_converged=%s, max_mismatch=%s, "
            "ac_loss=%.9g MW, dc_loss=%.9g MW, converter_loss=%.9g MW, "
            "total_loss=%.9g MW, max_constraint_violation=%.9g",
            evaluation_number,
            objective.value,
            pf_result.converged,
            pf_result.max_mismatch,
            breakdown.get("ac_branch_mw", 0.0),
            breakdown.get("dc_branch_mw", 0.0),
            breakdown.get("converter_mw", 0.0),
            total_active_losses,
            max_violation,
        )

    @staticmethod
    def _build_default_pf_solver(config: OPFConfig) -> PowerFlowSolver:
        if config.pf_backend != "acdcpf":
            raise ValueError(f"Unsupported PF backend: {config.pf_backend}")
        return ACDCPFAdapter(
            max_iter_outer=config.pf_max_iter_outer,
            max_iter_inner=config.pf_max_iter_inner,
            tolerance=config.pf_tolerance,
            verbose=config.debug,
        )

    @staticmethod
    def _build_decision_variable_changes(
        *,
        variable_set: DecisionVariableSet,
        optimized_x: np.ndarray,
        base_case: Any,
    ) -> list[DecisionVariableChange]:
        s_base = float(getattr(base_case, "S_base", 1.0))
        changes: list[DecisionVariableChange] = []

        for var, after_value in zip(variable_set.variables, optimized_x):
            before_value = float(var.initial_value)
            after_value = float(after_value)
            delta_value = after_value - before_value
            physical_unit = _physical_unit_for_variable(var.attribute)
            scale = s_base if physical_unit in {"MW", "MVAr"} else None

            changes.append(
                DecisionVariableChange(
                    name=var.name,
                    before_value=before_value,
                    after_value=after_value,
                    delta_value=delta_value,
                    unit=var.unit,
                    before_physical=before_value * scale if scale is not None else None,
                    after_physical=after_value * scale if scale is not None else None,
                    delta_physical=delta_value * scale if scale is not None else None,
                    physical_unit=physical_unit,
                )
            )

        return changes


def _physical_unit_for_variable(attribute: str) -> str | None:
    if attribute.startswith("Q"):
        return "MVAr"
    if attribute.startswith("P"):
        return "MW"
    return None
