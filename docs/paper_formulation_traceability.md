# Paper Formulation Traceability

This note maps the nonlinear AC/DC OPF equations from Ergun et al. (2019),
*Optimal Power Flow for AC-DC Grids: Formulation, Convex Relaxation, Linear
Approximation, and Implementation*, to the current Pyomo/IPOPT formulation.

The current solver targets the paper's exact nonlinear VSC AC/DC model. It does
not target the paper's convex relaxations, linear approximations, or LCC
firing-angle model.

## Implemented Core NLP Groups

| Paper equations | Model group | Current status |
|---|---|---|
| (1) | AC bus voltage limits | Implemented |
| (2) | DC bus voltage limits | Implemented |
| (3)-(8) | DC branch flow, loss, current, and branch ratings | Implemented with explicit voltage-current branch-flow equations |
| Standard AC OPF | AC branch nonlinear P/Q flows and branch ratings | Implemented |
| (9)-(16) | Converter transformer lossy/lossless equations | Implemented |
| (17)-(19) | Converter filter and filter-node P/Q balance | Implemented |
| (9)-(16), reused | Phase reactor lossy/lossless equations | Implemented |
| (20)-(23) | Converter AC P/Q and DC active-power bounds | Implemented |
| (24) | Converter AC/DC active-power balance | Implemented |
| (25) | Converter loss model, `P_loss = a + b I + c I^2` | Implemented |
| (26)-(30) | Converter AC/DC current relations and current limits | Implemented |
| (33)-(35) | DC and AC nodal balance equations | Implemented |

## Current Crucial Modeling Detail

The paper notes that the quadratic loss coefficient can differ between
rectifying and inverting operation. A hard switch on a decision variable would
make the IPOPT NLP nonsmooth. The current implementation therefore supports two
loss modes:

| Mode | Behavior |
|---|---|
| `fixed` | Uses the legacy pre-selected `loss_c` coefficient. |
| `smooth_directional` | Uses a smooth logistic transition between `loss_c_positive_p_ac` and `loss_c_negative_p_ac` based on the solved sign of `Pcv_ac`. |

For the canonical OPF sign convention, `Pcv_ac > 0` means the electronic
converter absorbs active power from the AC terminal. The data adapters define
which physical coefficient corresponds to positive or negative `Pcv_ac`, because
PyFlow and the native ACDCPF package use different local converter sign
conventions internally.

## Intentionally Out Of Scope

| Paper equations/features | Reason |
|---|---|
| (31)-(32), LCC firing-angle equations | The current benchmark scope is VSC/MMC only. |
| SOC/SDP/QC convex relaxations | The current solver is an exact nonlinear Pyomo/IPOPT formulation. |
| Linear approximation | Not part of the current loss-minimization NLP solver. |
| Security-constrained OPF | Not in the first benchmark scope. |

## Non-Paper Feature Still Missing

DC/DC converter equations are present in PyFlow/MatACDC-style tooling, but they
are not part of the core VSC converter-station formulation above. The current
adapters intentionally reject networks with active DC/DC converters until those
equations are added.
