# Stagg5 Time Profiles

This folder contains small long-format profile files for snapshot PF/OPF runs.
Each `time_index` is solved independently; these files do not introduce
multi-period coupling or storage SOC dynamics.

## Files

- `original_stagg5_load_profile.csv`: three simple smoke-test snapshots for
  the original Stagg5 MTDC benchmark, scaling AC loads 0-3 to 70%, 85%, and
  100%.
- `hybrid_dcdc_load_pv_profile.csv`: three snapshots for the hybrid DCDC case,
  scaling AC loads 0-3 and setting the DC PV source to 10, 20, and 30 MW.
- `stagg5_one_day_ac_load_profile.csv`: 24 hourly snapshots for one day,
  applying representative SimBench-style normalized AC load multipliers to AC
  loads 0-3.
- `hybrid_dcdc_one_day_load_pv_profile.csv`: 24 hourly snapshots for the
  hybrid DCDC case, applying the same AC load multipliers and setting a daytime
  DC PV generation curve on `PV DC Source`.

## Notes On The One-Day Profile

The one-day profile is a practical demonstration input for Tool1 (acdcopf) time-series
testing. It follows the shape expected from distribution/transmission planning
load curves: low overnight demand, a morning ramp, daytime plateau, and an
evening peak. It is not a published Stagg5 reference profile and it is not an
exact extract from a SimBench dataset.

For published benchmark comparisons, replace this file with a profile extracted
from the official SimBench `LoadProfile` data for the relevant scenario.

Useful references:

- SimBench dataset downloads: https://simbench.de/en/download/datasets/
- SimBench profile documentation: https://simbench.readthedocs.io/en/stable/profiles.html
