from __future__ import annotations

from types import SimpleNamespace
import unittest

from acdcpf_opf.opf.config import OPFConfig
from acdcpf_opf.opf.variables import build_decision_variables


class OPFVariableTests(unittest.TestCase):
    def test_decision_variable_pack_unpack_roundtrip(self) -> None:
        generator = SimpleNamespace(
            genNumber=4,
            PGen=0.5,
            Min_pow_gen=0.1,
            Max_pow_gen=1.0,
        )
        case = SimpleNamespace(
            nodes_AC=[SimpleNamespace(connected_gen=[generator])],
            nodes_DC=[],
            Converters_ACDC=[],
        )
        config = OPFConfig(optimize_ac_generator_active_power=True)
        variables = build_decision_variables(case, config)

        vector = variables.pack(case)
        unpacked = variables.unpack(vector)
        variables.apply(case, [0.75])

        self.assertEqual(len(variables), 1)
        self.assertEqual(unpacked["ac_gen[4].PGen"], 0.5)
        self.assertEqual(generator.PGen, 0.75)

    def test_slack_generator_active_power_is_not_a_decision_variable(self) -> None:
        generator = SimpleNamespace(
            genNumber=0,
            PGen=0.0,
            Min_pow_gen=0.0,
            Max_pow_gen=2.0,
        )
        case = SimpleNamespace(
            nodes_AC=[SimpleNamespace(type="Slack", connected_gen=[generator])],
            nodes_DC=[],
            Converters_ACDC=[],
        )
        config = OPFConfig(optimize_ac_generator_active_power=True)

        variables = build_decision_variables(case, config)

        self.assertEqual(len(variables), 0)

    def test_selected_generator_active_power_indices_are_respected(self) -> None:
        gen_1 = SimpleNamespace(
            genNumber=1,
            PGen=0.4,
            Min_pow_gen=0.1,
            Max_pow_gen=1.0,
        )
        gen_2 = SimpleNamespace(
            genNumber=2,
            PGen=0.5,
            Min_pow_gen=0.1,
            Max_pow_gen=1.0,
        )
        case = SimpleNamespace(
            nodes_AC=[
                SimpleNamespace(type="PV", connected_gen=[gen_1]),
                SimpleNamespace(type="PV", connected_gen=[gen_2]),
            ],
            nodes_DC=[],
            Converters_ACDC=[],
        )
        config = OPFConfig(
            optimize_ac_generator_active_power=True,
            ac_generator_active_power_indices=(2,),
        )

        variables = build_decision_variables(case, config)

        self.assertEqual([var.name for var in variables.variables], ["ac_gen[2].PGen"])

    def test_empty_selection_disables_an_enabled_variable_group(self) -> None:
        gen = SimpleNamespace(
            genNumber=1,
            PGen=0.4,
            Min_pow_gen=0.1,
            Max_pow_gen=1.0,
        )
        case = SimpleNamespace(
            nodes_AC=[SimpleNamespace(type="PV", connected_gen=[gen])],
            nodes_DC=[],
            Converters_ACDC=[],
        )
        config = OPFConfig(
            optimize_ac_generator_active_power=True,
            ac_generator_active_power_indices=(),
        )

        variables = build_decision_variables(case, config)

        self.assertEqual(len(variables), 0)

    def test_voltage_controlled_converter_q_is_not_a_decision_variable(self) -> None:
        case = SimpleNamespace(
            nodes_AC=[],
            nodes_DC=[],
            S_base=100.0,
            Converters_ACDC=[
                SimpleNamespace(
                    ConvNumber=1,
                    AC_type="PV",
                    type="Slack",
                    Q_AC=0.0,
                    MVA_max=100.0,
                )
            ],
        )
        config = OPFConfig(optimize_converter_reactive_power=True)

        variables = build_decision_variables(case, config)

        self.assertEqual(len(variables), 0)

    def test_selected_converter_indices_are_respected(self) -> None:
        case = SimpleNamespace(
            nodes_AC=[],
            nodes_DC=[],
            S_base=100.0,
            Converters_ACDC=[
                SimpleNamespace(
                    ConvNumber=0,
                    type="PAC",
                    AC_type="PQ",
                    P_AC=-0.6,
                    MVA_max=100.0,
                ),
                SimpleNamespace(
                    ConvNumber=2,
                    type="PAC",
                    AC_type="PQ",
                    P_AC=0.35,
                    MVA_max=100.0,
                ),
            ],
        )
        config = OPFConfig(
            optimize_converter_active_power=True,
            converter_active_power_indices=(2,),
        )

        variables = build_decision_variables(case, config)

        self.assertEqual([var.name for var in variables.variables], ["converter[2].P_AC"])


if __name__ == "__main__":
    unittest.main()
