# LH2 Process Simulator — public synthetic edition

Reusable liquid-hydrogen physics models and a Korean browser-based component
editor, with graph validation, units, result trends, CSV export and design saving.
All initial geometry, equipment and operating examples are invented.

## Run from source

Python 3.12+ recommended:

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python launch_simulator.py
```

Open http://127.0.0.1:8765. A headless local server can be started with
`python launch_simulator.py --console`. Stop it with Ctrl+C.
Use `--self-check` to inspect dependencies and resources without starting a server.

## Public examples

The default flowsheet is a synthetic three-node source/tank/outlet example.
Three runs demonstrate closed storage, imposed liquid inflow and imposed homogeneous
outflow using the homogeneous tank's mass/energy balance. These are prescribed
boundary-flow examples, not a solved hydraulic network or safety valve sizing.
Edit tank dimensions/initial conditions and process duration before Run. Graph
edits are validated; arbitrary connected networks are not automatically solved.
The separate edited-design preview runs each authored homogeneous tank without
flow. Other reusable pipe, pump, valve, vaporizer and advanced physics APIs remain
available in src/lh2dt and through the component palette.

The original facility topology, equipment tags, drawings, facility-derived
dimensions, private scenario runners, raw records, internal reports, old Git
history and private executables are excluded. This public demo does not claim
the original facility's eight operating scenarios or validation results.

## Windows build

```powershell
python -m pip install -r requirements-build.txt
python -m PyInstaller --noconfirm LH2ProcessSimulator.spec
```

Use the entire dist/LH2ProcessSimulator folder. This release distributes source
only; no prebuilt Windows executable is published. The local frozen build
was not qualified because Windows application-control policy blocked a SciPy
native module. Running the Python source was tested independently. Source and binary distributions
must retain LICENSE, THIRD_PARTY_NOTICES.md and third_party_licenses. The public
executable is rebuilt from these generic sources, never copied from the private
release. It is unsigned and starts only a loopback service.

## Rights and use

Source-visible; all rights reserved (LICENSE). No open-source reuse license is
granted by publication. Third-party components retain their own licenses.
Research/education only; not a facility controller or certified safety analysis.
