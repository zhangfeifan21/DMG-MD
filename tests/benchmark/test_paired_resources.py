"""CPU-only regression coverage for paired timing resource gates."""

from copy import deepcopy
import sys
import unittest
from unittest.mock import patch

import paired_resources as resources


def gpu(index, utilization="0 %", processes=None):
    return {"index": index, "uuid": f"GPU-{index}", "utilization": utilization,
            "memory_used": "128 MiB" if processes else "0 MiB", "processes": processes or []}


def process(pid=10, ownership="job", cpus=(0, 1, 128, 129)):
    return {"pid": pid, "ownership": ownership, "affinity": {"10": cpus}}


def capacity(cgroup="0::/fixture", quota="max 100000", cpuset="0-3,128-131"):
    return {
        "status": "ok", "raw_cgroup": cgroup, "capacity_id": f"123:{cgroup}",
        "mount_point": "/sys/fs/cgroup", "mount_root": "/",
        "ancestors": [{"path": "/sys/fs/cgroup/fixture", "cpu.max": quota,
                       "cpu.weight": "100", "cpuset.cpus.effective": cpuset, "cpu.stat": "usage_usec 1"}],
    }


def live_snapshot():
    return {"gpus": [gpu(0, "80 %", [process()]), gpu(1), gpu(2), gpu(3)],
            "selected_gpu_uuids": ["GPU-0"]}


class ResourceTests(unittest.TestCase):
    def test_preflight_accepts_server_storage_and_still_requires_idle_gpus(self):
        for source, fstype in (("/dev/disk", "ext4"), ("fileserver:/export", "nfs4")):
            evidence = {"gpus": [gpu(index) for index in range(4)],
                        "storage": {"mount": {"source": source, "fstype": fstype}}}
            resources.check_snapshot(evidence)
            evidence["gpus"][1] = gpu(1, "80 %", [process()])
            with self.assertRaisesRegex(resources.ResourceError, r"GPUs: \[1\]") as caught:
                resources.check_snapshot(evidence)
            self.assertIs(caught.exception.evidence, evidence)

    def test_cooperative_lock_cannot_be_acquired_twice(self):
        evidence = {"gpus": [gpu(index) for index in range(4)], "storage": {"mount": {"source": "/dev/disk", "fstype": "ext4"}}}
        with patch.object(resources, "node_snapshot", return_value=evidence):
            with resources.exclusive_node("/example", "operator reservation"):
                with self.assertRaisesRegex(resources.ResourceError, "cooperative lock"):
                    with resources.exclusive_node("/other", "operator reservation"):
                        self.fail("lock acquired twice on the same host")
            with resources.exclusive_node("/other", "operator reservation"):
                pass

    def test_requires_own_activity_on_each_gpu_at_agreed_affinity(self):
        snapshot = live_snapshot()
        cpus = [0, 1, 128, 129]
        self.assertEqual(resources.validate_observations([snapshot], [0], cpus), [])
        for changes, reason in (
            (gpu(0, "80 %", [process(ownership="foreign")]), "external"),
            (gpu(0, "80 %", [process(cpus=[255])]), "exceeded"),
            (gpu(0, "80 %", [process(cpus=[0])]), "cover the CPU budget"),
            (gpu(0, "0 %", [process()]), "missing live GPU/CPU binding or activity"),
        ):
            with self.subTest(reason=reason):
                modified = deepcopy(snapshot)
                modified["gpus"][0] = changes
                self.assertIn(reason, resources.validate_observations([modified], [0], cpus)[0])
        snapshot["gpus"][1] = gpu(1, "99 %", [process(pid=30)])
        self.assertIn("outside the placement", resources.validate_observations([snapshot], [0], cpus)[0])

    def test_multi_gpu_single_process_is_only_allowed_for_non_mpi_engines(self):
        snapshot = live_snapshot()
        snapshot["gpus"][0] = gpu(0, "80 %", [process(cpus=[0, 128])])
        snapshot["gpus"][1] = gpu(1, "90 %", [process(cpus=[1, 129])])
        snapshot["selected_gpu_uuids"].append("GPU-1")
        cpus = [0, 1, 128, 129]
        plan = {0: [{"cpus": [0, 128]}], 1: [{"cpus": [1, 129]}]}
        self.assertEqual(resources.validate_observations([snapshot], [0, 1], cpus), [])
        self.assertIn("one distinct compute process", resources.validate_observations(
            [snapshot], [0, 1], cpus, one_gpu_per_pid=True, by_gpu=plan)[0])
        snapshot["gpus"][1]["processes"][0]["pid"] = 11
        self.assertIn("one compute process across", resources.validate_observations(
            [snapshot], [0, 1], cpus)[0])
        self.assertIn("at least two", resources.validate_observations(
            [snapshot], [0, 1], cpus, one_gpu_per_pid=True, by_gpu=plan)[0])
        self.assertEqual(resources.validate_observations(
            [snapshot, deepcopy(snapshot)], [0, 1], cpus, one_gpu_per_pid=True, by_gpu=plan), [])

    def test_rank_affinity_rejects_shared_cores_and_reversed_numa(self):
        snapshot = live_snapshot()
        snapshot["gpus"][0] = gpu(0, "80 %", [process(cpus=[0, 128])])
        snapshot["gpus"][2] = gpu(2, "90 %", [process(pid=11, cpus=[64, 192])])
        snapshot["selected_gpu_uuids"].append("GPU-2")
        cpus = [0, 64, 128, 192]
        plan = {"0": [{"cpus": [0, 128]}], "2": [{"cpus": [64, 192]}]}
        self.assertEqual(resources.validate_observations(
            [snapshot, deepcopy(snapshot)], [0, 2], cpus, one_gpu_per_pid=True, by_gpu=plan), [])
        for masks in ((cpus, cpus), ([64, 192], [0, 128])):
            with self.subTest(masks=masks):
                bad = deepcopy(snapshot)
                for gpu_id, mask in zip((0, 2), masks):
                    bad["gpus"][gpu_id]["processes"][0]["affinity"]["10"] = mask
                self.assertIn("did not use its assigned", resources.validate_observations(
                    [bad], [0, 2], cpus, one_gpu_per_pid=True, by_gpu=plan)[0])

    def test_payload_inode_and_visible_limits_must_match(self):
        snapshot = live_snapshot()
        cpus = [0, 1, 128, 129]
        proc = snapshot["gpus"][0]["processes"][0]
        proc["capacity"] = capacity()
        proc["capacity"]["ancestors"][0]["cpu.stat"] = "usage_usec 1001"
        proc["executable"] = {"path": sys.executable, **resources.executable_identity(sys.executable)}
        self.assertEqual(resources.validate_observations(
            [snapshot], [0], cpus, cpu_capacity=capacity(), engine_binary=sys.executable), [])
        for other in (capacity(cgroup="0::/other"), capacity(quota="100000 100000"),
                      capacity(cpuset="0-1,128-129")):
            with self.subTest(other=other):
                self.assertIn("cgroup membership or visible CPU limits", resources.validate_observations(
                    [snapshot], [0], cpus, cpu_capacity=other)[0])
        proc["executable"]["inode"] += 1
        self.assertIn("executable differs from registered payload", resources.validate_observations(
            [snapshot], [0], cpus, engine_binary=sys.executable)[0])

    def test_fixed_power_and_clock_setting_changes_reject_a_sample(self):
        snapshot = live_snapshot()
        original = {"GPU-0": {"application_clocks": {"graphics": "2500 MHz"},
                              "power_limits": {"current_power_limit": "350 W"}}}
        snapshot["gpu_settings"] = deepcopy(original)
        cpus = [0, 1, 128, 129]
        self.assertEqual(resources.validate_observations(
            [snapshot], [0], cpus, gpu_settings=original), [])
        snapshot["gpu_settings"]["GPU-0"]["power_limits"]["current_power_limit"] = "300 W"
        self.assertIn("application clocks or power limits changed", resources.validate_observations(
            [snapshot], [0], cpus, gpu_settings=original)[0])

    def test_exit_between_gpu_and_proc_samples_needs_previous_ownership_evidence(self):
        first = live_snapshot()
        exited = live_snapshot()
        exited["gpus"][0]["processes"] = [process(ownership="exited_before_proc_sample", cpus=())]
        cpus = [0, 1, 128, 129]
        self.assertEqual(resources.validate_observations([first, exited], [0], cpus), [])
        self.assertIn("unattributed", resources.validate_observations([exited], [0], cpus)[0])


if __name__ == "__main__":
    unittest.main()
