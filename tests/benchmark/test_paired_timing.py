import json
import os
import tempfile
import unittest
from unittest.mock import patch
import shutil
from pathlib import Path

from paired_timing import execute_plan, freeze, make_plan, read_plan, replay, sha


def config():
    cells = []
    comparisons = []
    for material in ("carbon", "water"):
        for engine in ("gpumd", "dmgmd"):
            variants = {phase: {"steps": steps, "thermo_interval": 100, "engine": engine,
                                "ranks": 1, "atoms": 216,
                                "binaries": {"/bin/engine": "1" * 64},
                                "inputs": {"model.xyz": "2" * 64, "nep.txt": "3" * 64}}
                        for phase, steps in (("pilot", 10000), ("warmup", 1000),
                                             ("sample", 100000), ("smoke", 1500))}
            cells.append({"id": material + "-" + engine, "engine": engine, "material": material,
                          "devices": [0], "physics": {"ensemble": "npt_scr",
                          "model_sha256": "2" * 64, "potential_sha256": "3" * 64},
                          "variants": variants})
        comparisons.append({"id": material, "numerator": material + "-gpumd",
                            "denominator": material + "-dmgmd"})
    return {"cpu_budget": 4, "cells": cells, "comparisons": comparisons}


class PlanningTests(unittest.TestCase):
    def test_pilot_blocks_and_replay(self):
        cfg = config()
        pilot = make_plan(cfg, "pilot")
        cfg["cells"].reverse()
        self.assertEqual(pilot, make_plan(cfg, "pilot"))
        self.assertEqual(20260922, pilot["order_seed"])
        self.assertEqual(20, len(pilot["jobs"]))
        for block in range(1, 6):
            jobs = [job for job in pilot["jobs"] if job["block"] == block]
            self.assertEqual(sorted(c["id"] for c in cfg["cells"]), sorted(j["cell"] for j in jobs))
            self.assertEqual(jobs[0]["material"], jobs[1]["material"])
            self.assertEqual(jobs[2]["material"], jobs[3]["material"])
            self.assertNotEqual(jobs[1]["material"], jobs[2]["material"])


    def test_freeze_per_cell_and_pair_unequal_repeat_counts(self):
        cfg = config()
        pilots = []
        for cell in cfg["cells"]:
            slow = cell["id"] == "water-dmgmd"
            values = (80, 90, 100, 110, 120) if slow else (98, 98, 100, 102, 102)
            for block, wall in enumerate(values, 1):
                pilots.append({"cell": cell["id"], "phase": "pilot", "block": block,
                               "valid": True, "wall_s": wall})
        plan = make_plan(cfg, "sample", pilots)
        self.assertEqual(5, plan["decisions"]["water-dmgmd"]["repeats"])
        self.assertEqual(3, plan["decisions"]["carbon-gpumd"]["repeats"])
        self.assertEqual([1, 2, 3], plan["comparisons"][1]["blocks"])
        for block in range(1, 6):
            jobs = [j for j in plan["jobs"] if j["block"] == block]
            self.assertEqual(["warmup"] * (len(jobs) // 2) + ["sample"] * (len(jobs) // 2),
                             [j["phase"] for j in jobs])
            for name in {j["cell"] for j in jobs}:
                self.assertEqual(["warmup", "sample"], [j["phase"] for j in jobs if j["cell"] == name])
        for bad in (pilots[1:], pilots + pilots[:1], [dict(pilots[0], valid=False), *pilots[1:]]):
            with self.assertRaises(ValueError):
                make_plan(cfg, "sample", bad)

    def test_invalid_placement_task_and_protocol_rejected_before_launch(self):
        for field, value in (("devices", [1]), ("devices", [0, 2]), ("material", "other"),
                             ("physics", {"ensemble": "different"})):
            cfg = config()
            cfg["cells"][0][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                make_plan(cfg, "pilot")
        cfg = config()
        cfg["cells"][0]["variants"]["pilot"]["steps"] = 1000
        with self.assertRaises(ValueError):
            make_plan(cfg, "pilot")

    def test_frozen_manifest_roundtrip_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp)
            config_path = parent / "config.json"
            cfg = config()
            (parent / "run.in").write_text("potential nep.txt\nensemble npt_scr 300 300 40 0 425 1000\n")
            for cell in cfg["cells"]:
                spec = cell["variants"]["smoke"]
                spec["input_dir"] = str(parent)
                spec["inputs"]["run.in"] = sha(parent / "run.in")
            config_path.write_text(json.dumps(cfg))
            frozen = parent / "plan.json"
            freeze(config_path, "smoke", frozen)
            self.assertEqual(12, len(read_plan(frozen)["plan"]["jobs"]))
            with self.assertRaises(FileExistsError):
                freeze(config_path, "smoke", frozen)
            changed = json.loads(frozen.read_text())
            changed["plan"]["jobs"].reverse()
            frozen.write_text(json.dumps(changed))
            with self.assertRaises(ValueError):
                read_plan(frozen)

    def test_process_records_replay_and_reject_swapped_artifacts(self):
        from test_paired_measurement import prepare_control
        from test_paired_resources import live_snapshot

        # Only the node inventory is simulated. Actual child processes write the files and exit.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(os.environ.get("DMGMD_PAIRED_REPLAY_EVIDENCE", tmp))
            cells = []
            for engine in ("gpumd", "dmgmd"):
                work = root / ("input-" + engine)
                work.mkdir()
                spec = prepare_control(work, engine)
                spec["input_dir"] = str(work.absolute())
                physics = {"ensemble": "nve", "model_sha256": spec["inputs"]["model.xyz"],
                           "potential_sha256": spec["inputs"]["nep.txt"]}
                cells.append({"id": engine, "engine": engine, "material": "cpu-control",
                              "devices": [0], "physics": physics, "variants": {"smoke": spec}})
            cfg = {"cells": cells, "cpu_budget": 2, "comparisons": [
                {"id": "cpu-control", "numerator": "gpumd", "denominator": "dmgmd"}]}
            config_path, manifest, output = root / "config.json", root / "plan.json", root / "out"
            config_path.write_text(json.dumps(cfg))
            freeze(config_path, "smoke", manifest)
            with (patch("paired_resources.exclusive_node") as allocation,
                  patch("paired_resources.validate_observations", return_value=[])):
                node = allocation.return_value.__enter__.return_value
                node.check_job.return_value = {"cpu_affinity": [0, 1, 128, 129],
                                               "selected_gpu_uuids": ["GPU-cpucontrol"], "gpu_settings": {},
                                               "cpu": {"by_gpu": {}, "capacity": {}}}
                node.sample_active.return_value = live_snapshot()
                self.assertTrue(execute_plan(manifest, output, "simulated hardware for CPU control"))
            self.assertEqual(replay(output), json.loads((output / "summary.json").read_text()))
            self.assertEqual(3, replay(output)["comparisons"][0]["wall_ratio"]["n"])
            # Preserve archived evidence while checking that a replacement raw file is rejected.
            changed = Path(tmp) / "changed"
            shutil.copytree(output, changed)
            shutil.copyfile(changed / "job-0002/work/paired-job.json",
                            changed / "job-0001/work/paired-job.json")
            with self.assertRaisesRegex(ValueError, "measurement fingerprint"):
                replay(changed)

    def test_comparisons_pin_placement_reference_and_pilot_identity(self):
        pilots = [{"cell": c["id"], "phase": "pilot", "block": n, "valid": True, "wall_s": 10}
                  for c in config()["cells"] for n in range(1, 6)]
        for changed in ("card-count", "steady-start", "snapshot", "build", "backend"):
            cfg = config()
            cell = cfg["cells"][0]
            variant = cell["variants"]["sample"]
            if changed == "card-count":
                cell["devices"] = [0, 1]
                for spec in cell["variants"].values():
                    spec["ranks"] = 2
            elif changed == "steady-start":
                variant["steady_start_step"] = 2000
            elif changed == "snapshot":
                variant["inputs"]["model.xyz"] = "4" * 64
            elif changed == "build":
                variant["binaries"] = {"/bin/engine": "5" * 64}
            else:
                variant["expected_backend"] = "CudaAware"
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                make_plan(cfg, "sample", pilots)

if __name__ == "__main__":
    unittest.main()
