# License and attribution review

## Tool1 license and attribution

Tool1 is distributed under the MIT License in the root LICENSE file, with the existing HYNET OPF Contributors attribution. On 29 September 2026, the project maintainer confirmed agreement with the original Tool1 author to release it under MIT. This resolves the previously recorded missing Tool1 license-grant confirmation. See docs/PROVENANCE.md for the original source lineage.

## Development status

Tool1 is a functional working version under active development. Features, interfaces and documentation may change as refinement and validation continue. Review solver diagnostics and validate results for the intended application. This notice does not add restrictions to the MIT License. Dependency licenses and third-party attribution continue to apply.

## Tool5 (acdcpf)

The common backend is `acdcpf==0.2.0+tool5.1`, a local compatible derivative of `slazar394/acdcpf` commit `fe6da1577b9e6a9dc97650826e2f301967f64ba3` and `ArtemMedvedevDev/acdcpf` commit `1406db69598de099181fa13b5e112b5d9931fdc7`. Both identify ACDCPF Contributors and declare MIT. Tool5 is supplied separately and is not included in this repository. Preserve the license and provenance notices supplied with that dependency. This package has not been published or accepted as an upstream release.

## Other dependencies

NumPy, SciPy, pandas, Pyomo, IDAES, FastAPI, Uvicorn, openpyxl, and transitive dependencies retain their own licenses and authorship. The metadata/notice inventory from the validated installation is in `docs/dependency_license_inventory.json`; it is an evidence inventory, not a legal compatibility determination. IPOPT binaries downloaded by IDAES have their own distribution terms and are not included in these archives. Optional PyFlow and legacy comparison code require their own attribution review before redistribution.

## Private cases

The supplied Tool #7 files are private local inputs and are excluded from every artifact. No redistribution permission is inferred for them. The small synthetic example was created for this application. Existing benchmark provenance and references remain in the copied source.
