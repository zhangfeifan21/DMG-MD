"""Read-only resource evidence and cooperative exclusivity for L20 paired timing."""

from contextlib import contextmanager
from datetime import datetime, timezone
import csv
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import time
import xml.etree.ElementTree as ET

from cpu_sampling import capacity, cpu_set, parse_stat, sample


GPU_PLACEMENTS = {(0,), (0, 1), (0, 2), (0, 1, 2, 3)}
LOCK_ADDRESS = "\0dmgmd-paired-timing-l20-v1"


class ResourceError(ValueError):
    """Preflight refusal with the observations acquired before refusing."""

    def __init__(self, message, evidence=None):
        super().__init__(message)
        self.evidence = evidence


def command(*argv, returncodes=(0,)):
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=10,
                                env={**os.environ, "LC_ALL": "C"})
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ResourceError(f"{argv[0]} could not report resources: {error}") from error
    if result.returncode not in returncodes:
        raise ResourceError(f"{argv[0]} exited {result.returncode}: {result.stderr.strip()}")
    return result.stdout.strip()


def output_path(path):
    """Use the pre-existing mount containing the run directory."""
    existing = Path(path).resolve()
    while not existing.exists():
        existing = existing.parent
    if not existing.is_dir():
        raise ResourceError(f"output path is beneath a non-directory: {existing}")
    return existing


def leaf_devices(entries):
    leaves = []
    for entry in entries:
        if entry.get("children"):
            leaves.extend(leaf_devices(entry["children"]))
        else:
            leaves.append(entry)
    return leaves


def storage_snapshot(path):
    resolved = output_path(path)
    mount = json.loads(command("findmnt", "--json", "--target", str(resolved),
                               "--output", "TARGET,SOURCE,SOURCES,FSTYPE,MAJ:MIN"))["filesystems"][0]
    sources = {source.split("[", 1)[0] for source in mount.get("sources") or [mount["source"]]
               if source}
    devices = []
    for source in sorted(sources):
        if Path(source).is_block_device():
            try:
                tree = json.loads(command("lsblk", "--inverse", "--json", "--output",
                                          "NAME,PATH,TYPE,TRAN,ROTA,MODEL", source))
                devices.extend(leaf_devices(tree["blockdevices"]))
            except (ResourceError, ValueError, KeyError) as error:
                devices.append({"source": source, "inventory_error": str(error)})
    return {"requested": str(Path(path).resolve()), "existing_path": str(resolved),
            "mount": mount, "devices": devices}


def gpu_indices():
    output = command("nvidia-smi", "--query-gpu=index,uuid", "--format=csv,noheader")
    indices = {uuid: int(index) for index, uuid in csv.reader(output.splitlines(), skipinitialspace=True)}
    if sorted(indices.values()) != list(range(4)):
        raise ResourceError(f"exactly four GPU indices 0–3 are required: {indices}")
    return indices


def gpu_telemetry(indices):
    inventory = ET.fromstring(command("nvidia-smi", "-q", "-x"))
    gpus = []
    for gpu in inventory.findall("gpu"):
        uuid = gpu.findtext("uuid")
        if uuid not in indices or gpu.findtext("product_name") != "NVIDIA L20":
            raise ResourceError("GPU inventory changed or includes a card other than NVIDIA L20")
        processes = gpu.find("processes")
        if processes is None:
            raise ResourceError(f"GPU process list unavailable for {uuid}")
        gpus.append({
            "index": indices[uuid], "uuid": uuid, "name": gpu.findtext("product_name"),
            "pci_bus_id": gpu.get("id"), "compute_mode": gpu.findtext("compute_mode"),
            "mig_mode": gpu.findtext("mig_mode/current_mig"),
            "memory_used": gpu.findtext("fb_memory_usage/used"),
            "utilization": gpu.findtext("utilization/gpu_util"),
            "temperature": gpu.findtext("temperature/gpu_temp"),
            "pstate": gpu.findtext("performance_state"),
            "clocks": {key: gpu.findtext(f"clocks/{key}_clock") for key in ("graphics", "sm", "mem")},
            "max_clocks": {key: gpu.findtext(f"max_clocks/{key}_clock")
                           for key in ("graphics", "sm", "mem")},
            "application_clocks": {key: gpu.findtext(f"applications_clocks/{key}_clock")
                                   for key in ("graphics", "mem")},
            "clock_policy": {item.tag: item.text for item in gpu.findall("clock_policy/*")},
            "power": {key: gpu.findtext(f"gpu_power_readings/{key}") for key in (
                "average_power_draw", "instant_power_draw", "current_power_limit",
                "requested_power_limit", "default_power_limit")},
            "processes": [
                {"pid": int(proc.findtext("pid")), "type": proc.findtext("type"),
                 "name": proc.findtext("process_name"), "memory": proc.findtext("used_memory")}
                for proc in processes.findall("process_info")
            ],
        })
    if {gpu["uuid"] for gpu in gpus} != set(indices) or len(gpus) != len(indices):
        raise ResourceError("NVML GPU inventory is incomplete or has duplicate UUIDs")
    return {
        "captured_at": datetime.now(timezone.utc).isoformat(), "monotonic_ns": time.monotonic_ns(),
        "driver": inventory.findtext("driver_version"), "cuda": inventory.findtext("cuda_version"),
        "gpus": sorted(gpus, key=lambda gpu: gpu["index"]),
        "gpu_settings": {gpu["uuid"]: {
            "application_clocks": gpu["application_clocks"], "clock_policy": gpu["clock_policy"],
            "power_limits": {key: value for key, value in gpu["power"].items() if key.endswith("_limit")},
        } for gpu in gpus},
    }


def gpu_topology():
    raw = command("nvidia-smi", "topo", "-m")
    rows = {}
    for line in raw.splitlines():
        tokens = line.split()
        if tokens and re.fullmatch(r"GPU[0-3]", tokens[0]) and len(tokens) >= 7:
            rows[int(tokens[0][-1])] = {
                "links": tokens[1:5], "cpu_affinity": tokens[5], "numa": int(tokens[6]),
            }
    if sorted(rows) != list(range(4)):
        raise ResourceError("could not read NUMA topology for GPU indices 0–3")
    n0, n1, n2, n3 = (rows[i]["numa"] for i in range(4))
    if not (n0 == n1 and n2 == n3 and n0 != n2):
        raise ResourceError(f"L20 topology differs from the protocol: {rows}")
    return {"matrix": raw, "by_index": rows}


def placement(devices):
    devices = tuple(devices)
    if devices not in GPU_PLACEMENTS:
        raise ResourceError(f"GPU placement {devices} does not follow the L20 timing protocol")
    return devices


def cpu_resources(devices, cpu_budget, topology):
    if type(cpu_budget) is not int or cpu_budget <= 0:
        raise ResourceError("CPU budget must be a positive physical-core count")
    pid = os.getpid()
    cap = capacity(pid, [sample(pid)])
    if cap["status"] != "ok":
        raise ResourceError("CPU resource limits unavailable", cap)
    quota = cap["quota_cores"]
    if quota is not None and quota < cpu_budget:
        raise ResourceError(f"visible cgroup quota {quota} is below CPU budget {cpu_budget}", cap)
    if int(os.environ.get("SLURM_JOB_NUM_NODES", "1")) != 1:
        raise ResourceError("paired timing requires a single node allocation", cap)
    scheduler = {key: int(os.environ[key]) for key in
                 ("SLURM_CPUS_ON_NODE", "PBS_NP", "LSB_DJOB_NUMPROC") if key in os.environ}
    if any(limit < cpu_budget for limit in scheduler.values()):
        raise ResourceError(f"scheduler CPU limit below requested budget: {scheduler}", cap)
    raw = command("lscpu", "--parse=CPU,CORE,SOCKET,NODE")
    cores = {}
    for row in csv.reader(line for line in raw.splitlines() if not line.startswith("#")):
        cpu, core, package, numa = map(int, row)
        cores.setdefault((numa, package, core), set()).add(cpu)
    online = cpu_set(Path("/sys/devices/system/cpu/online").read_text())
    allowed = set(cap["allowed_cpus"])
    if scheduler and allowed == online:
        raise ResourceError("scheduler CPU placement has no enforced affinity/cpuset envelope", cap)
    # Select complete core sibling sets in the NUMA domain of each selected GPU.
    available = {key: cpus for key, cpus in cores.items() if cpus <= allowed}
    by_gpu = {}
    total, remainder = divmod(cpu_budget, len(devices))
    for position, device in enumerate(devices):
        details = topology["by_index"][device]
        closest = set(cpu_set(details["cpu_affinity"]))
        count = total + (position < remainder)
        selected = [key for key, cpus in sorted(available.items())
                    if key[0] == details["numa"] and cpus <= closest][:count]
        if len(selected) != count:
            raise ResourceError(f"insufficient complete NUMA-local physical cores for GPU {device}", cap)
        by_gpu[device] = [
            {"numa": key[0], "socket": key[1], "core": key[2], "cpus": sorted(available.pop(key))}
            for key in selected
        ]
    return {
        "physical_core_budget": cpu_budget,
        "by_gpu": by_gpu,
        "affinity": sorted(cpu for group in by_gpu.values() for core in group for cpu in core["cpus"]),
        "capacity": cap, "scheduler": scheduler,
    }


def cpu_limit_identity(cap):
    """Compare cgroup membership and visible limits, ignoring cumulative counters."""
    if cap.get("status") != "ok":
        return None
    return {
        "membership": {key: cap[key] for key in
                       ("raw_cgroup", "capacity_id", "mount_point", "mount_root")},
        "ancestors": [{key: ancestor[key] for key in
                       ("path", "cpu.max", "cpu.weight", "cpuset.cpus.effective")}
                      for ancestor in cap["ancestors"]],
    }


def node_snapshot(output_root):
    indices = gpu_indices()
    return {
        **gpu_telemetry(indices), "node": socket.gethostname(), "gpu_topology": gpu_topology(),
        "storage": storage_snapshot(output_root),
    }


def is_positive(raw):
    match = re.match(r"^(\d+(?:\.\d+)?)\s", raw or "")
    return match is not None and float(match[1]) > 0


def is_zero(raw):
    return re.match(r"^0\s", raw or "") is not None


def has_no_context(gpu):
    return not gpu["processes"] and is_zero(gpu["memory_used"])


def is_idle(gpu):
    return has_no_context(gpu) and is_zero(gpu["utilization"])


def executable_identity(path):
    stat = Path(path).stat()
    return {"device": stat.st_dev, "inode": stat.st_ino, "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns}


def job_process(pid, root_pid):
    """Attribute an NVML PID via live Linux ancestry and sample every visible task."""
    current = pid
    try:
        while current and current != root_pid:
            current = int(Path(f"/proc/{current}/stat").read_text().rsplit(")", 1)[1].split()[1])
        if current != root_pid:
            return {"ownership": "foreign"}
        base = Path(f"/proc/{pid}")
        details = sample(pid)
        if not base.exists():
            return {"ownership": "exited_before_proc_sample"}
        if details["status"] != "ok":
            return {"ownership": "unverified", "error": details["reason"]}
        exe = {"path": os.readlink(base / "exe"), **executable_identity(base / "exe")}
        resources = capacity(pid, [details])
        thread_ids = sorted(int(path.name) for path in (base / "task").iterdir())
        affinity = {}
        for thread in thread_ids:
            try:
                affinity[str(thread)] = sorted(os.sched_getaffinity(thread))
            except ProcessLookupError:
                continue
        if parse_stat((base / "stat").read_text())["start_ticks"] != details["start_ticks"]:
            return {"ownership": "unverified", "error": "PID identity changed during observation"}
        return {"ownership": "job", "affinity": affinity, "capacity": resources,
                "executable": exe, "start_ticks": details["start_ticks"]}
    except (FileNotFoundError, ProcessLookupError):
        # Short-lived processes can disappear between the NVML and /proc reads.
        return {"ownership": "exited_before_proc_sample"}
    except (PermissionError, ValueError, IndexError) as error:
        return {"ownership": "unverified", "error": str(error)}


class ExclusiveNode:
    def __init__(self, output_root, reservation, evidence):
        self.output_root = output_root
        self.reservation = reservation
        self.evidence = evidence
        self._indices = {gpu["uuid"]: gpu["index"] for gpu in evidence["gpus"]}
        self._cpu_baseline = None

    def check_job(self, devices, cpu_budget):
        devices = placement(devices)
        evidence = node_snapshot(self.output_root)
        # NVML utilization may still report the previous rolling interval.
        # Allow only that counter to settle before taking the after-job snapshot.
        if (all(has_no_context(gpu) for gpu in evidence["gpus"])
                and any(is_positive(gpu["utilization"]) for gpu in evidence["gpus"])):
            time.sleep(1)
            evidence = node_snapshot(self.output_root)
        evidence["reservation"] = self.reservation
        evidence["selected_gpu_uuids"] = [evidence["gpus"][index]["uuid"] for index in devices]
        check_snapshot(evidence)
        if {gpu["uuid"]: gpu["index"] for gpu in evidence["gpus"]} != self._indices:
            raise ResourceError("GPU index/UUID mapping changed during the reservation", evidence)
        if evidence["gpu_settings"] != self.evidence["gpu_settings"]:
            raise ResourceError("GPU application clocks or power limits changed during reservation", evidence)
        if evidence["storage"]["mount"] != self.evidence["storage"]["mount"]:
            raise ResourceError("output mount changed during the reservation", evidence)
        try:
            evidence["cpu"] = cpu_resources(devices, cpu_budget, evidence["gpu_topology"])
        except ResourceError as error:
            evidence["cpu"] = error.evidence
            raise ResourceError(str(error), evidence) from error
        evidence["cpu_affinity"] = evidence["cpu"]["affinity"]
        current_limits = cpu_limit_identity(evidence["cpu"]["capacity"])
        if self._cpu_baseline is None:
            self._cpu_baseline = current_limits
        elif current_limits != self._cpu_baseline:
            raise ResourceError("runner cgroup membership or visible CPU limits changed", evidence)
        return evidence

    def sample_active(self, pid, devices):
        devices = placement(devices)
        record = gpu_telemetry(self._indices)
        record["root_pid"] = pid
        record["selected_gpu_uuids"] = [record["gpus"][index]["uuid"] for index in devices]
        for gpu in record["gpus"]:
            for proc in gpu["processes"]:
                proc.update(job_process(proc["pid"], pid))
        return record


def check_snapshot(snapshot):
    busy = [gpu["index"] for gpu in snapshot["gpus"] if not is_idle(gpu)]
    if busy:
        raise ResourceError(f"non-idle or unverifiable L20 GPUs: {busy}", snapshot)


@contextmanager
def exclusive_node(output_root, reservation):
    """Record the operator allocation and keep cooperating runners serialized.

    An idle snapshot and the cooperative lock do not reserve the host. The
    scheduler or operator must hold the four-card node for the entire run.
    """
    if not isinstance(reservation, str) or not reservation.strip():
        raise ResourceError("record the scheduler or operator's four-card reservation")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as lock:
        try:
            lock.bind(LOCK_ADDRESS)
        except OSError as error:
            raise ResourceError(f"could not acquire the node-wide cooperative lock: {error}") from error
        evidence = node_snapshot(output_root)
        evidence["reservation"] = reservation.strip()
        evidence["cooperative_lock"] = "Linux abstract Unix socket in the runner's network namespace"
        check_snapshot(evidence)
        yield ExclusiveNode(output_root, reservation.strip(), evidence)


def validate_observations(observations, devices, cpu_affinity, *, one_gpu_per_pid=False,
                          by_gpu=None, cpu_capacity=None, gpu_settings=None, engine_binary=None):
    """Reject interference, incorrect placement and missing live binding evidence."""
    devices = set(placement(devices))
    allowed = set(cpu_affinity)
    per_card = {int(gpu): {cpu for core in cores for cpu in core["cpus"]}
                for gpu, cores in (by_gpu or {}).items()}
    if one_gpu_per_pid and (set(per_card) != devices or any(not mask for mask in per_card.values())
                            or sum(map(len, per_card.values())) != len(allowed)
                            or set().union(*per_card.values()) != allowed):
        return ["missing or overlapping per-GPU CPU plan for MPI ranks"]
    limits = cpu_limit_identity(cpu_capacity) if cpu_capacity is not None else None
    if cpu_capacity is not None and limits is None:
        return ["runner cgroup membership and visible CPU limits are unavailable"]
    try:
        expected_exe = executable_identity(engine_binary) if engine_binary is not None else None
    except OSError as error:
        return [f"registered payload executable unavailable: {error}"]
    seen = set()
    active = set()
    witnessed_cpus = set()
    pids_by_gpu = {}
    binding_samples = {}
    process_starts = {}
    confirmed = {(gpu["uuid"], process["pid"])
                 for sample in observations for gpu in sample["gpus"] for process in gpu["processes"]
                 if process["ownership"] == "job" and process["affinity"]}
    for sample_index, sample in enumerate(observations):
        if gpu_settings is not None and sample.get("gpu_settings") != gpu_settings:
            return ["GPU application clocks or power limits changed during the job"]
        selected = {gpu["uuid"] for gpu in sample["gpus"] if gpu["index"] in devices}
        if selected != set(sample["selected_gpu_uuids"]):
            return ["active GPU UUID list differs from GPU placement"]
        for gpu in sample["gpus"]:
            index = gpu["index"]
            if index not in devices and not is_idle(gpu):
                return [f"GPU {index} was busy outside the placement"]
            for proc in gpu["processes"]:
                pid = proc["pid"]
                label = f"GPU {index} process {pid}"
                if (proc["ownership"] == "exited_before_proc_sample"
                        and (gpu["uuid"], pid) in confirmed):
                    continue
                if proc["ownership"] != "job":
                    return [f"GPU {index} reported an external or unattributed PID {pid}"]
                if process_starts.setdefault(pid, proc.get("start_ticks")) != proc.get("start_ticks"):
                    return [f"{label} had inconsistent Linux start time"]
                if limits is not None and cpu_limit_identity(proc.get("capacity", {})) != limits:
                    return [f"{label} cgroup membership or visible CPU limits differ from the runner"]
                if expected_exe is not None:
                    exe = proc.get("executable", {})
                    if any(exe.get(key) != value for key, value in expected_exe.items()):
                        return [f"{label} executable differs from registered payload "
                                f"({exe.get('path')})"]
                masks = [set(mask) for mask in proc["affinity"].values()]
                if any(not mask or not mask <= allowed for mask in masks):
                    return [f"{label} exceeded the agreed CPU affinity"]
                if one_gpu_per_pid and any(mask != per_card[index] for mask in masks):
                    return [f"{label} did not use its assigned per-GPU CPU affinity"]
                if masks:
                    witnessed_cpus.update(set().union(*masks))
                    pids_by_gpu.setdefault(index, set()).add(pid)
                    binding_samples.setdefault(pid, set()).add(sample_index)
                    seen.add(index)
                    if is_positive(gpu["utilization"]):
                        active.add(index)
    if seen != devices or active != devices:
        return [f"missing live GPU/CPU binding or activity: bound={sorted(seen)}, active={sorted(active)}"]
    if one_gpu_per_pid and (any(len(pids) != 1 for pids in pids_by_gpu.values())
                            or len(set().union(*pids_by_gpu.values())) != len(devices)):
        return ["expected one distinct compute process on every selected GPU"]
    if not one_gpu_per_pid and len(binding_samples) != 1:
        return ["expected one compute process across all selected GPUs"]
    if one_gpu_per_pid and any(len(samples) < 2 for samples in binding_samples.values()):
        return ["MPI rank binding requires at least two post-exec observations per process"]
    if witnessed_cpus != allowed:
        return [f"observed affinity did not cover the CPU budget: {sorted(witnessed_cpus)}"]
    return []
