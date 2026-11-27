"""Measure one prepared, single-segment paired benchmark job."""
from __future__ import annotations

import datetime as dt
import math
import os
from pathlib import Path
import re
import shlex
import socket
import subprocess
from threading import Event, Thread
import time

from run_benchmark import fields, reported_versions, save, sha, stop_process_tree


STDOUT = "paired-job.stdout.log"
STDERR = "paired-job.stderr.log"
RECORD = "paired-job.json"
WRAPPER_FILES = {STDOUT, STDERR, RECORD}
POLL_S = 0.005
PROCESS_SAMPLE_S = 0.2
NUMBER = r"[-+.\deE]+"
THERMO_COLS = 18
LAUNCHERS = {"mpirun", "mpiexec", "orterun", "prterun", "taskset", "numactl"}
LAMMPS_RANK_LAUNCHER = str(Path(__file__).resolve().parents[1] / "lammps_reference" / "launch_rank.py")


def positive_int(spec, key):
    value = spec[key]
    if type(value) is not int or value <= 0:
        raise ValueError(f"{key} must be a positive integer")
    return value


def relative_file(path):
    if not path or Path(path).is_absolute() or ".." in Path(path).parts or str(Path(path)) != path:
        raise ValueError(f"invalid job-relative path: {path!r}")
    return path


def finite(tokens):
    result = [float(token) for token in tokens]
    if not all(math.isfinite(value) for value in result):
        raise ValueError("nonfinite values")
    return result


def elapsed(start, end):
    return (end - start) / 1_000_000_000


def engine_spec(spec):
    engine = spec["engine"]
    if engine not in ("dmgmd", "gpumd", "wpe", "lammps"):
        raise ValueError(f"unknown benchmark engine: {engine}")
    steps = positive_int(spec, "steps")
    stride = positive_int(spec, "thermo_interval")
    atoms = positive_int(spec, "atoms")
    ranks = positive_int(spec, "ranks")
    steady = spec.get("steady_start_step", 1000)
    if type(steady) is not int or steady < 0:
        raise ValueError("steady_start_step must be a nonnegative integer")
    if steps % stride or (steady < steps and (steady % stride or (not steady and engine != "lammps"))):
        raise ValueError("thermo interval must include the final and steady start steps")
    if engine == "wpe" and ranks != 1:
        raise ValueError("WPE requires one GPU")
    if engine == "dmgmd" and (not spec.get("expected_backend") or not spec.get("expected_domain_mode")):
        raise ValueError("DMG-MD requires expected_backend and expected_domain_mode")
    if engine == "lammps":
        frame = spec["final_frame"]
        if frame["format"] != "lammps-dump":
            raise ValueError("LAMMPS final frame must be a custom dump")
        thermo = relative_file(spec.get("thermo_path", STDOUT))
    else:
        frame = spec.get("final_frame", {"path": "restart.xyz", "format": "extxyz"})
        if frame != {"path": "restart.xyz", "format": "extxyz"}:
            raise ValueError("GPUMD-family final frame must be restart.xyz")
        thermo = "thermo.out"
    path = relative_file(frame["path"])
    if path in WRAPPER_FILES or thermo in {STDERR, RECORD, path}:
        raise ValueError("conflicting final frame or thermo path")
    return engine, steps, stride, atoms, ranks, steady, thermo, path


def names_in(workdir):
    return {str(path.relative_to(workdir)) for path in workdir.rglob("*")
            if path.is_file() or path.is_symlink()}


def identity(expected, workdir, absolute, reasons):
    records = {}
    for name, expected_hash in expected.items():
        if not re.fullmatch(r"[0-9a-f]{64}", expected_hash):
            raise ValueError(f"invalid expected SHA-256: {name}")
        if absolute:
            path = Path(name)
            if not path.is_absolute():
                raise ValueError(f"binary path must be absolute: {name}")
        else:
            path = workdir / relative_file(name)
        resolved = path.resolve()
        record = {"path": str(resolved), "expected_sha256": expected_hash, "sha256": None}
        records[name] = record
        try:
            record["sha256"] = sha(resolved)
        except OSError as error:
            reasons.append(f"identity unreadable for {name}: {error}")
        if record["sha256"] != expected_hash:
            reasons.append(f"{'binary' if absolute else 'input'} identity mismatch: {name}")
    return records


def engine_executable(spec, command, binaries):
    """Require the observed launcher argv to name the registered engine payload."""
    executable = spec.get("engine_binary")
    if executable is None:
        if Path(command[0]).name not in LAUNCHERS:
            executable = command[0]
        else:
            candidates = [word for word in command[1:] if word in binaries
                          and Path(word).name not in LAUNCHERS]
            if len(candidates) != 1:
                raise ValueError("launcher needs an absolute, frozen engine_binary in its argv")
            executable = candidates[0]
    if executable not in binaries or executable not in command:
        raise ValueError("engine_binary must be frozen and present in command")
    return executable


def lammps_input_name(command, binaries, executable):
    if LAMMPS_RANK_LAUNCHER in command:
        if LAMMPS_RANK_LAUNCHER not in binaries:
            raise ValueError("LAMMPS rank launcher has no frozen identity")
        chosen = [command[index + 1] for index, word in enumerate(command[:-1])
                  if word == "--binary"]
        if chosen != [executable]:
            raise ValueError("LAMMPS rank launcher does not name the frozen engine")
        names = [command[index + 1] for index, word in enumerate(command[:-1])
                 if word == "--input"]
        if not names:
            names = ["in.lmp"]
    else:
        names = [command[index + 1] for index, word in enumerate(command[:-1])
                 if word in ("-in", "-i")]
    if len(names) != 1:
        raise ValueError("LAMMPS requires a single frozen input file")
    return relative_file(names[0])


def verify_commands(engine, workdir, spec, executable, steps, stride, frame):
    """Bind row-to-step and the final frame to a single frozen input run."""
    command, inputs = spec["command"], spec["inputs"]
    if engine == "lammps":
        name = lammps_input_name(command, spec["binaries"], executable)
        required = {name}
    else:
        name = "run.in"
        required = {name, "model.xyz"}
    if not required <= inputs.keys():
        raise ValueError(f"missing frozen inputs: {sorted(required - inputs.keys())}")
    lines = [shlex.split(line, comments=True) for line in (workdir / name).read_text().splitlines()]
    commands = [line for line in lines if line]
    run_indices = [index for index, line in enumerate(commands) if line[0] == "run"]
    if len(run_indices) != 1 or commands[run_indices[0]] != ["run", str(steps)]:
        raise ValueError(f"{name} must contain one frozen run of {steps} steps")
    run_index = run_indices[0]
    if engine == "lammps":
        stride_commands = [line for line in commands[:run_index] if line[0] == "thermo"]
        style = [line for line in commands[:run_index] if line[0] == "thermo_style"]
        modify = [line for line in commands[:run_index] if line[0] == "thermo_modify"]
        dumps = [line for line in commands if line[0] in ("dump", "write_dump")]
        if (stride_commands != [["thermo", str(stride)]] or len(style) != 1
                or style[0][1:3] != ["custom", "step"] or "atoms" not in style[0][3:]):
            raise ValueError("LAMMPS requires frozen custom thermo (Step, Atoms) and stride")
        flush = [line[index + 1] for line in modify for index, word in enumerate(line[:-1])
                 if word == "flush"]
        if not flush or flush[-1] != "yes" or commands.index(style[0]) >= commands.index(modify[-1]):
            raise ValueError("LAMMPS requires thermo_modify flush yes after thermo_style")
        if (len(dumps) != 1 or dumps[0][:4] != ["write_dump", "all", "custom", frame]
                or commands.index(dumps[0]) < run_index):
            raise ValueError("LAMMPS requires one final write_dump after the run")
        reads = [line[1] for line in commands[:run_index] if line[0] == "read_data" and len(line) > 1]
        if not reads or not set(reads) <= inputs.keys():
            raise ValueError("LAMMPS data input has no frozen identity")
        potentials = [line[2] for line in commands[:run_index]
                      if line[:2] == ["pair_style", "matpl/nep/kk"] and len(line) > 2]
        if len(potentials) != 1 or potentials[0] not in inputs:
            raise ValueError("LAMMPS potential input has no frozen identity")
    else:
        thermo = [line for line in commands[:run_index] if line[0] == "dump_thermo"]
        restarts = [line for line in commands[:run_index] if line[0] == "dump_restart"]
        if thermo != [["dump_thermo", str(stride)]] or restarts != [["dump_restart", str(steps)]]:
            raise ValueError("run.in requires frozen thermo stride and one final restart")
        potentials = [line[1] for line in commands[:run_index]
                      if line[0] == "potential" and len(line) > 1]
        if len(potentials) != 1 or potentials[0] not in inputs:
            raise ValueError("potential input has no frozen identity")


class ThermoRows:
    def __init__(self, engine, stride, atoms):
        self.engine = engine
        self.stride = stride
        self.atoms = atoms
        self.headers = 0
        self.width = None
        self.atom_index = None
        self.header_lines = []
        self.steps = []

    def accept(self, line):
        tokens = line.split()
        if not tokens:
            return None
        if self.engine == "lammps":
            if tokens[0] == "Step" and "Atoms" in tokens:
                self.headers += 1
                self.width = len(tokens)
                self.atom_index = tokens.index("Atoms")
                return None
            if line.startswith("Loop time of"):
                self.width = None
            if self.width is None or not re.fullmatch(r"[-+]?\d+", tokens[0]):
                return None
            if len(tokens) != self.width:
                raise ValueError("wrong LAMMPS thermo column count")
            values = finite(tokens)
            if values[self.atom_index] != self.atoms:
                raise ValueError("LAMMPS thermo atom count mismatch")
            step = int(tokens[0])
        else:
            if tokens[0] == "#":
                self.header_lines.append(tokens[1:])
                if tokens[1:2] == ["dump_thermo"]:
                    self.headers += 1
                return None
            if len(tokens) != THERMO_COLS:
                raise ValueError("wrong GPUMD-family thermo column count")
            finite(tokens)
            step = (len(self.steps) + 1) * self.stride
        self.steps.append(step)
        return step

    def validate(self, steps):
        first = 0 if self.engine == "lammps" else self.stride
        if self.headers != 1 or self.steps != list(range(first, steps + 1, self.stride)):
            raise ValueError("thermo rows differ from the frozen step schedule")
        if self.engine != "lammps":
            required = (["dump_thermo", str(self.stride)], ["format_version", "1"],
                        ["num_atoms", str(self.atoms)])
            if any(self.header_lines.count(line) != 1 for line in required):
                raise ValueError("missing or mismatched GPUMD-family thermo metadata")


class StartupMarkers:
    """Flushed log milestones in the installed GPUMD hosts and DMG-MD."""

    def __init__(self, engine):
        self.engine = engine

    def accept(self, line):
        if self.engine == "dmgmd":
            if line.startswith("DMGMD_MPI rank=0 "):
                return "mpi_device_ready"
        else:
            if line == "Started initializing positions and related parameters.":
                return "positions_start"
            if line == "Finished initializing positions and related parameters.":
                return "positions_ready"
        return None


def read_live(path, origin, clock, done, parser, label, samples, errors):
    """Keep each row's observation window rather than infer a write timestamp."""
    offset = 0
    pending = b""
    lower_ns = origin
    batch = 0
    while True:
        before_ns = time.monotonic_ns()
        try:
            with path.open("rb") as stream:
                stream.seek(offset)
                data = stream.read()
        except FileNotFoundError:
            data = b""
        except OSError as error:
            errors.append(f"live {label} read: {error}")
            return
        observed_ns = time.monotonic_ns()
        offset += len(data)
        lines = (pending + data).split(b"\n")
        pending = lines.pop()
        items = []
        for line in lines:
            try:
                item = parser.accept(line.decode("utf-8", errors="replace"))
                if item is not None:
                    items.append(item)
            except ValueError as error:
                errors.append(f"live {label} parse: {error}")
        for item in items:
            exit_ns = clock.get("exit_ns")
            upper_ns = min(observed_ns, exit_ns) if exit_ns is not None else observed_ns
            samples.append({label: item, "batch": batch, "rows_in_batch": len(items),
                            "earliest_ns": min(lower_ns, upper_ns),
                            "latest_ns": upper_ns, "observed_ns": observed_ns})
        lower_ns = before_ns
        batch += 1
        if done.is_set():
            # If this read raced the exit, drain once more after the exit.
            if before_ns >= clock["exit_ns"]:
                return
        else:
            done.wait(POLL_S)


def observe_active(callback, pid, origin, done, samples, errors):
    while not done.is_set():
        start = time.monotonic_ns()
        try:
            sample = callback(pid)
        except Exception as error:
            errors.append(f"process observer: {type(error).__name__}: {error}")
            return
        finish = time.monotonic_ns()
        samples.append({"begin_s": elapsed(origin, start), "end_s": elapsed(origin, finish),
                        "sample": sample})
        done.wait(max(0, PROCESS_SAMPLE_S - elapsed(start, finish)))


def enforce_deadline(proc, done, timeout, errors):
    if not done.wait(timeout) and proc.poll() is None:
        errors.append("job exceeded timeout")
        try:
            stop_process_tree(proc)
        except OSError as error:
            errors.append(f"process-tree cleanup: {error}")
            proc.kill()


def output_snapshot(workdir, before, errors):
    saved = {}
    for name in names_in(workdir) - before:
        try:
            saved[name] = (workdir / name).lstat()
        except OSError as error:
            errors.append(f"output inventory: {error}")
    return saved


def run_job(spec, workdir, command, thermo, parser, observer, record, before):
    done = Event()
    clock = {}
    extra = spec.get("env", {})
    with (workdir / STDOUT).open("xb") as stdout, (workdir / STDERR).open("xb") as stderr:
        started = time.monotonic_ns()
        record["launch_monotonic_ns"] = started
        try:
            proc = subprocess.Popen(command, cwd=workdir, env={**os.environ, **extra},
                                    stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
                                    start_new_session=True)
        except OSError as error:
            record["invalid_reasons"].append(f"launch failed: {error}")
            return
        record["pid"] = proc.pid
        errors = record["invalid_reasons"]
        workers = [Thread(target=read_live, args=(workdir / thermo, started, clock, done, parser,
                   "step", record["thermo_observations"], errors))]
        if spec["engine"] != "lammps":
            markers = StartupMarkers(spec["engine"])
            workers.append(Thread(target=read_live, args=(workdir / STDOUT, started, clock, done,
                           markers, "marker", record["startup_observations"], errors)))
        if observer is not None:
            workers.append(Thread(target=observe_active, args=(observer, proc.pid, started, done,
                           record["process_observations"], errors)))
        if spec.get("timeout_s") is not None:
            remaining = max(0, spec["timeout_s"] - elapsed(started, time.monotonic_ns()))
            workers.append(Thread(target=enforce_deadline, args=(proc, done, remaining,
                           record["invalid_reasons"])))
        try:
            for worker in workers:
                worker.start()
            proc.wait()
        except KeyboardInterrupt:
            record["interrupted"] = True
            errors.append("job interrupted")
        finally:
            if proc.returncode is None:
                stop_process_tree(proc)
            finished = time.monotonic_ns()
            clock["exit_ns"] = finished
            done.set()
            outputs = output_snapshot(workdir, before, errors)
            for worker in workers:
                if worker.ident is not None:
                    worker.join()
        record["exit_monotonic_ns"] = finished
        record["wall_s"] = elapsed(started, finished)
        record["returncode"] = proc.returncode
        if proc.returncode != 0:
            record["invalid_reasons"].append(f"nonzero exit: {proc.returncode}")
    return outputs


def timing_from_samples(record, steps, steady):
    origin = record["launch_monotonic_ns"]
    finish = record["exit_monotonic_ns"]
    samples = record["thermo_observations"]
    by_step = {sample["step"]: sample for sample in samples}
    for sample in samples:
        sample["observed_after_exit"] = sample["observed_ns"] > finish
    if samples:
        first = samples[0]
        bounds = [elapsed(origin, first["earliest_ns"]), elapsed(origin, first["latest_ns"])]
        record["first_thermo_bounds_s"] = bounds
        if first["rows_in_batch"] == 1:
            record["first_thermo_s"] = sum(bounds) / 2
    last = by_step.get(steps)
    if last is not None:
        bounds = [elapsed(last["latest_ns"], finish), elapsed(last["earliest_ns"], finish)]
        record["tail_bounds_s"] = bounds
        if last["rows_in_batch"] == 1:
            record["tail_s"] = sum(bounds) / 2
        else:
            record["invalid_reasons"].append("final thermo was coalesced with earlier rows")
    start = by_step.get(steady)
    if steps > steady:
        if (start is None or last is None or start["rows_in_batch"] != 1
                or last["rows_in_batch"] != 1 or start["latest_ns"] >= last["earliest_ns"]):
            record["invalid_reasons"].append("steady thermo time windows missing or unresolved")
        else:
            factor = 1000 / (steps - steady)
            bounds = [elapsed(start["latest_ns"], last["earliest_ns"]) * factor,
                      elapsed(start["earliest_ns"], last["latest_ns"]) * factor]
            record["steady_ms_step_bounds"] = bounds
            record["steady_ms_step"] = sum(bounds) / 2


def startup_from_samples(engine, record):
    origin = record["launch_monotonic_ns"]
    finish = record["exit_monotonic_ns"]
    markers = {}
    for event in record["startup_observations"]:
        event["observed_after_exit"] = event["observed_ns"] > finish
        markers.setdefault(event["marker"], event)
    first_thermo = record.get("first_thermo_bounds_s")
    startup = {
        "read_input_upper_s": first_thermo[1] if first_thermo else None,
        "upper_bound_source": "first thermo row" if first_thermo else None,
        "scope": record.get("startup_read_scope"),
    }
    if engine == "dmgmd":
        startup["unavailable_reason"] = "runtime has no standalone startup or input-read timer"
        ready = markers.get("mpi_device_ready")
        if ready:
            bounds = [elapsed(origin, ready["earliest_ns"]), elapsed(origin, ready["latest_ns"])]
            startup["launch_to_mpi_device_ready_bounds_s"] = bounds
            startup["model_read_upper_s"] = bounds[1]
            startup["model_read_upper_source"] = "DMGMD_MPI rank=0 after model parsing and device setup"
    elif engine == "lammps":
        startup["engine_read_data_cpu_s"] = record["read_input_s"]
        if record["read_input_s"] is None:
            startup["unavailable_reason"] = "engine did not report read_data CPU time"
    else:
        startup["unavailable_reason"] = "engine has no standalone startup or input-read timer"
        start = markers.get("positions_start")
        ready = markers.get("positions_ready")
        if ready:
            upper = elapsed(origin, ready["latest_ns"])
            startup["model_read_upper_s"] = upper
            startup["model_read_upper_source"] = "GPUMD positions-ready marker after model read"
        if start and ready:
            bounds = [max(0, elapsed(start["latest_ns"], ready["earliest_ns"])),
                      max(0, elapsed(start["earliest_ns"], ready["latest_ns"]))]
            startup["positions_init_bounds_s"] = bounds
            startup["positions_init_scope"] = "model read, velocity initialization and GPU allocation"
            if start["latest_ns"] < ready["earliest_ns"]:
                startup["positions_init_s"] = sum(bounds) / 2
    record["startup_read"] = startup


def validate_extxyz(path, atoms):
    with path.open() as stream:
        if int(stream.readline()) != atoms:
            raise ValueError("wrong extxyz atom count")
        header = stream.readline()
        lattice = re.search(r'\bLattice="([^"]+)"', header)
        pbc = re.search(r'\bpbc="([^"]+)"', header)
        props = re.search(r'\bProperties=(\S+)', header)
        if not lattice or not pbc or pbc.group(1).split() != ["T", "T", "T"] or not props:
            raise ValueError("missing extxyz lattice, periodicity or properties")
        matrix = finite(lattice.group(1).split())
        if len(matrix) != 9:
            raise ValueError("wrong extxyz lattice length")
        a, b, c, d, e, f, g, h, i = matrix
        volume = a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g)
        if not math.isfinite(volume) or volume <= 0:
            raise ValueError("invalid extxyz lattice volume")
        prop_tokens = props.group(1).split(":")
        if len(prop_tokens) % 3:
            raise ValueError("invalid extxyz property schema")
        layout = list(zip(prop_tokens[::3], prop_tokens[1::3], map(int, prop_tokens[2::3])))
        names = {name: (kind, size) for name, kind, size in layout}
        minimum = {"species": ("S", 1), "pos": ("R", 3), "mass": ("R", 1), "vel": ("R", 3)}
        if len(names) != len(layout) or any(names.get(name) != item for name, item in minimum.items()):
            raise ValueError("missing or repeated extxyz species, positions, masses or velocities")
        if any(kind not in ("S", "I", "R") or size <= 0 for _, kind, size in layout):
            raise ValueError("invalid extxyz property type or width")
        width = sum(size for _, _, size in layout)
        for _ in range(atoms):
            line = stream.readline()
            tokens = line.split()
            if not line.endswith("\n") or len(tokens) != width:
                raise ValueError("truncated extxyz atom record")
            index = 0
            for name, kind, size in layout:
                values = tokens[index:index + size]
                if kind == "R":
                    values = finite(values)
                if kind == "I":
                    list(map(int, values))
                if name == "mass" and values[0] <= 0:
                    raise ValueError("nonpositive extxyz mass")
                index += size
        if any(line.strip() for line in stream):
            raise ValueError("more than one extxyz frame")


def validate_lammps_dump(path, atoms, steps):
    with path.open() as stream:
        if stream.readline().strip() != "ITEM: TIMESTEP" or int(stream.readline()) != steps:
            raise ValueError("missing LAMMPS final timestep")
        if stream.readline().strip() != "ITEM: NUMBER OF ATOMS" or int(stream.readline()) != atoms:
            raise ValueError("wrong LAMMPS final atom count")
        box = stream.readline().split()
        if box != ["ITEM:", "BOX", "BOUNDS", "pp", "pp", "pp"]:
            raise ValueError("expected orthogonal periodic LAMMPS final box")
        for _ in range(3):
            low, high = finite(stream.readline().split())
            if low >= high:
                raise ValueError("invalid LAMMPS final box")
        columns = stream.readline().split()
        required = "id type mass x y z vx vy vz".split()
        if columns[:2] != ["ITEM:", "ATOMS"] or not set(required) <= set(columns[2:]):
            raise ValueError("missing LAMMPS final species, positions, masses or velocities")
        columns = columns[2:]
        atom_id, kind, mass = (columns.index(key) for key in ("id", "type", "mass"))
        seen = bytearray(atoms)
        for _ in range(atoms):
            line = stream.readline()
            tokens = line.split()
            if not line.endswith("\n") or len(tokens) != len(columns):
                raise ValueError("truncated LAMMPS atom record")
            index = int(tokens[atom_id])
            if not 1 <= index <= atoms or seen[index - 1]:
                raise ValueError("invalid or duplicate LAMMPS atom ID")
            seen[index - 1] = 1
            if int(tokens[kind]) <= 0 or finite(tokens)[mass] <= 0:
                raise ValueError("invalid LAMMPS atom type or mass")
        if any(line.strip() for line in stream):
            raise ValueError("more than one LAMMPS final frame")


def dmgmd_rank0_output(rows):
    key = "scientific_output_host_seconds_rank0_total"
    cost = {
        "seconds": None, "in_wall": True,
        "scope": "rank 0 host interval including collective frame gathering and thermo/restart writing",
        "source": f"DMGMD_DOMAIN_TIMING {key}",
        "unavailable_reason": "opt-in domain instrumentation did not report rank 0's output interval",
    }
    if not any(key in row for row in rows):
        return cost
    by_step_class = {}
    for row in rows:
        step_class = row.get("class")
        if (row.get("sequence") != "0" or step_class not in ("ordinary", "rebuild")
                or step_class in by_step_class or key not in row):
            raise ValueError("inconsistent DMG-MD rank 0 output timing categories")
        seconds = finite((row[key],))[0]
        if seconds < 0:
            raise ValueError("negative DMG-MD rank 0 output duration")
        by_step_class[step_class] = seconds
    if by_step_class.keys() != {"ordinary", "rebuild"}:
        raise ValueError("DMG-MD rank 0 output timing requires both step categories")
    cost["by_step_class_s"] = by_step_class
    cost["seconds"] = sum(by_step_class.values())
    cost["unavailable_reason"] = None
    return cost


def runtime_evidence(engine, log, steps, atoms, ranks, record):
    def lines(prefix):
        return [line for line in log.splitlines() if line.startswith(prefix)]

    record["reported_versions"] = reported_versions(engine, log)
    if engine == "dmgmd":
        timings = [fields(line) for line in lines("DMGMD_TIMING phase=")]
        runs = [row for row in timings if row.get("phase") == "run"]
        totals = [row for row in timings if row.get("phase") == "total"]
        record["engine_segments"] = runs
        record["engine_total"] = totals
        modes = [fields(line) for line in lines("DMGMD_DOMAIN mode=")]
        npt_modes = [fields(line) for line in lines("DMGMD_DOMAIN_NPT mode=")]
        devices = [fields(line) for line in lines("DMGMD_MPI rank=")]
        record["observed_runtime"] = {"domain": modes, "domain_npt": npt_modes, "devices": devices,
                                      "domain_summary": [fields(line) for line in lines("DMGMD_DOMAIN_SUMMARY ")]}
        record["engine_subsegments"] = [line for line in lines("DMGMD_DOMAIN_TIMING")
                                         if "HISTOGRAM" not in line]
        output_rows = [fields(line) for line in lines("DMGMD_DOMAIN_TIMING sequence=")]
        record["rank0_output"] = dmgmd_rank0_output(output_rows)
        record["startup_read_scope"] = "main input parsing and MPI startup are outside DMGMD_TIMING total"
        if len(runs) != 1:
            raise ValueError("expected one DMGMD_TIMING run segment")
        row = runs[0]
        if tuple(int(row[key]) for key in ("sequence", "steps", "atoms", "ranks")) != (0, steps, atoms, ranks):
            raise ValueError("DMG-MD run identity mismatch (sequence/steps/atoms/ranks)")
        record["engine_run_s"] = finite((row["seconds_max"],))[0]
        if len(totals) == 1:
            record["engine_total_s"] = finite((totals[0]["seconds_max"],))[0]
        spec = record["spec"]
        expected_backend = spec["expected_backend"]
        if row.get("backend") != expected_backend or {device.get("backend") for device in devices} != {expected_backend}:
            raise ValueError("DMG-MD runtime backend absent or differs from frozen identity")
        bound_uuids = {device.get("cuda_uuid") for device in devices}
        if (len(devices) != ranks or len(bound_uuids) != ranks or None in bound_uuids
                or {int(device["rank"]) for device in devices} != set(range(ranks))):
            raise ValueError("missing or inconsistent DMG-MD rank GPU evidence")
        ordered = sorted(devices, key=lambda device: int(device["local_rank"]))
        if (any(device.get("hostname") != socket.gethostname() or int(device["world_size"]) != ranks
                or int(device["local_size"]) != ranks for device in ordered)
                or [int(device["local_rank"]) for device in ordered] != list(range(ranks))):
            raise ValueError("DMG-MD rank host, world size or local rank layout differs from this job")
        selected = spec.get("env", {}).get("CUDA_VISIBLE_DEVICES", "").split(",")
        if all(device.startswith("GPU-") for device in selected):
            expected = [device.removeprefix("GPU-").replace("-", "").lower() for device in selected]
            observed = [device["cuda_uuid"].replace("-", "").lower() for device in ordered]
            if observed != expected:
                raise ValueError("DMG-MD logged GPU UUID order differs from the selected devices")
        if len(modes) != 1 or modes[0].get("mode") != spec["expected_domain_mode"]:
            raise ValueError("DMG-MD runtime domain absent or differs from frozen identity")
        if "expected_domain_npt_mode" in spec and (
                len(npt_modes) != 1 or npt_modes[0].get("mode") != spec["expected_domain_npt_mode"]):
            raise ValueError("DMG-MD runtime NPT domain absent or differs from frozen identity")
    elif engine == "lammps":
        loops = re.findall(rf"Loop time of ({NUMBER}) on (\d+) procs for (\d+) steps with (\d+) atoms", log)
        record["engine_segments"] = [{"seconds": finite((seconds,))[0], "ranks": int(n),
                                      "steps": int(count), "atoms": int(size)}
                                     for seconds, n, count, size in loops]
        read_data = re.findall(rf"read_data CPU = ({NUMBER}) seconds", log)
        if read_data:
            record["read_input_s"] = sum(finite(read_data))
            record["read_input_source"] = "LAMMPS read_data CPU"
        record["startup_read_scope"] = "read_data timer excludes later pair and neighbor setup"
        record["engine_subsegments"] = [line for line in log.splitlines()
                                         if re.match(r"^\s*(Pair|Neigh|Comm|Output|Modify|Other)\s*\|", line)]
        record["observed_runtime"] = {
            "kokkos": [line for line in log.splitlines() if "KOKKOS mode" in line or "kokkos_device" in line],
            "bindings": [fields(line) for line in lines("LAMMPS_GPU_BIND rank=")],
        }
        record["rank0_output"] = {"seconds": None, "in_wall": True,
                                  "scope": "LAMMPS Output section is loop aggregate; final write_dump is outside loop",
                                  "source": "MPI task timing breakdown",
                                  "unavailable_reason": "loop aggregate does not isolate rank 0's final write_dump"}
        if len(loops) != 1 or tuple(map(int, loops[0][1:])) != (ranks, steps, atoms):
            raise ValueError("LAMMPS loop identity mismatch (rank/steps/atoms)")
        record["engine_run_s"] = record["engine_segments"][0]["seconds"]
        if ("KOKKOS mode" not in log or "kokkos_device" not in log
                or "pair matpl/nep/kk" not in log or "NEP Kokkos potential" not in log):
            raise ValueError("LAMMPS did not report MatPL/KOKKOS device execution")
        bindings = record["observed_runtime"]["bindings"]
        if ranks > 1 or LAMMPS_RANK_LAUNCHER in record["spec"]["command"]:
            if (len(bindings) != ranks
                    or {int(binding["rank"]) for binding in bindings} != set(range(ranks))
                    or {int(binding["local_rank"]) for binding in bindings} != set(range(ranks))
                    or len({binding["cuda_uuid"] for binding in bindings}) != ranks):
                raise ValueError("missing or inconsistent LAMMPS rank GPU evidence")
    else:
        run_s = finite(re.findall(rf"Time used for this run = ({NUMBER}) second\.", log))
        totals = finite(re.findall(rf"^Time used = ({NUMBER}) s\.", log, re.MULTILINE))
        counts = [int(item) for item in re.findall(r"^Run (\d+) steps\.", log, re.MULTILINE)]
        count_atoms = [int(item) for item in re.findall(r"^Number of atoms is (\d+)\.", log, re.MULTILINE)]
        devices = [int(item) for item in re.findall(r"^number of GPUs = (\d+)", log, re.MULTILINE)]
        multi_nep = [int(item) for item in re.findall(r"Try to use (\d+) GPUs for the NEP part\.", log)]
        wpe = "Potential engine: Wisevolve Potential Engine" in log
        record["engine_segments"] = [{"seconds": seconds, "steps": count}
                                     for seconds, count in zip(run_s, counts)]
        record["observed_runtime"] = {"visible_gpu_counts": devices, "multi_gpu_nep_counts": multi_nep,
                                      "potential_engine": "wpe" if wpe else "native"}
        record["rank0_output"] = {"seconds": None, "in_wall": True,
                                  "scope": "single-process host output",
                                  "unavailable_reason": "engine does not report a separate host-output timer"}
        record["startup_read_scope"] = "GPUMD total includes startup inside main, input and initialization"
        if len(run_s) != 1 or counts != [steps] or count_atoms != [atoms]:
            raise ValueError("GPUMD run identity mismatch (segments/steps/atoms)")
        record["engine_run_s"] = run_s[0]
        if len(totals) == 1:
            record["engine_total_s"] = totals[0]
        if devices != [ranks] or multi_nep != ([ranks] if ranks > 1 else []) or wpe != (engine == "wpe"):
            raise ValueError("GPUMD GPU or potential engine identity mismatch")
    run_s = record["engine_run_s"]
    if not math.isfinite(run_s) or run_s <= 0:
        raise ValueError("invalid engine run duration")
    total_s = record.get("engine_total_s")
    if total_s is not None and (not math.isfinite(total_s) or total_s <= 0):
        raise ValueError("invalid engine total duration")
    if record.get("wall_s") is not None:
        record["outside_engine_run_s"] = record["wall_s"] - run_s
        if total_s is not None:
            record["outside_engine_total_s"] = record["wall_s"] - total_s


def validate_scientific_files(workdir, engine, steps, stride, atoms, thermo, frame, record):
    errors = record["invalid_reasons"]
    rows = ThermoRows(engine, stride, atoms)
    try:
        with (workdir / thermo).open(errors="replace") as stream:
            for line in stream:
                if not line.endswith("\n"):
                    raise ValueError("incomplete final line")
                rows.accept(line)
        rows.validate(steps)
    except (OSError, ValueError) as error:
        errors.append(f"thermo output validation: {error}")
    record["thermo_steps"] = rows.steps
    if rows.steps != [sample["step"] for sample in record["thermo_observations"]]:
        errors.append("live thermo rows differ from final thermo")
    try:
        if engine == "lammps":
            validate_lammps_dump(workdir / frame, atoms, steps)
        else:
            validate_extxyz(workdir / frame, atoms)
    except (OSError, ValueError) as error:
        errors.append(f"final frame validation: {error}")


def measure_job(spec, workdir, *, observe_process=None):
    """Measure one fresh job; logs and JSON are preserved for launched failures.

    Required spec keys are engine, command, inputs, binaries, atoms, ranks, steps and
    thermo_interval. inputs and binaries map file names to frozen SHA-256 strings.
    ranks is the GPU count for single-process GPUMD or WPE. DMG-MD also requires
    expected_backend and expected_domain_mode and may freeze expected_domain_npt_mode.
    engine_binary selects the payload for a multi-layer command.
    Optional env overrides are passed to the subprocess. extra_outputs documents
    accepted engine-generated files. required_log_patterns maps descriptions to regexes.
    observe_process(pid) runs separately and contributes JSON resource snapshots.
    """
    workdir = Path(workdir).resolve(strict=True)
    engine, steps, stride, atoms, ranks, steady, thermo, frame = engine_spec(spec)
    inputs, binaries, command = spec["inputs"], spec["binaries"], spec["command"]
    extras = spec.get("extra_outputs", {})
    required_outputs = {thermo, frame}
    timeout = spec.get("timeout_s")
    if timeout is not None and (type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0):
        raise ValueError("timeout_s must be positive and finite")
    if (not isinstance(command, list) or not command or not Path(command[0]).is_absolute()
            or command[0] not in binaries):
        raise ValueError("command must start with a frozen absolute launcher/executable path")
    executable = engine_executable(spec, command, binaries)
    for name, reason in extras.items():
        if relative_file(name) in required_outputs | WRAPPER_FILES | inputs.keys() or not reason:
            raise ValueError(f"invalid extra output declaration: {name}")
    if inputs.keys() & (required_outputs | WRAPPER_FILES):
        raise ValueError("inputs conflict with expected outputs or logs")
    matchers = {name: re.compile(pattern, re.MULTILINE)
                for name, pattern in spec.get("required_log_patterns", {}).items()}
    before = names_in(workdir)
    if before & WRAPPER_FILES:
        raise ValueError("job directory already has a measurement record or logs")
    record = {
        "spec": spec, "started_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "valid": False, "invalid_reasons": [], "wall_s": None, "tail_s": None,
        "steady_ms_step": None, "first_thermo_s": None, "read_input_s": None,
        "engine_run_s": None, "engine_segments": [], "thermo_observations": [],
        "startup_observations": [],
        "process_observations": [], "outputs": {}, "returncode": None,
        "thermo_source": thermo, "final_frame": frame, "thermo_poll_interval_s": POLL_S,
        "thermo_time_method": "file-visibility observation windows; midpoint estimates",
        "wall_end_method": "wait for foreground executable or MPI launcher, then validate outputs",
        "engine_binary": executable,
    }
    errors = record["invalid_reasons"]
    record["inputs"] = identity(inputs, workdir, False, errors)
    record["binaries"] = identity(binaries, workdir, True, errors)
    if before - inputs.keys():
        errors.append(f"job directory has undeclared pre-existing files: {sorted(before - inputs.keys())}")
    if not errors:
        try:
            verify_commands(engine, workdir, spec, executable, steps, stride, frame)
        except (OSError, ValueError) as error:
            errors.append(f"frozen run configuration: {error}")
    if not errors:
        parser = ThermoRows(engine, stride, atoms)
        output_stats = run_job(spec, workdir, command, thermo, parser, observe_process, record, before)
    else:
        (workdir / STDOUT).touch(exist_ok=False)
        (workdir / STDERR).write_text("\n".join(errors) + "\n")
    if record["wall_s"] is not None:
        # Recheck metadata after streaming validation to detect writes past exit.
        identities_after = {}
        for names, absolute, key in ((inputs, False, "inputs"), (binaries, True, "binaries")):
            checked = identity(names, workdir, absolute, errors)
            identities_after[key] = checked
            if checked != record[key]:
                errors.append(f"{key} changed during execution")
        record["identity_after"] = identities_after
        timing_from_samples(record, steps, steady)
        validate_scientific_files(workdir, engine, steps, stride, atoms, thermo, frame, record)
        if names_in(workdir) - before != output_stats.keys():
            errors.append("output file set changed after process exit")
        for name, saved in sorted(output_stats.items()):
            path = workdir / name
            if not path.is_file() or path.is_symlink():
                errors.append(f"invalid output file: {name}")
                continue
            current = path.stat()
            if (saved.st_ino, saved.st_size, saved.st_mtime_ns) != (
                    current.st_ino, current.st_size, current.st_mtime_ns):
                errors.append(f"output changed after process exit: {name}")
            if name in WRAPPER_FILES:
                declared = "wrapper log"
            elif name in required_outputs:
                declared = "required"
            else:
                declared = extras.get(name)
            if declared is None:
                errors.append(f"unexpected output: {name}")
            record["outputs"][name] = {"bytes": current.st_size, "declared": declared}
        wrapper_stdout = (workdir / STDOUT).read_text(errors="replace")
        log = wrapper_stdout
        if engine == "lammps" and thermo != STDOUT:
            engine_log_path = workdir / thermo
            log = engine_log_path.read_text(errors="replace") if engine_log_path.is_file() else ""
            bindings = (line for line in wrapper_stdout.splitlines()
                        if line.startswith("LAMMPS_GPU_BIND rank="))
            log += "\n" + "\n".join(bindings)
        stderr = (workdir / STDERR).read_text(errors="replace")
        combined = wrapper_stdout + "\n" + stderr
        if engine == "lammps" and thermo != STDOUT:
            combined += "\n" + log
        matches = {name: [match.group() for match in matcher.finditer(combined)]
                   for name, matcher in matchers.items()}
        record["runtime_identity_matches"] = matches
        for name, match in matches.items():
            if not match:
                errors.append(f"runtime identity mismatch: {name}")
        try:
            runtime_evidence(engine, log, steps, atoms, ranks, record)
        except (KeyError, ValueError) as error:
            errors.append(str(error))
        startup_from_samples(engine, record)
    record["valid"] = not errors
    save(workdir / RECORD, record)
    if record.get("interrupted"):
        raise KeyboardInterrupt
    return record
