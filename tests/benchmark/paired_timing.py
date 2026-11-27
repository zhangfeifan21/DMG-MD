"""Freeze and execute randomized material blocks for the single-node paired timing protocol."""

import argparse
import hashlib
import json
import math
import random
import shutil
import shlex
import statistics
from pathlib import Path

from run_benchmark import save, sha

ORDER_SEED = 20260922
PHASE_STEPS = {"pilot": 10_000, "warmup": 1_000, "sample": 100_000}


def pilot_decisions(cells, records):
    observations = {name: {} for name in cells}
    for record in records or []:
        cell, block, seconds = record["cell"], record["block"], record["wall_s"]
        if (cell not in cells or record["phase"] != "pilot" or record["valid"] is not True
                or type(block) is not int or block in observations[cell]
                or not isinstance(seconds, (int, float)) or isinstance(seconds, bool)
                or not math.isfinite(seconds) or seconds <= 0):
            raise ValueError(f"invalid or duplicate pilot record: {cell}, block {block}")
        observations[cell][block] = seconds
    decisions = {}
    for name, values in observations.items():
        if set(values) != set(range(1, 6)):
            raise ValueError(f"{name}: expected five complete 10000-step pilots")
        seconds = [values[i] for i in range(1, 6)]
        cv = statistics.stdev(seconds) / statistics.mean(seconds)
        decisions[name] = {"wall_s_by_block": seconds, "sample_cv": cv,
                           "repeats": 5 if cv > 0.02 else 3}
    return decisions


def validate_cells(config, cells, phases):
    if type(config.get("cpu_budget")) is not int or config["cpu_budget"] < 1:
        raise ValueError("cpu_budget must be a positive number of physical cores")
    for name, cell in cells.items():
        engine, devices = cell["engine"], cell["devices"]
        if engine not in ("dmgmd", "gpumd", "wpe", "lammps"):
            raise ValueError(f"{name}: unknown engine")
        supplement = cell.get("placement") == "cross-numa-scaling"
        if supplement and engine != "dmgmd":
            raise ValueError("cross-NUMA placement is restricted to DMG-MD")
        allowed = [[0, 2]] if supplement else [[0], [0, 1], [0, 1, 2, 3]]
        if devices not in allowed or (engine == "wpe" and devices != [0]):
            raise ValueError(f"{name}: unsupported GPU placement for {engine}: {devices}")
        if (not cell["material"] or not isinstance(cell.get("physics"), dict)
                or not all(cell["physics"].get(k) for k in ("model_sha256", "potential_sha256", "ensemble"))):
            raise ValueError(f"{name}: material and frozen physical parameters are required")
        for phase in phases:
            spec = cell["variants"][phase]
            steps = spec["steps"]
            if spec["ranks"] != len(devices) or spec.get("steady_start_step", 1000) != 1000:
                raise ValueError(f"{name}/{phase}: rank/device count or steady start differs from protocol")
            if (type(steps) is not int or spec["engine"] != engine or steps < 1
                    or phase in PHASE_STEPS and steps != PHASE_STEPS[phase]):
                raise ValueError(f"{name}/{phase}: engine or trajectory length disagrees with protocol")
            stride = spec["thermo_interval"]
            if (type(stride) is not int or stride <= 0 or 1000 % stride or steps % stride
                    or phase != "warmup" and steps <= 1000):
                raise ValueError(f"{name}/{phase}: thermo must cover steps 1000 and {steps}")
        # The pilot, warmup and timed run must describe the same engine and physical inputs.
        invariant = None
        for phase in (("pilot", "warmup", "sample") if "sample" in phases else phases):
            spec = cell["variants"][phase]
            inputs = spec["inputs"]
            physical = cell["physics"]
            if cell["engine"] == "lammps":
                equivalence = cell["input_equivalence"]
                if not equivalence["evidence"] or equivalence["source_model_sha256"] != physical["model_sha256"] or (
                        equivalence["source_potential_sha256"] != physical["potential_sha256"]):
                    raise ValueError(f"{name}: LAMMPS requires the frozen physical equivalence reference")
                identities = equivalence["inputs"]
                if any(inputs.get(k) != v for k, v in identities.items()) or len(identities) < 2:
                    raise ValueError(f"{name}: converted snapshot or potential differs from equivalence evidence")
            elif (inputs.get("model.xyz") != physical["model_sha256"]
                  or physical["potential_sha256"] not in inputs.values()):
                raise ValueError(f"{name}/{phase}: snapshot or potential differs from physical task")
            current = (spec["binaries"], spec.get("env", {}), spec.get("engine_binary"),
                       spec.get("expected_backend"), spec.get("expected_domain_mode"),
                       spec.get("expected_domain_npt_mode"), spec["ranks"], spec["atoms"],
                       spec["thermo_interval"])
            if invariant is not None and invariant != current:
                raise ValueError(f"{name}: pilot/warmup/sample engine, runtime or output cadence differs")
            invariant = current
    ids = set()
    for comparison in config["comparisons"]:
        left, right = (cells[comparison[key]] for key in ("numerator", "denominator"))
        if comparison["id"] in ids or left is right:
            raise ValueError("comparison IDs must be unique and the two cells distinct")
        ids.add(comparison["id"])
        if left["material"] != right["material"] or left["physics"] != right["physics"]:
            raise ValueError(f'{comparison["id"]}: physics or material mismatch')
        engines = {left["engine"], right["engine"]}
        if "gpumd" in engines or "lammps" in engines:
            if right["engine"] != "dmgmd" or left["devices"] != right["devices"]:
                raise ValueError("GPUMD and LAMMPS external comparisons require same-card DMG-MD")
        if "wpe" in engines and (left["engine"], right["engine"]) != ("wpe", "dmgmd"):
            raise ValueError("WPE single-card and migration comparisons require DMG-MD")
        if any(c.get("placement") == "cross-numa-scaling" for c in (left, right)):
            if {left["engine"], right["engine"]} != {"dmgmd"}:
                raise ValueError("cross-NUMA supplement is restricted to DMG-MD strong scaling")
        for phase in phases:
            if any(left["variants"][phase][key] != right["variants"][phase][key]
                   for key in ("steps", "thermo_interval", "atoms")):
                raise ValueError(f'{comparison["id"]}: step count, output interval or atom count mismatch')


def make_plan(config, phase, pilots=None):
    cells = {cell["id"]: cell for cell in config["cells"]}
    if not cells or len(cells) != len(config["cells"]):
        raise ValueError("provide distinct, nonempty cell IDs")
    if phase not in ("pilot", "sample", "smoke"):
        raise ValueError("unknown plan phase")
    phases = ("warmup", "sample") if phase == "sample" else (phase,)
    validate_cells(config, cells, phases)
    decisions = pilot_decisions(cells, pilots) if phase == "sample" else {}
    counts = {name: (decisions[name]["repeats"] if decisions else (5 if phase == "pilot" else 3))
              for name in cells}
    comparisons = [dict(c, blocks=list(range(1, 1 + min(counts[c["numerator"]], counts[c["denominator"]]))))
                   for c in config["comparisons"]]
    rng = random.Random(ORDER_SEED)
    jobs = []
    for block in range(1, max(counts.values()) + 1):
        groups = {}
        for name in sorted(cells):
            if counts[name] >= block:
                groups.setdefault(cells[name]["material"], []).append(name)
        materials = sorted(groups)
        rng.shuffle(materials)
        for material in materials:
            rng.shuffle(groups[material])
        for job_phase in phases:
            for material in materials:
                for name in groups[material]:
                    jobs.append({"block": block, "material": material,
                                 "cell": name, "phase": job_phase})
    return {"phase": phase, "order_seed": ORDER_SEED, "jobs": jobs,
            "decisions": decisions, "comparisons": comparisons}


def digest(data):
    encoded = json.dumps(data, sort_keys=True, ensure_ascii=True, allow_nan=False,
                         separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text())


def read_plan(path):
    bundle = read_json(path)
    if bundle["sha256"] != digest({k: v for k, v in bundle.items() if k != "sha256"}):
        raise ValueError(f"frozen plan fingerprint mismatch: {path}")
    return bundle



def verify_input_decks(config, phase):
    """Read the registered input scripts before freezing the order and measured workload."""
    phases = ("pilot", "warmup", "sample") if phase == "sample" else (phase,)
    native = {}
    for cell in config["cells"]:
        recipes = []
        for part in phases:
            spec = cell["variants"][part]
            inputs = spec["inputs"]
            if cell["engine"] == "lammps":
                argv = spec["command"]
                scripts = [argv[i+1] for i, arg in enumerate(argv[:-1]) if arg in ("-i", "-in", "--input")]
                script = scripts[0] if scripts else "in.lmp"
            else:
                script = "run.in"
            path = Path(spec["input_dir"]) / script
            if inputs.get(script) != sha(path):
                raise ValueError(f"{cell['id']}/{part}: input script differs from frozen identity")
            lines = [shlex.split(line, comments=True) for line in path.read_text().splitlines()]
            commands = [line for line in lines if line]
            if cell["engine"] == "lammps":
                # Equivalence of the conversion is established by the material/engine pilot.
                used = [line[1] for line in commands if line[0] == "read_data"]
                used += [line[2] for line in commands if line[:2] == ["pair_style", "matpl/nep/kk"]]
                if set(used) != set(cell["input_equivalence"]["inputs"]) or len(used) != 2:
                    raise ValueError(f"{cell['id']}: equivalence reference is not for the files in use")
                ignored = {"run", "thermo", "thermo_style", "thermo_modify", "write_dump", "log"}
            else:
                potentials = [inputs.get(line[1]) for line in commands if line[0] == "potential"]
                ensembles = [line[1] for line in commands if line[0] == "ensemble"]
                if potentials != [cell["physics"]["potential_sha256"]] or ensembles != [cell["physics"]["ensemble"]]:
                    raise ValueError(f"{cell['id']}: potential or ensemble differs from physical task")
                ignored = {"run", "dump_restart", "dump_thermo", "potential"}
            recipes.append([line for line in commands if line[0] not in ignored])
        if any(recipe != recipes[0] for recipe in recipes):
            raise ValueError(f"{cell['id']}: physical commands differ between pilot, warmup and sample")
        if cell["engine"] != "lammps":
            native[cell["id"]] = recipes[0]
    for pair in config["comparisons"]:
        left, right = pair["numerator"], pair["denominator"]
        if left in native and right in native and native[left] != native[right]:
            raise ValueError(f"{pair['id']}: the two input decks specify different physical commands")

def freeze(config_path, phase, output, pilot_dir=None):
    config = read_json(config_path)
    pilots = None
    source = None
    if phase == "sample":
        if pilot_dir is None:
            raise ValueError("sample plan requires a complete pilot run")
        pilot_dir = Path(pilot_dir)
        previous = read_plan(pilot_dir / "plan.json")
        if previous["config"] != config or previous["plan"]["phase"] != "pilot":
            raise ValueError("pilot run must use the same frozen configuration")
        pilots = load_records(pilot_dir, previous)
        source = {"plan_sha256": previous["sha256"], "records_sha256": digest(pilots)}
    elif pilot_dir is not None:
        raise ValueError("only a sample plan takes pilot evidence")
    planned = make_plan(config, phase, pilots)
    verify_input_decks(config, phase)
    bundle = {"schema": 1, "config": config, "plan": planned,
              "pilot_source": source}
    bundle["sha256"] = digest(bundle)
    with Path(output).open("x") as stream:
        json.dump(bundle, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def job_directory(root, index):
    return root / f"job-{index:04d}"


def load_records(output, bundle):
    records = []
    cells = {c["id"]: c for c in bundle["config"]["cells"]}
    for index, job in enumerate(bundle["plan"]["jobs"], 1):
        directory = job_directory(output, index)
        # A stopped run cannot be replayed as a complete set of pairs.
        audit = read_json(directory / "audit.json")
        if audit["job"] != job or audit["plan_sha256"] != bundle["sha256"]:
            raise ValueError(f"{directory}: job does not match frozen order")
        measurement = read_json(directory / "work/paired-job.json") if audit["launched"] else {}
        if audit["launched"] and digest(measurement) != audit.get("measurement_sha256"):
            raise ValueError(f"{directory}: measurement fingerprint mismatch or unfinished audit")
        cell = cells[job["cell"]]
        reasons = list(audit["invalid_reasons"]) + measurement.get("invalid_reasons", [])
        records.append({**job, "engine": cell["engine"], "gpus": len(cell["devices"]),
                        "physics": cell["physics"], "devices": cell["devices"],
                        "wall_s": measurement.get("wall_s"), "tail_s": measurement.get("tail_s"),
                        "tail_bounds_s": measurement.get("tail_bounds_s"),
                        "steady_ms_step": measurement.get("steady_ms_step"),
                        "valid": measurement.get("valid") is True and not reasons,
                        "invalid_reasons": reasons, "raw_directory": directory.name})
    return records


def replay(output):
    from paired_statistics import summarize
    bundle = read_plan(output / "plan.json")
    records = load_records(output, bundle)
    plan = bundle["plan"]
    return summarize(records, plan["comparisons"], expected_records=plan["jobs"], phase=plan["phase"])


def stage_inputs(spec, work):
    source = Path(spec["input_dir"])
    if not source.is_absolute():
        raise ValueError("input_dir must be absolute so a frozen plan can be replayed")
    for name in spec["inputs"]:
        path = Path(name)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError(f"input must be inside the job directory: {name}")
        target = work / path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / path, target)


def execute_plan(frozen, output, reservation):
    from paired_measurement import measure_job
    from paired_resources import ResourceError, exclusive_node, validate_observations

    bundle = read_plan(frozen)
    config, plan = bundle["config"], bundle["plan"]
    output.mkdir(parents=True, exist_ok=False)
    save(output / "plan.json", bundle)
    cells = {cell["id"]: cell for cell in config["cells"]}
    failed_warmups = set()
    all_valid = True
    try:
        with exclusive_node(output, reservation) as node:
            for index, job in enumerate(plan["jobs"], 1):
                directory = job_directory(output, index)
                directory.mkdir()
                cell = cells[job["cell"]]
                devices = cell["devices"]
                audit = {"plan_sha256": bundle["sha256"], "job": job, "reservation": reservation,
                         "launched": False, "invalid_reasons": []}
                key = (job["block"], job["cell"])
                if job["phase"] == "sample" and key in failed_warmups:
                    audit["invalid_reasons"].append("preceding warmup failed")
                    save(directory / "audit.json", audit)
                    continue
                pre = node.check_job(devices, config["cpu_budget"])
                audit["before"] = pre
                work = directory / "work"
                work.mkdir()
                spec = dict(cell["variants"][job["phase"]])
                stage_inputs(spec, work)
                spec["env"] = {**spec.get("env", {}),
                               "CUDA_VISIBLE_DEVICES": ",".join(pre["selected_gpu_uuids"])}
                audit["launched"] = True
                save(directory / "audit.json", audit)
                result = measure_job(spec, work, observe_process=lambda pid: node.sample_active(pid, devices))
                samples = [o["sample"] for o in result["process_observations"] if "sample" in o]
                audit["measurement_sha256"] = digest(result)
                audit["invalid_reasons"] = validate_observations(
                    samples, devices, pre["cpu_affinity"], one_gpu_per_pid=cell["engine"] in ("dmgmd", "lammps"),
                    by_gpu=pre["cpu"]["by_gpu"], cpu_capacity=pre["cpu"]["capacity"],
                    gpu_settings=pre["gpu_settings"], engine_binary=result["engine_binary"])
                try:
                    audit["after"] = node.check_job(devices, config["cpu_budget"])
                except ResourceError as error:
                    audit["invalid_reasons"].append(str(error))
                    audit["after"] = error.evidence
                    save(directory / "audit.json", audit)
                    raise
                save(directory / "audit.json", audit)
                if not result["valid"] or audit["invalid_reasons"]:
                    all_valid = False
                    if job["phase"] == "warmup":
                        failed_warmups.add(key)
    except (ValueError, OSError, RuntimeError, KeyboardInterrupt) as error:
        save(output / "stopped.json", {"error": str(error) or type(error).__name__,
                                      "evidence": getattr(error, "evidence", None)})
        raise
    save(output / "summary.json", replay(output))
    return all_valid


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    prepare = commands.add_parser("freeze", help="write a new immutable block list")
    prepare.add_argument("phase", choices=["pilot", "sample", "smoke"])
    prepare.add_argument("config", type=Path)
    prepare.add_argument("plan", type=Path)
    prepare.add_argument("--pilot-run", type=Path)
    run = commands.add_parser("run", help="run a frozen list serially on an exclusive node")
    run.add_argument("plan", type=Path)
    run.add_argument("output", type=Path)
    run.add_argument("--reservation", required=True, help="source of the exclusive four-GPU allocation")
    report = commands.add_parser("replay", help="recompute pairs from archived job records")
    report.add_argument("run_directory", type=Path)
    args = parser.parse_args()
    if args.action == "freeze":
        freeze(args.config, args.phase, args.plan, args.pilot_run)
    elif args.action == "run":
        return 0 if execute_plan(args.plan, args.output, args.reservation) else 1
    else:
        print(json.dumps(replay(args.run_directory), ensure_ascii=False, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (KeyError, OSError, RuntimeError, ValueError) as error:
        raise SystemExit(f"paired timing: {error}") from error
