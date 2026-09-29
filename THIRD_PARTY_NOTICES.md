# License and attribution review

## Release blocker: Tool1 source license evidence

The source project declares MIT in pyproject.toml, but the copied source has no root LICENSE file identifying its copyright holders and license grant. The Tool1 distribution metadata therefore records that verification is pending. No new copyright ownership or license grant is asserted here. Obtain the applicable license text and attribution from the project owners before external redistribution. Local integration artifacts are supplied for review, not marked legally cleared for publication.

## Tool5 (acdcpf)

The common backend is `acdcpf==0.2.0+tool5.1`, a local compatible derivative of `slazar394/acdcpf` commit `fe6da1577b9e6a9dc97650826e2f301967f64ba3` and `ArtemMedvedevDev/acdcpf` commit `1406db69598de099181fa13b5e112b5d9931fdc7`. Both identify ACDCPF Contributors and declare MIT. Tool5 is supplied separately and is not included in this repository. Preserve the license and provenance notices supplied with that dependency. This package has not been published or accepted as an upstream release.

## Other dependencies

NumPy, SciPy, pandas, Pyomo, IDAES, FastAPI, Uvicorn, openpyxl, and transitive dependencies retain their own licenses and authorship. The metadata/notice inventory from the validated installation is in `docs/dependency_license_inventory.json`; it is an evidence inventory, not a legal compatibility determination. IPOPT binaries downloaded by IDAES have their own distribution terms and are not included in these archives. Optional PyFlow and legacy comparison code require their own attribution review before redistribution.

## Private cases

The supplied Tool #7 files are private local inputs and are excluded from every artifact. No redistribution permission is inferred for them. The small synthetic example was created for this application. Existing benchmark provenance and references remain in the copied source.
