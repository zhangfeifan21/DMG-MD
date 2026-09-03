#!/usr/bin/env python3
"""Run GPUMD NEP MD golden/differential baselines without third-party Python packages."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BASE_DIR.parents[1]
DEFAULT_REFERENCE = PROJECT_ROOT.parent / "gpumd-reference" / "src" / "gpumd"
MANIFEST_PATH = BASE_DIR / "manifest.json"
GOLDEN_DIR = BASE_DIR / "goldens"
CALIBRATION_PATH = BASE_DIR / "calibration.json"
FIELD_RE = re.compile(r'(\w+)=(?:"([^"]*)"|(\S+))')


class BaselineError(RuntimeError):
    pass


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run_text(command: Sequence[str], cwd: Optional[Path] = None) -> str:
    result = subprocess.run(
        list(command), cwd=cwd, capture_output=True, text=True, check=False, timeout=20
    )
    if result.returncode != 0:
        return (result.stdout + result.stderr).strip()
    return result.stdout.strip()


def load_manifest() -> Dict[str, Any]:
    with MANIFEST_PATH.open(encoding="utf-8") as stream:
        return json.load(stream)


def validate_input_hashes(manifest: Dict[str, Any]) -> None:
    expected_paths = set(manifest["input_sha256"])
    actual_paths = {
        path.relative_to(BASE_DIR).as_posix()
        for path in (BASE_DIR / "inputs").rglob("*")
        if path.is_file()
    }
    if actual_paths != expected_paths:
        missing = sorted(expected_paths - actual_paths)
        extra = sorted(actual_paths - expected_paths)
        raise BaselineError(f"input inventory mismatch; missing={missing}, extra={extra}")
    for relative, expected_hash in manifest["input_sha256"].items():
        actual_hash = sha256(BASE_DIR / relative)
        if actual_hash != expected_hash:
            raise BaselineError(
                f"input hash mismatch for {relative}: expected {expected_hash}, got {actual_hash}"
            )


def validate_reference(executable: Path, manifest: Dict[str, Any]) -> str:
    expected_hash = manifest["reference"]["executable_sha256"]
    actual_hash = sha256(executable)
    if actual_hash != expected_hash:
        raise BaselineError(
            "reference executable hash mismatch: "
            f"expected {expected_hash}, got {actual_hash} ({executable})"
        )

    reference_repo = executable.resolve().parent.parent
    commit = run_text(["git", "-C", str(reference_repo), "rev-parse", "HEAD"])
    expected_commit = manifest["reference"]["commit"]
    if commit != expected_commit:
        raise BaselineError(
            f"reference commit mismatch: expected {expected_commit}, got {commit or '<unknown>'}"
        )
    status = run_text(["git", "-C", str(reference_repo), "status", "--short"])
    if status:
        raise BaselineError(f"reference repository is not clean:\n{status}")
    return commit


def child_environment(device: Optional[str]) -> Tuple[Dict[str, str], str]:
    inherited = os.environ.get("CUDA_VISIBLE_DEVICES", "").strip()
    if device is not None:
        selected = device.strip()
    elif inherited:
        tokens = [token.strip() for token in inherited.split(",") if token.strip()]
        if len(tokens) != 1:
            raise BaselineError(
                "CUDA_VISIBLE_DEVICES exposes more than one device; pass --device with one ID/UUID"
            )
        selected = tokens[0]
    else:
        selected = "0"
    if not selected or "," in selected or selected == "-1":
        raise BaselineError("exactly one CUDA device ID/UUID must be selected")
    env = os.environ.copy()
    env["CUDA_DEVICE_ORDER"] = "PCI_BUS_ID"
    env["CUDA_VISIBLE_DEVICES"] = selected
    return env, selected


def parse_fields(line: str) -> Tuple[List[str], Dict[str, str]]:
    keys: List[str] = []
    fields: Dict[str, str] = {}
    for match in FIELD_RE.finditer(line):
        key = match.group(1)
        value = match.group(2) if match.group(2) is not None else match.group(3)
        keys.append(key)
        fields[key] = value
    return keys, fields


def parse_properties(text: str) -> List[Tuple[str, str, int]]:
    tokens = text.split(":")
    if len(tokens) % 3:
        raise BaselineError(f"invalid Properties schema: {text}")
    result = []
    for index in range(0, len(tokens), 3):
        name, kind, width_text = tokens[index:index + 3]
        try:
            width = int(width_text)
        except ValueError as error:
            raise BaselineError(f"invalid Properties width in {text}") from error
        if kind not in ("S", "I", "R") or width <= 0:
            raise BaselineError(f"invalid Properties entry {name}:{kind}:{width_text}")
        result.append((name, kind, width))
    return result


def parse_xyz(path: Path) -> List[Dict[str, Any]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    frames: List[Dict[str, Any]] = []
    cursor = 0
    while cursor < len(lines):
        if not lines[cursor].strip():
            raise BaselineError(f"blank line where atom count was expected in {path}:{cursor + 1}")
        try:
            natoms = int(lines[cursor].strip())
        except ValueError as error:
            raise BaselineError(f"invalid atom count in {path}:{cursor + 1}") from error
        if cursor + natoms + 1 >= len(lines):
            raise BaselineError(f"truncated XYZ frame in {path}:{cursor + 1}")
        comment = lines[cursor + 1]
        field_order, fields = parse_fields(comment)
        if "Properties" not in fields:
            raise BaselineError(f"missing Properties in {path}:{cursor + 2}")
        properties = parse_properties(fields["Properties"])
        expected_columns = sum(width for _, _, width in properties)
        rows: List[List[str]] = []
        for line_number in range(cursor + 2, cursor + 2 + natoms):
            row = lines[line_number].split()
            if len(row) != expected_columns:
                raise BaselineError(
                    f"{path}:{line_number + 1} has {len(row)} columns; expected {expected_columns}"
                )
            rows.append(row)
        frames.append(
            {
                "natoms": natoms,
                "comment": comment,
                "field_order": field_order,
                "fields": fields,
                "properties": properties,
                "rows": rows,
            }
        )
        cursor += natoms + 2
    return frames


def parse_thermo(path: Path) -> List[Dict[str, Any]]:
    segments: List[Dict[str, Any]] = []
    current: Optional[Dict[str, Any]] = None
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if line.startswith("# dump_thermo "):
            current = {"headers": [line], "rows": []}
            segments.append(current)
        elif line.startswith("#"):
            if current is None:
                raise BaselineError(f"orphan thermo header in {path}:{line_number}")
            current["headers"].append(line)
        elif line.strip():
            if current is None:
                raise BaselineError(f"thermo data before header in {path}:{line_number}")
            try:
                row = [float(token) for token in line.split()]
            except ValueError as error:
                raise BaselineError(f"non-numeric thermo data in {path}:{line_number}") from error
            if len(row) != 18:
                raise BaselineError(
                    f"{path}:{line_number} has {len(row)} thermo columns; expected 18"
                )
            current["rows"].append(row)
    for segment in segments:
        if len(segment["headers"]) != 5:
            raise BaselineError(f"thermo segment in {path} does not have exactly five header lines")
        expected_columns = "# columns T KE PE sxx syy szz syz sxz sxy ax ay az bx by bz cx cy cz"
        if segment["headers"][4] != expected_columns:
            raise BaselineError(f"unexpected thermo columns in {path}: {segment['headers'][4]}")
        if not segment["headers"][3].endswith(" fs"):
            raise BaselineError(f"thermo dt_output does not declare fs in {path}")
    return segments


def validate_output_shape(path: Path, spec: Dict[str, Any]) -> None:
    kind = spec["kind"]
    if kind == "text":
        lines = path.read_text(encoding="utf-8").splitlines()
        if len(lines) != spec["lines"]:
            raise BaselineError(f"{path} has {len(lines)} lines; expected {spec['lines']}")
        return
    if kind == "thermo":
        segments = parse_thermo(path)
        rows = [len(segment["rows"]) for segment in segments]
        if rows != spec["segment_rows"]:
            raise BaselineError(f"{path} thermo segment rows {rows}; expected {spec['segment_rows']}")
        return
    if kind not in ("xyz", "restart"):
        raise BaselineError(f"unknown output kind {kind} for {path}")
    frames = parse_xyz(path)
    if len(frames) != spec["frames"]:
        raise BaselineError(f"{path} has {len(frames)} frames; expected {spec['frames']}")
    for index, frame in enumerate(frames):
        if frame["natoms"] != spec["natoms"]:
            raise BaselineError(
                f"{path} frame {index} has {frame['natoms']} atoms; expected {spec['natoms']}"
            )
        if frame["fields"]["Properties"] != spec["properties"]:
            raise BaselineError(
                f"{path} frame {index} Properties mismatch: {frame['fields']['Properties']}"
            )
        if frame["field_order"][-1] != "Properties":
            raise BaselineError(f"Properties is not the last comment field in {path} frame {index}")
    if kind == "xyz":
        actual_times = []
        for frame in frames:
            if "Time" not in frame["fields"]:
                raise BaselineError(f"missing Time field in {path}")
            actual_times.append(float(frame["fields"]["Time"]))
        for actual, expected in zip(actual_times, spec["times_fs"]):
            if abs(actual - expected) > 5e-9:
                raise BaselineError(f"{path} output times {actual_times}; expected {spec['times_fs']}")
    else:
        if any("Time" in frame["fields"] for frame in frames):
            raise BaselineError(f"restart unexpectedly contains Time in {path}")


def vector_cross(a: Sequence[float], b: Sequence[float]) -> Tuple[float, float, float]:
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def vector_dot(a: Sequence[float], b: Sequence[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def vector_norm(a: Sequence[float]) -> float:
    return math.sqrt(vector_dot(a, a))


def radial_cutoff(path: Path) -> float:
    lines = [line.split() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    header = lines[0]
    num_types = int(header[1])
    cutoff = next(tokens for tokens in lines[1:] if tokens[0] == "cutoff")
    if len(cutoff) == 5:
        return float(cutoff[1])
    if len(cutoff) != 2 * num_types + 3:
        raise BaselineError(f"unsupported cutoff line in {path}: {' '.join(cutoff)}")
    return max(float(cutoff[1 + 2 * atom_type]) for atom_type in range(num_types))


def force_path(model: Path, potential: Path) -> str:
    frames = parse_xyz(model)
    if len(frames) != 1:
        raise BaselineError(f"input model must contain one frame: {model}")
    fields = frames[0]["fields"]
    lattice = [float(token) for token in fields["Lattice"].split()]
    if len(lattice) != 9:
        raise BaselineError(f"input Lattice must have nine values: {model}")
    a, b, c = lattice[0:3], lattice[3:6], lattice[6:9]
    volume = abs(vector_dot(a, vector_cross(b, c)))
    thicknesses = (
        volume / vector_norm(vector_cross(b, c)),
        volume / vector_norm(vector_cross(c, a)),
        volume / vector_norm(vector_cross(a, b)),
    )
    pbc = fields.get("pbc", "T T T").split()
    threshold = 2.5 * (radial_cutoff(potential) + 1.0)
    return "small-box" if any(flag.upper() == "T" and t <= threshold for flag, t in zip(pbc, thicknesses)) else "large-box"


def validate_case_contract(case_name: str, case: Dict[str, Any], model: Path, potential: Path) -> None:
    header = potential.read_text(encoding="utf-8").splitlines()[0].split()
    actual_version = header[0]
    actual_num_types = int(header[1])
    if actual_version != case["potential_version"] or actual_num_types != case["potential_num_types"]:
        raise BaselineError(
            f"{case_name}: potential header is {actual_version}/{actual_num_types}, expected "
            f"{case['potential_version']}/{case['potential_num_types']}"
        )
    actual_path = force_path(model, potential)
    if actual_path != case["expected_force_path"]:
        raise BaselineError(
            f"{case_name}: fixture selects {actual_path}, expected {case['expected_force_path']}"
        )
    if "periodic_pair" not in case:
        return
    frame = parse_xyz(model)[0]
    if frame["fields"].get("pbc") != "T T T":
        raise BaselineError(f"{case_name}: periodic-pair assertion requires full PBC")
    lattice = [float(token) for token in frame["fields"]["Lattice"].split()]
    if any(abs(lattice[index]) > 1e-14 for index in (1, 2, 3, 5, 6, 7)):
        raise BaselineError(f"{case_name}: periodic-pair assertion currently requires an orthogonal box")
    offsets: Dict[str, Tuple[int, int]] = {}
    offset = 0
    for name, _, width in frame["properties"]:
        offsets[name] = (offset, width)
        offset += width
    species_offset, _ = offsets["species"]
    position_offset, _ = offsets["pos"]
    first, second = case["periodic_pair"]["atom_indices"]
    actual_species = [frame["rows"][first][species_offset], frame["rows"][second][species_offset]]
    if actual_species != case["periodic_pair"]["species"]:
        raise BaselineError(f"{case_name}: periodic-pair species mismatch: {actual_species}")
    first_position = [float(value) for value in frame["rows"][first][position_offset:position_offset + 3]]
    second_position = [float(value) for value in frame["rows"][second][position_offset:position_offset + 3]]
    lengths = [lattice[0], lattice[4], lattice[8]]
    displacement = []
    for left, right, length in zip(first_position, second_position, lengths):
        delta = right - left
        delta -= round(delta / length) * length
        displacement.append(delta)
    distance = vector_norm(displacement)
    maximum = case["periodic_pair"]["maximum_mic_distance_A"]
    if distance > maximum:
        raise BaselineError(
            f"{case_name}: periodic pair MIC distance {distance:.17g} A exceeds {maximum:.17g} A"
        )


class DiffCollector:
    def __init__(self, tolerances: Dict[str, Any], enforce: bool) -> None:
        self.tolerances = tolerances
        self.enforce = enforce
        self.stats: Dict[str, Dict[str, Any]] = {}

    def observe(self, category: str, reference: float, actual: float, context: str) -> None:
        if not (math.isfinite(reference) and math.isfinite(actual)):
            if reference != actual:
                raise BaselineError(f"non-finite mismatch at {context}: {actual} vs {reference}")
            return
        absolute = abs(actual - reference)
        relative = absolute / abs(reference) if abs(reference) > 1e-12 else 0.0
        stat = self.stats.setdefault(
            category,
            {"comparisons": 0, "max_abs": 0.0, "max_rel_ref_gt_1e-12": 0.0, "worst": ""},
        )
        stat["comparisons"] += 1
        if absolute > stat["max_abs"]:
            stat["max_abs"] = absolute
            stat["worst"] = context
        stat["max_rel_ref_gt_1e-12"] = max(stat["max_rel_ref_gt_1e-12"], relative)
        if self.enforce:
            tolerance = self.tolerances[category]
            allowed = tolerance["atol"] + tolerance["rtol"] * abs(reference)
            if absolute > allowed:
                raise BaselineError(
                    f"numeric mismatch at {context}: actual={actual:.17g}, reference={reference:.17g}, "
                    f"abs={absolute:.3e}, allowed={allowed:.3e} ({category})"
                )


def atom_category(name: str, restart: bool) -> str:
    if name == "pos":
        return "restart_position" if restart else "position"
    if name == "vel":
        return "restart_velocity" if restart else "velocity"
    if name == "mass":
        return "restart_mass" if restart else "mass"
    if name == "forces":
        return "force"
    if name == "energy_atom":
        return "energy"
    if name == "virial":
        return "virial"
    if name in ("charge", "bec", "unwrapped_position"):
        raise BaselineError(f"no baseline tolerance category is defined for property {name}")
    raise BaselineError(f"unexpected real-valued XYZ property {name}")


def compare_xyz(
    reference_path: Path,
    actual_path: Path,
    spec: Dict[str, Any],
    collector: DiffCollector,
    label: str,
) -> None:
    reference_frames = parse_xyz(reference_path)
    actual_frames = parse_xyz(actual_path)
    if len(reference_frames) != len(actual_frames):
        raise BaselineError(f"frame count mismatch for {label}")
    restart = spec["kind"] == "restart"
    field_categories = {
        "Time": "time",
        "Lattice": "lattice",
        "energy": "energy",
        "virial": "virial",
        "stress": "xyz_stress",
    }
    for frame_index, (reference, actual) in enumerate(zip(reference_frames, actual_frames)):
        context = f"{label}:frame[{frame_index}]"
        if reference["natoms"] != actual["natoms"]:
            raise BaselineError(f"atom count mismatch at {context}")
        if reference["field_order"] != actual["field_order"]:
            raise BaselineError(f"comment field order mismatch at {context}")
        for key in reference["field_order"]:
            if key in field_categories:
                reference_values = [float(token) for token in reference["fields"][key].split()]
                actual_values = [float(token) for token in actual["fields"][key].split()]
                if len(reference_values) != len(actual_values):
                    raise BaselineError(f"comment field width mismatch for {key} at {context}")
                for value_index, (ref_value, actual_value) in enumerate(zip(reference_values, actual_values)):
                    collector.observe(
                        field_categories[key], ref_value, actual_value, f"{context}:{key}[{value_index}]"
                    )
            elif reference["fields"][key] != actual["fields"].get(key):
                raise BaselineError(f"comment field {key} mismatch at {context}")
        if reference["properties"] != actual["properties"]:
            raise BaselineError(f"Properties schema mismatch at {context}")
        offset = 0
        for name, kind, width in reference["properties"]:
            for atom_index, (reference_row, actual_row) in enumerate(zip(reference["rows"], actual["rows"])):
                ref_tokens = reference_row[offset:offset + width]
                actual_tokens = actual_row[offset:offset + width]
                item_context = f"{context}:atom[{atom_index}]:{name}"
                if kind == "S":
                    if ref_tokens != actual_tokens:
                        raise BaselineError(f"atom order/string mismatch at {item_context}")
                elif kind == "I":
                    if [int(token) for token in ref_tokens] != [int(token) for token in actual_tokens]:
                        raise BaselineError(f"integer property mismatch at {item_context}")
                else:
                    category = atom_category(name, restart)
                    for value_index, (ref_token, actual_token) in enumerate(zip(ref_tokens, actual_tokens)):
                        collector.observe(
                            category,
                            float(ref_token),
                            float(actual_token),
                            f"{item_context}[{value_index}]",
                        )
            offset += width


def compare_thermo(
    reference_path: Path, actual_path: Path, collector: DiffCollector, label: str
) -> None:
    reference_segments = parse_thermo(reference_path)
    actual_segments = parse_thermo(actual_path)
    if len(reference_segments) != len(actual_segments):
        raise BaselineError(f"thermo segment count mismatch for {label}")
    for segment_index, (reference, actual) in enumerate(zip(reference_segments, actual_segments)):
        context = f"{label}:segment[{segment_index}]"
        if reference["headers"] != actual["headers"]:
            raise BaselineError(f"thermo header mismatch at {context}")
        if len(reference["rows"]) != len(actual["rows"]):
            raise BaselineError(f"thermo output-period mismatch at {context}")
        for row_index, (reference_row, actual_row) in enumerate(zip(reference["rows"], actual["rows"])):
            for column, (ref_value, actual_value) in enumerate(zip(reference_row, actual_row)):
                if column == 0:
                    category = "temperature"
                elif column in (1, 2):
                    category = "energy"
                elif 3 <= column <= 8:
                    category = "thermo_stress"
                else:
                    category = "lattice"
                collector.observe(
                    category,
                    ref_value,
                    actual_value,
                    f"{context}:row[{row_index}]:column[{column}]",
                )


def compare_stage(
    reference_dir: Path,
    actual_dir: Path,
    outputs: Dict[str, Any],
    collector: DiffCollector,
    label: str,
) -> None:
    for filename, spec in outputs.items():
        reference_path = reference_dir / filename
        actual_path = actual_dir / filename
        if spec["kind"] == "text":
            if reference_path.read_bytes() != actual_path.read_bytes():
                raise BaselineError(f"exact text mismatch for {label}/{filename}")
        elif spec["kind"] == "thermo":
            compare_thermo(reference_path, actual_path, collector, f"{label}/{filename}")
        else:
            compare_xyz(reference_path, actual_path, spec, collector, f"{label}/{filename}")


def stage_inputs_unchanged(stage_dir: Path, hashes: Dict[str, str]) -> None:
    for filename, expected_hash in hashes.items():
        actual_hash = sha256(stage_dir / filename)
        if actual_hash != expected_hash:
            raise BaselineError(f"executable modified staged input {stage_dir / filename}")


def execute_suite(
    executable: Path,
    manifest: Dict[str, Any],
    work_root: Path,
    env: Dict[str, str],
    timeout: int,
    launcher: Sequence[str] = (),
) -> Dict[Tuple[str, str], Path]:
    result_dirs: Dict[Tuple[str, str], Path] = {}
    for case_name, case in manifest["cases"].items():
        potential_source = BASE_DIR / case["potential"]
        first_stage = case["stages"][0]
        first_model = BASE_DIR / first_stage["model"]
        validate_case_contract(case_name, case, first_model, potential_source)
        prior_stage_dirs: Dict[str, Path] = {}
        for stage in case["stages"]:
            stage_name = stage["name"]
            stage_dir = work_root / case_name / stage_name
            stage_dir.mkdir(parents=True)
            if "model" in stage:
                model_source = BASE_DIR / stage["model"]
            else:
                source_stage, source_filename = stage["model_from_stage"]
                model_source = prior_stage_dirs[source_stage] / source_filename
            run_source = BASE_DIR / stage["run"]
            shutil.copyfile(model_source, stage_dir / "model.xyz")
            shutil.copyfile(run_source, stage_dir / "run.in")
            shutil.copyfile(potential_source, stage_dir / "nep.txt")
            staged_hashes = {
                "model.xyz": sha256(stage_dir / "model.xyz"),
                "run.in": sha256(stage_dir / "run.in"),
                "nep.txt": sha256(stage_dir / "nep.txt"),
            }
            result = subprocess.run(
                [*launcher, str(executable)],
                cwd=stage_dir,
                env=env,
                capture_output=True,
                text=True,
                check=False,
                timeout=timeout,
            )
            (stage_dir / "execution.stdout").write_text(result.stdout, encoding="utf-8")
            (stage_dir / "execution.stderr").write_text(result.stderr, encoding="utf-8")
            if result.returncode != 0:
                raise BaselineError(
                    f"{case_name}/{stage_name}: executable exited {result.returncode}\n"
                    f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
                )
            stage_inputs_unchanged(stage_dir, staged_hashes)
            ignored = {"model.xyz", "run.in", "nep.txt", "execution.stdout", "execution.stderr"}
            actual_outputs = {path.name for path in stage_dir.iterdir() if path.is_file()} - ignored
            expected_outputs = set(stage["outputs"])
            if actual_outputs != expected_outputs:
                raise BaselineError(
                    f"{case_name}/{stage_name}: output filenames mismatch; "
                    f"expected={sorted(expected_outputs)}, actual={sorted(actual_outputs)}"
                )
            for filename, spec in stage["outputs"].items():
                validate_output_shape(stage_dir / filename, spec)
            prior_stage_dirs[stage_name] = stage_dir
            result_dirs[(case_name, stage_name)] = stage_dir
    return result_dirs


def output_hashes(result_dirs: Dict[Tuple[str, str], Path], manifest: Dict[str, Any]) -> Dict[str, str]:
    hashes: Dict[str, str] = {}
    for case_name, case in manifest["cases"].items():
        for stage in case["stages"]:
            stage_name = stage["name"]
            for filename in stage["outputs"]:
                key = f"{case_name}/{stage_name}/{filename}"
                hashes[key] = sha256(result_dirs[(case_name, stage_name)] / filename)
    return hashes


def environment_metadata(executable: Path, commit: str, selected_device: str) -> Dict[str, Any]:
    return {
        "generated_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "reference_commit": commit,
        "executable": str(executable.resolve()),
        "executable_sha256": sha256(executable),
        "cuda_visible_devices": selected_device,
        "platform": platform.platform(),
        "python": sys.version.split()[0],
        "nvidia_smi": run_text(["nvidia-smi", "-L"]),
        "nvidia_driver": run_text(
            ["nvidia-smi", "--query-gpu=name,driver_version,compute_cap", "--format=csv,noheader"]
        ),
        "nvcc": run_text(["nvcc", "--version"]),
    }


def update_goldens(
    result_dirs: Dict[Tuple[str, str], Path], manifest: Dict[str, Any], metadata: Dict[str, Any]
) -> None:
    pending = Path(tempfile.mkdtemp(prefix="goldens-", dir=BASE_DIR))
    try:
        for case_name, case in manifest["cases"].items():
            for stage in case["stages"]:
                stage_name = stage["name"]
                destination = pending / case_name / stage_name
                destination.mkdir(parents=True)
                source = result_dirs[(case_name, stage_name)]
                for filename in stage["outputs"]:
                    shutil.copyfile(source / filename, destination / filename)
        metadata = dict(metadata)
        metadata["output_sha256"] = output_hashes(result_dirs, manifest)
        (pending / "metadata.json").write_text(
            json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        if GOLDEN_DIR.exists():
            shutil.rmtree(GOLDEN_DIR)
        pending.rename(GOLDEN_DIR)
    finally:
        if pending.exists():
            shutil.rmtree(pending)


def compare_with_goldens(
    result_dirs: Dict[Tuple[str, str], Path], manifest: Dict[str, Any], exact_reference: bool
) -> DiffCollector:
    metadata_path = GOLDEN_DIR / "metadata.json"
    if not metadata_path.exists():
        raise BaselineError("goldens are missing; generate them with --update-goldens on the pinned reference")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    if metadata["reference_commit"] != manifest["reference"]["commit"]:
        raise BaselineError("golden reference commit does not match manifest")
    if metadata["executable_sha256"] != manifest["reference"]["executable_sha256"]:
        raise BaselineError("golden reference executable hash does not match manifest")
    expected_golden_paths = {
        f"{case_name}/{stage['name']}/{filename}"
        for case_name, case in manifest["cases"].items()
        for stage in case["stages"]
        for filename in stage["outputs"]
    }
    if set(metadata["output_sha256"]) != expected_golden_paths:
        raise BaselineError("golden output inventory does not match manifest")
    for relative, expected_hash in metadata["output_sha256"].items():
        actual_hash = sha256(GOLDEN_DIR / relative)
        if actual_hash != expected_hash:
            raise BaselineError(
                f"golden hash mismatch for {relative}: expected {expected_hash}, got {actual_hash}"
            )
    collector = DiffCollector(manifest["tolerances"], enforce=True)
    for case_name, case in manifest["cases"].items():
        for stage in case["stages"]:
            stage_name = stage["name"]
            golden_stage = GOLDEN_DIR / case_name / stage_name
            if exact_reference:
                for filename in stage["outputs"]:
                    if (golden_stage / filename).read_bytes() != (
                        result_dirs[(case_name, stage_name)] / filename
                    ).read_bytes():
                        raise BaselineError(
                            f"pinned reference is not byte-identical for {case_name}/{stage_name}/{filename}"
                        )
            compare_stage(
                golden_stage,
                result_dirs[(case_name, stage_name)],
                stage["outputs"],
                collector,
                f"{case_name}/{stage_name}",
            )
    return collector


def calibrate(
    executable: Path,
    manifest: Dict[str, Any],
    work_root: Path,
    env: Dict[str, str],
    timeout: int,
    repeats: int,
    metadata: Dict[str, Any],
) -> None:
    if repeats < 2:
        raise BaselineError("calibration requires at least two repeats")
    reference_dirs = execute_suite(executable, manifest, work_root / "repeat-000", env, timeout)
    collector = DiffCollector(manifest["tolerances"], enforce=False)
    for repeat in range(1, repeats):
        actual_dirs = execute_suite(
            executable, manifest, work_root / f"repeat-{repeat:03d}", env, timeout
        )
        for case_name, case in manifest["cases"].items():
            for stage in case["stages"]:
                stage_name = stage["name"]
                compare_stage(
                    reference_dirs[(case_name, stage_name)],
                    actual_dirs[(case_name, stage_name)],
                    stage["outputs"],
                    collector,
                    f"repeat-{repeat:03d}/{case_name}/{stage_name}",
                )
    report = {
        "schema_version": 1,
        "repeats": repeats,
        "comparison_reference": "repeat-000",
        "near_zero_relative_cutoff": 1e-12,
        "metadata": metadata,
        "observed": collector.stats,
        "configured_tolerances": manifest["tolerances"],
    }
    CALIBRATION_PATH.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", type=Path, default=DEFAULT_REFERENCE)
    parser.add_argument("--candidate", type=Path, help="run this executable against GPUMD goldens")
    parser.add_argument("--update-goldens", action="store_true")
    parser.add_argument("--calibrate", type=int, metavar="REPEATS")
    parser.add_argument("--device", help="one CUDA device index or UUID; defaults to existing single selection or 0")
    parser.add_argument("--timeout", type=int, default=180, help="seconds allowed per GPUMD process")
    parser.add_argument("--keep-work", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.candidate and (args.update_goldens or args.calibrate):
        raise BaselineError("--candidate cannot update goldens or calibrate the reference")
    if args.update_goldens and args.calibrate:
        raise BaselineError("run --update-goldens and --calibrate as separate audited actions")
    executable = (args.candidate or args.reference).resolve()
    if not executable.is_file() or not os.access(executable, os.X_OK):
        raise BaselineError(f"executable is missing or not executable: {executable}")
    manifest = load_manifest()
    validate_input_hashes(manifest)
    commit = manifest["reference"]["commit"]
    if args.candidate is None:
        commit = validate_reference(executable, manifest)
    env, selected_device = child_environment(args.device)
    work_root = Path(tempfile.mkdtemp(prefix="dmgmd-gpumd-baseline-"))
    succeeded = False
    try:
        metadata = environment_metadata(executable, commit, selected_device)
        if args.calibrate:
            calibrate(
                executable, manifest, work_root, env, args.timeout, args.calibrate, metadata
            )
            print(f"PASS: calibrated {args.calibrate} complete baseline repetitions")
            print(f"Wrote {CALIBRATION_PATH}")
        else:
            result_dirs = execute_suite(executable, manifest, work_root, env, args.timeout)
            if args.update_goldens:
                update_goldens(result_dirs, manifest, metadata)
                print(f"PASS: updated pinned GPUMD goldens in {GOLDEN_DIR}")
            else:
                collector = compare_with_goldens(
                    result_dirs, manifest, exact_reference=args.candidate is None
                )
                role = "candidate" if args.candidate else "pinned GPUMD reference"
                print(f"PASS: all {len(manifest['cases'])} baseline cases match for {role}")
                for category, stat in sorted(collector.stats.items()):
                    print(
                        f"  {category:18s} max_abs={stat['max_abs']:.3e} "
                        f"max_rel={stat['max_rel_ref_gt_1e-12']:.3e}"
                    )
        succeeded = True
        return 0
    finally:
        if succeeded and not args.keep_work:
            shutil.rmtree(work_root)
        else:
            print(f"work directory retained: {work_root}", file=sys.stderr)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (BaselineError, subprocess.TimeoutExpired) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        raise SystemExit(1)
