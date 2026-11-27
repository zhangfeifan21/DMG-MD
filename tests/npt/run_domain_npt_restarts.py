#!/usr/bin/env python3
"""Exercise new jobs across the frozen DN4 rank-count restart matrix.

Freeze once for the candidate. Produce distinct P1/P2/P4 batches, then
replay any ordered pair of different rank counts from those receipts.
SCR uses the DN4 operational fixture and original single-segment input.
Berendsen reuses the matching DN4 input. NHC and BDP use the same initial
physical snapshot and corresponding NVT ensemble commands.
"""
from __future__ import annotations

import argparse
import itertools
import json
import math
from pathlib import Path
import re
import time

import run_domain_npt_contracts as contracts
from generate_inputs import model_text, sha256_bytes

baseline = contracts.baseline
ROOT = contracts.ROOT
ENSEMBLES = ("npt_scr", "npt_ber", "nvt_nhc", "nvt_bdp")
BACKENDS = ("HostStaged", "CudaAware")
RANKS = (1, 2, 4)
STEPS = 4
SEED = "12345678"
MODEL = "model.xyz"
POTENTIAL = "nep.txt"
BASELINE = ROOT / "tests/baseline/manifest.json"
PHYSICAL_SCHEMA = (
    ("species", "S", 1), ("pos", "R", 3), ("mass", "R", 1), ("vel", "R", 3),
)
STATEFUL = ("npt_scr", "nvt_nhc", "nvt_bdp")
OUTPUTS = ("thermo.out", "trajectory.xyz", "restart.xyz")


def replace_command(template: str, command: str, replacement: str) -> str:
    lines = template.splitlines(keepends=True)
    keyword = command.split()[0]
    commands = [index for index, line in enumerate(lines) if line.split()[:1] == [keyword]]
    if len(commands) != 1 or lines[commands[0]] != command + "\n":
        raise ValueError(f"expected exactly one frozen command {command!r}")
    lines[commands[0]] = replacement + "\n"
    return "".join(lines)


def run_text(ensemble: str) -> str:
    if ensemble.startswith("npt_"):
        return contracts.run_text(ensemble, "single")
    settings = contracts.run_text("npt_ber", "single")
    return replace_command(settings, "ensemble npt_ber 300 300 40 0 425 200",
                           f"ensemble {ensemble} 300 300 40")


def inventory(directory: Path, names) -> dict:
    return {name: contracts.sha(directory / name) for name in names}


def save(path: Path, record: dict) -> None:
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(record, indent=2, allow_nan=False) + "\n")
    temp.replace(path)


def software_identity(candidate: Path) -> dict:
    files = (
        Path(__file__), Path(contracts.__file__), Path(contracts.domain.__file__),
        Path(contracts.check_environment.__file__), Path(baseline.__file__),
        Path(model_text.__code__.co_filename), BASELINE,
        ROOT / "tests/npt/domain-manifest.json",
    )
    return {
        "candidate_sha256": contracts.sha(candidate),
        "scripts_and_manifests": {
            str(path.resolve().relative_to(ROOT)): contracts.sha(path) for path in files
        },
    }


def registered_inputs() -> dict[str, bytes]:
    return {
        MODEL: model_text((20, 6, 6), 301).encode("utf-8"),
        POTENTIAL: (ROOT / "tests/baseline/inputs/potentials/nep_C.txt").read_bytes(),
        **{f"{ensemble}.in": run_text(ensemble).encode("utf-8") for ensemble in ENSEMBLES},
    }


def expected_freeze(candidate: Path, inputs: dict[str, bytes]) -> dict:
    return {
        "schema": "dmgmd.domain-npt-new-job-restarts.v1",
        **software_identity(candidate),
        "files": {name: sha256_bytes(contents) for name, contents in inputs.items()},
        "ranks": list(RANKS), "backends": list(BACKENDS), "ensembles": list(ENSEMBLES),
        "seed": SEED, "steps": STEPS, "dt_fs": 0.25,
        "tolerances": json.loads(BASELINE.read_text())["tolerances"],
    }


def freeze(candidate: Path, root: Path) -> None:
    inputs = registered_inputs()
    payload = expected_freeze(candidate, inputs)
    root.mkdir(parents=True, exist_ok=False)
    for name, contents in inputs.items():
        (root / name).write_bytes(contents)
    save(root / "freeze.json", payload)


def load_freeze(candidate: Path, root: Path) -> dict:
    frozen = json.loads((root / "freeze.json").read_text())
    expected = expected_freeze(candidate, registered_inputs())
    if frozen != expected:
        raise ValueError("freeze metadata or registered input hashes differ from the expected freeze")
    if {path.name for path in root.iterdir()} != {"freeze.json", *expected["files"]}:
        raise ValueError("file set differs from the expected restart freeze")
    if inventory(root, expected["files"]) != expected["files"]:
        raise ValueError("input bytes differ from the expected restart freeze")
    return expected


def columns(frame: dict, name: str) -> slice:
    offset = 0
    for prop, _, width in frame["properties"]:
        if prop == name:
            return slice(offset, offset + width)
        offset += width
    raise ValueError(f"missing XYZ property {name}")


def topology(frame: dict) -> list[tuple[str, float]]:
    species, mass = columns(frame, "species"), columns(frame, "mass")
    return [(row[species][0], float(row[mass][0])) for row in frame["rows"]]


def phase(frame: dict, prop: str) -> list[float]:
    if prop == "Lattice":
        return [float(token) for token in frame["fields"][prop].split()]
    values = columns(frame, prop)
    return [float(token) for row in frame["rows"] for token in row[values]]


def independent_model(path: Path, final: dict, source_model: dict, restart: dict) -> None:
    """Rebuild model.xyz from the producer's trajectory and original species/masses."""
    species_mass = topology(source_model)
    species = columns(final, "species")
    pos, vel = columns(final, "pos"), columns(final, "vel")
    if tuple(restart["properties"]) != PHYSICAL_SCHEMA or set(restart["fields"]) != {
        "pbc", "Lattice", "Properties",
    }:
        raise ValueError("restart includes unexpected state or has a missing physical field")
    if (final["fields"]["pbc"] != restart["fields"]["pbc"] or
        any(phase(final, prop) != phase(restart, prop) for prop in ("Lattice", "pos", "vel")) or
        [row[species][0] for row in final["rows"]] != [symbol for symbol, _ in species_mass] or
        topology(restart) != species_mass):
        raise ValueError("restart physical state differs from the separately written final trajectory")
    lattice = " ".join(format(value, ".17g") for value in phase(final, "Lattice"))
    header = (f'Lattice="{lattice}" pbc="{final["fields"]["pbc"]}" '
              'Properties=species:S:1:mass:R:1:vel:R:3:pos:R:3')
    with path.open("w") as model:
        model.write(f'{final["natoms"]}\n{header}\n')
        for (symbol, mass), row in zip(species_mass, final["rows"], strict=True):
            numbers = [mass, *map(float, row[vel]), *map(float, row[pos])]
            model.write(symbol + " " + " ".join(format(value, ".17g") for value in numbers) + "\n")


def validate_physical_fields(stage: Path) -> dict:
    thermo = baseline.parse_thermo(stage / "thermo.out")
    if len(thermo) != 1 or any(not math.isfinite(x) for row in thermo[0]["rows"] for x in row):
        raise ValueError("restart run must contain one segment of finite thermo")
    model, = baseline.parse_xyz(stage / MODEL)
    restart, = baseline.parse_xyz(stage / "restart.xyz")
    if (tuple(restart["properties"]) != PHYSICAL_SCHEMA or
        set(restart["fields"]) != {"pbc", "Lattice", "Properties"} or
        restart["fields"]["pbc"] != model["fields"]["pbc"] or
        topology(restart) != topology(model)):
        raise ValueError("restart species, mass, PBC, or schema changed")
    frames = baseline.parse_xyz(stage / "trajectory.xyz")
    species = [symbol for symbol, _ in topology(model)]
    for frame in frames:
        if [row[0] for row in frame["rows"]] != species:
            raise ValueError("trajectory species or atom order changed")
        if frame["fields"]["pbc"] != model["fields"]["pbc"]:
            raise ValueError("trajectory PBC changed")
        for key in ("Time", "Lattice", "energy", "virial", "stress"):
            if any(not math.isfinite(float(token)) for token in frame["fields"][key].split()):
                raise ValueError(f"non-finite trajectory field {key}")
        for name, kind, _ in frame["properties"]:
            if kind == "R" and any(not math.isfinite(value) for value in phase(frame, name)):
                raise ValueError(f"non-finite trajectory property {name}")
    if any(phase(frames[-1], prop) != phase(restart, prop) for prop in ("Lattice", "pos", "vel")):
        raise ValueError("double-precision restart differs from the last trajectory frame")
    return {"first_time_fs": float(frames[0]["fields"]["Time"]),
            "first_thermo": thermo[0]["rows"][0], "restart_exact_physical_roundtrip": True}


def validate_route(log: str, ranks: int, backend: str, mode: str,
                   npt: bool, devices: list[str]) -> dict:
    route = [line for line in log.splitlines() if line.startswith("DMGMD_DOMAIN ")]
    npt_route = [line for line in log.splitlines() if line.startswith("DMGMD_DOMAIN_NPT ")]
    if len(route) != 1:
        raise ValueError("expected one domain route")
    fields = dict(re.findall(r"(\w+)=([^\s]+)", route[0]))
    expected_mode = "m2a" if ranks > 1 else ("m1" if npt else "m1-fallback")
    if (fields["mode"] != expected_mode or int(fields["ranks"]) != ranks or
        npt_route != ([f"DMGMD_DOMAIN_NPT mode={mode}"] if npt and ranks > 1 else [])):
        raise ValueError(f"unexpected route: {route + npt_route}")
    return {"domain": route + npt_route,
            "bindings": contracts.verify_binding(log, devices[:ranks], backend)}


def launch(args, stage: Path, model: Path, ensemble: str, ranks: int,
           backend: str, mode: str, steps: int = STEPS) -> dict:
    stage.parent.mkdir(parents=True, exist_ok=True)
    if stage.exists():
        raise FileExistsError(f"choose a new stage path: {stage}")
    run = (args.freeze / f"{ensemble}.in").read_text()
    if steps != STEPS:
        run = replace_command(run, f"run {STEPS}", f"run {steps}")
    # The explicit off value excludes the regression-only production fallback.
    extra_env = {"DMGMD_FORCE_M1_REPLICATED": "0", "DMGMD_COMM_LOG_INTERVAL": "1"}
    records = []
    result = {"case": ensemble}
    started = time.monotonic()
    try:
        contracts.execute(stage, args.candidate, args.mpiexec, ranks, backend, mode, args.uuids,
                          args.timeout, model, args.freeze / POTENTIAL, run, records,
                          extra_env=extra_env)
        log = "\n".join((stage / file).read_text() for file in ("stdout.log", "stderr.log"))
        result["route"] = validate_route(log, ranks, backend, mode, ensemble.startswith("npt_"), args.uuids)
        result.update(outputs={**records[0]["outputs"], **validate_physical_fields(stage)},
                      hashes=inventory(stage, OUTPUTS), status="PASS")
    except Exception as error:
        result.update(status="FAIL", error=str(error))
        raise
    finally:
        if stage.is_dir():
            job = records[0] if records else {}
            result = {**job, **result, "elapsed_seconds": time.monotonic() - started}
            result["inputs"] = job.get("input_sha256", {})
            save(stage / "result.json", result)
    return result


def producer_mode(ensemble: str, ranks: int) -> str:
    return "rebuild" if ensemble.startswith("npt_") and ranks > 1 else "off"


def reset_control(args, stage, fresh, ensemble, rank, backend, continued):
    launch(args, continued, args.freeze / MODEL, ensemble, rank, backend,
           producer_mode(ensemble, rank), steps=STEPS + 1)
    original_outputs = baseline.parse_xyz(stage / "trajectory.xyz")
    uninterrupted = baseline.parse_xyz(continued / "trajectory.xyz")
    if original_outputs != uninterrupted[:STEPS]:
        raise ValueError("uninterrupted control did not reproduce the producer through the checkpoint")
    fresh_first = baseline.parse_xyz(fresh / "trajectory.xyz")[0]
    tolerances = args.frozen["tolerances"]
    comparisons = {}
    required = ("vel", "Lattice") if ensemble == "npt_scr" else ("vel",)
    for prop, category in (("vel", "velocity"), ("Lattice", "lattice")):
        tolerance = tolerances[category]
        stats = {"tolerance": tolerance, "max_abs": 0.0, "comparisons": 0,
                 "above_tolerance_count": 0, "first_above_tolerance": None}
        for index, (reference, actual) in enumerate(zip(
                phase(uninterrupted[-1], prop), phase(fresh_first, prop), strict=True)):
            absolute = abs(actual - reference)
            allowed = tolerance["atol"] + tolerance["rtol"] * abs(reference)
            stats["comparisons"] += 1
            stats["max_abs"] = max(stats["max_abs"], absolute)
            if absolute > allowed:
                stats["above_tolerance_count"] += 1
                if stats["first_above_tolerance"] is None:
                    stats["first_above_tolerance"] = {
                        "index": index, "uninterrupted_frame5": reference, "fresh_first_frame": actual,
                        "abs_diff": absolute, "allowed_abs_diff": allowed,
                    }
        comparisons[prop] = stats
    indistinguishable = [prop for prop in required if comparisons[prop]["above_tolerance_count"] == 0]
    result = {"matched_uninterrupted_prefix_frames": STEPS, "required_fields": required,
              "max_abs_reset_vs_uninterrupted_frame5": {
                  prop: stats["max_abs"] for prop, stats in comparisons.items()
              },
              "comparisons": comparisons, "indistinguishable_fields": indistinguishable}
    save(continued / "reset_comparison.json", result)
    if indistinguishable:
        raise ValueError(f"new job matches uninterrupted frame 5 within tolerance for {indistinguishable}")
    return result


def source_path(args, rank, backend, ensemble):
    return args.work_root / "sources" / f"p{rank}-{backend}-{ensemble}"


def produce(args) -> None:
    model, = baseline.parse_xyz(args.freeze / MODEL)
    for ranks, backend, ensemble in itertools.product(args.ranks, args.backends, args.ensembles):
        root = source_path(args, ranks, backend, ensemble)
        source, fresh = root / "producer", root / "fresh-p1"
        producer = launch(args, source, args.freeze / MODEL, ensemble, ranks, backend,
                          producer_mode(ensemble, ranks))
        restart, = baseline.parse_xyz(source / "restart.xyz")
        final = baseline.parse_xyz(source / "trajectory.xyz")[-1]
        reference_model = root / "independent-model.xyz"
        independent_model(reference_model, final, model, restart)
        reference = launch(args, fresh, reference_model, ensemble, 1, "HostStaged", "off")
        record = {"status": "PASS", "freeze_sha256": args.freeze_hash, "producer": producer,
                  "fresh_p1": reference, "independent_model_sha256": contracts.sha(reference_model)}
        # Same rank and seed for the diagnostic control isolate controller
        # reinitialization from decomposition-dependent rounding.
        if ensemble in STATEFUL and ranks == 1 and backend == "HostStaged":
            record["reset_control"] = reset_control(args, source, fresh, ensemble, ranks,
                                                     backend, root / "uninterrupted")
        save(root / "receipt.json", record)
        print(f"PASS source p{ranks} {backend} {ensemble}", flush=True)


def replay(args) -> None:
    for rank, backend, ensemble, source_rank in itertools.product(
            args.ranks, args.backends, args.ensembles, args.source_ranks):
        if source_rank == rank:
            continue
        root = source_path(args, source_rank, backend, ensemble)
        receipt = json.loads((root / "receipt.json").read_text())
        if receipt["status"] != "PASS" or receipt["freeze_sha256"] != args.freeze_hash:
            raise ValueError(f"invalid source receipt: {root}")
        model = root / "producer/restart.xyz"
        fresh = root / "fresh-p1"
        if (contracts.sha(model) != receipt["producer"]["hashes"]["restart.xyz"] or
            inventory(fresh, OUTPUTS) != receipt["fresh_p1"]["hashes"]):
            raise ValueError(f"source restart or independent reference has changed: {root}")
        modes = ("rebuild", "affine") if ensemble.startswith("npt_") and rank > 1 else ("off",)
        for mode in modes:
            stage = args.work_root / "targets" / f"p{source_rank}-to-p{rank}-{backend}-{ensemble}-{mode}"
            resumed = launch(args, stage, model, ensemble, rank, backend, mode)
            try:
                if resumed["inputs"]["run.in"] != receipt["fresh_p1"]["inputs"]["run.in"]:
                    raise ValueError("fresh P1 and resumed run use different parameters")
                resumed["comparison"] = contracts.compare(fresh, stage, args.frozen["tolerances"])
            except Exception as error:
                resumed.update(status="FAIL", error=str(error))
                raise
            finally:
                resumed.update(freeze_sha256=args.freeze_hash, source=str(root),
                               reference=str(fresh), source_restart_sha256=contracts.sha(model))
                save(stage / "result.json", resumed)
            print(f"PASS {stage.name}", flush=True)


def unique_list(raw, valid):
    values = raw.split(",")
    if not set(values).issubset(valid) or len(values) != len(set(values)):
        raise argparse.ArgumentTypeError(f"expected distinct values from {valid}")
    return values


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("freeze", "produce", "replay"))
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--freeze", type=Path, required=True)
    parser.add_argument("--work-root", type=Path)
    parser.add_argument("--devices", help="comma-separated NVIDIA GPU indices or UUIDs")
    parser.add_argument("--ranks", default="1")
    parser.add_argument("--source-ranks", default="1,2,4")
    parser.add_argument("--backends", default="HostStaged,CudaAware")
    parser.add_argument("--ensembles", default=",".join(ENSEMBLES))
    parser.add_argument("--timeout", type=int, default=120)
    args = parser.parse_args()
    args.candidate, args.freeze = args.candidate.resolve(), args.freeze.resolve()
    if args.action == "freeze":
        freeze(args.candidate, args.freeze)
        return
    if not args.work_root or not args.devices:
        parser.error("running jobs requires --work-root and --devices")
    try:
        args.ranks = [int(x) for x in unique_list(args.ranks, tuple(map(str, RANKS)))]
        args.source_ranks = [int(x) for x in unique_list(args.source_ranks, tuple(map(str, RANKS)))]
        args.backends = unique_list(args.backends, BACKENDS)
        args.ensembles = unique_list(args.ensembles, ENSEMBLES)
        devices = args.devices.split(",")
        if len(set(devices)) != len(devices) or len(devices) < max(args.ranks):
            raise argparse.ArgumentTypeError("provide enough distinct physical NVIDIA GPUs")
    except argparse.ArgumentTypeError as error:
        parser.error(str(error))
    args.work_root = args.work_root.resolve()
    args.frozen = load_freeze(args.candidate, args.freeze)
    args.freeze_hash = contracts.sha(args.freeze / "freeze.json")
    args.uuids = contracts.physical_gpu_uuids(devices[:max(args.ranks)])
    args.mpiexec = contracts.check_environment.validate_environment(
        args.candidate, "mpiexec", args.uuids, max(args.ranks), args.timeout)
    if args.action == "produce":
        produce(args)
    else:
        replay(args)


if __name__ == "__main__":
    main()
