from __future__ import annotations

from types import SimpleNamespace
import unittest

import numpy as np

from acdcpf_opf.opf.config import OPFConfig
from acdcpf_opf.opf.result import DecisionVariableChange, OPFResult
from acdcpf_opf.opf.solver import OPFSolver
from acdcpf_opf.powerflow.base import PowerFlowSolver
from acdcpf_opf.powerflow.result import PFResult


class FakePowerFlowSolver(PowerFlowSolver):
    name = "fake"

    def solve(self, case, *, copy_case=True, write_back=False) -> PFResult:
        return PFResult(
            converged=True,
            ac_branch_active_losses=np.array([1.0]),
            dc_branch_active_losses=np.array([2.0]),
            converter_active_losses=np.array([3.0]),
            total_active_losses=6.0,
            max_mismatch=0.0,
        )


class OPFSolverBasicTests(unittest.TestCase):
    def test_solver_returns_structured_result_without_decision_variables(self) -> None:
        case = SimpleNamespace(
            nodes_AC=[],
            nodes_DC=[],
            lines_AC=[],
            lines_DC=[],
            Converters_ACDC=[],
        )
        solver = OPFSolver(config=OPFConfig(), pf_solver=FakePowerFlowSolver())

        result = solver.solve(case)

        self.assertTrue(result.success)
        self.assertEqual(result.objective_value, 6.0)
        self.assertEqual(result.total_active_losses, 6.0)
        self.assertEqual(result.decision_variables, {})
        self.assertIsNotNone(result.pf_result)
        self.assertEqual(result.optimizer_status, "no_decision_variables")
        self.assertEqual(result.decision_variable_changes, [])

    def test_decision_variable_change_table_uses_physical_units(self) -> None:
        result = OPFResult(
            success=True,
            objective_value=1.0,
            decision_variables={"converter[0].Q_AC": -0.1},
            pf_result=None,
            decision_variable_changes=[
                DecisionVariableChange(
                    name="converter[0].Q_AC",
                    before_value=-0.4,
                    after_value=-0.1,
                    delta_value=0.3,
                    before_physical=-40.0,
                    after_physical=-10.0,
                    delta_physical=30.0,
                    physical_unit="MVAr",
                )
            ],
        )

        table = result.decision_variable_change_table()

        self.assertIn("-40.000 MVAr", table)
        self.assertIn("-10.000 MVAr", table)
        self.assertIn("+30.000 MVAr", table)


if __name__ == "__main__":
    unittest.main()
