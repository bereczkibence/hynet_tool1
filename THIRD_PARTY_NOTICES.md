# License and attribution review

## Tool1 license and attribution

Tool1 is distributed under the MIT License in the root LICENSE file, with the existing HYNET OPF Contributors attribution. On 29 September 2026, the project maintainer confirmed agreement with the original Tool1 author to release it under MIT. This resolves the previously recorded missing Tool1 license-grant confirmation. See docs/PROVENANCE.md for the original source lineage.

## Development status

Tool1 is a functional working version under active development. Features, interfaces and documentation may change as refinement and validation continue. Review solver diagnostics and validate results for the intended application. This notice does not add restrictions to the MIT License. Dependency licenses and third-party attribution continue to apply.

## Tool5 (acdcpf)

Tool5 is supplied separately from https://github.com/slazar394/acdcpf and is not bundled. The public solver is loaded unchanged. The earlier local common backend remains an optional compatibility path, not a prerequisite.

Tool1's network validation and transformer/storage table creators in `acdcpf_pyflow_backend/network_validation.py` and `network_factory.py` are adapted from the MIT-licensed `ArtemMedvedevDev/acdcpf` baseline (commit `1406db69598de099181fa13b5e112b5d9931fdc7`). Their license notice is retained below. These are application data/validation helpers, not a vendored PF solver.

```text
MIT License

Copyright (c) 2025 ACDCPF Contributors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## Other dependencies

NumPy, SciPy, pandas, Pyomo, IDAES, FastAPI, Uvicorn, openpyxl, and transitive dependencies retain their own licenses and authorship. The metadata/notice inventory from the validated installation is in `docs/dependency_license_inventory.json`; it is an evidence inventory, not a legal compatibility determination. IPOPT binaries downloaded by IDAES have their own distribution terms and are not included in these archives. Optional PyFlow and legacy comparison code require their own attribution review before redistribution.

## Private cases

The supplied Tool #7 files are private local inputs and are excluded from every artifact. No redistribution permission is inferred for them. The small synthetic example was created for this application. Existing benchmark provenance and references remain in the copied source.
