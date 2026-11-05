#!/usr/bin/env python3
"""CPU-only fail-closed tests for the independent confirmation freezer."""
from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).with_name("freeze_scr_independent_confirmation.py")
spec = importlib.util.spec_from_file_location("scr_freezer", SCRIPT)
freezer = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(freezer)


class FreezeValidationTests(unittest.TestCase):
    def fixture(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        input_root = root / "registered-inputs"
        sources = {}
        inventory = {}
        for profile in ("base", "half"):
            source = input_root / profile / "10/seed-1"
            source.mkdir(parents=True)
            sources[profile] = source
            for name, contents in (("model.xyz", b"model"), ("run.in", b"run"), ("nep.txt", b"potential")):
                (source / name).write_bytes(contents)
                inventory[f"{profile}/10/seed-1/{name}"] = freezer.sha256_file(source / name)
        hashes = {profile: {name: inventory[f"{profile}/10/seed-1/{name}"]
                            for name in freezer.INPUT_NAMES} for profile in sources}
        for i in range(186):
            extra = input_root / f"extra/{i:03d}"
            extra.parent.mkdir(parents=True, exist_ok=True)
            extra.write_bytes(f"extra-{i}".encode())
            inventory[f"extra/{i:03d}"] = freezer.sha256_file(extra)

        manifest = {"confirmation_registration": {
            "primary_profiles": [
                {"cohort": "primary_base", "steps": 10, "seed_set": [1]},
                {"cohort": "primary_half", "steps": 10, "seed_set": [1]},
            ],
            "bridge_profile": {"cohort": "bridge_base", "steps": 10,
                               "seed_set": [1], "candidate_conditions": [
                                   {"ranks": 2, "backend": "HostStaged"}]}},
        }
        jobs = []
        for key in sorted(freezer.canonical_registered_keys(manifest)):
            meta = freezer.canonical_job_metadata(manifest)[key]
            profile = meta["profile"]
            source = sources[profile]
            result_root = root / "results" / key.replace("|", "__")
            jobs.append({"key": key, **meta, "status": "planned", "input_source_dir": str(source),
                         "input_sha256": hashes[profile], "result_root": str(result_root),
                         "result_file": str(result_root / "results.json")})
        plan = {"jobs": jobs, "input_inventory_sha256": inventory, "input_file_count": 192}
        ledger = {"jobs": [dict(job) for job in jobs], "input_inventory_sha256": dict(inventory)}
        return input_root, manifest, plan, ledger

    def test_registered_jobs_and_input_inventory_are_accepted(self):
        input_root, manifest, plan, ledger = self.fixture()
        self.assertEqual(len(freezer.validate_jobs(manifest, plan, ledger, input_root)), 5)

    def test_missing_registered_job_is_rejected(self):
        input_root, manifest, plan, ledger = self.fixture()
        plan["jobs"].pop()
        ledger["jobs"].pop()
        with self.assertRaisesRegex(freezer.FreezeError, "registered job set mismatch"):
            freezer.validate_jobs(manifest, plan, ledger, input_root)

    def test_changed_job_input_hash_is_rejected(self):
        input_root, manifest, plan, ledger = self.fixture()
        plan["jobs"][0]["input_sha256"]["model.xyz"] = "f" * 64
        ledger["jobs"][0]["input_sha256"]["model.xyz"] = "f" * 64
        with self.assertRaisesRegex(freezer.FreezeError, "job input hash differs"):
            freezer.validate_jobs(manifest, plan, ledger, input_root)

    def test_job_key_cannot_bind_another_profile_input(self):
        input_root, manifest, plan, ledger = self.fixture()
        index = next(i for i, item in enumerate(plan["jobs"]) if item["cohort"] == "primary_half")
        for collection in (plan["jobs"], ledger["jobs"]):
            collection[index]["input_source_dir"] = str(input_root / "base/10/seed-1")
        with self.assertRaisesRegex(freezer.FreezeError, "input source does not match"):
            freezer.validate_jobs(manifest, plan, ledger, input_root)

    def test_modified_input_bytes_are_rejected(self):
        input_root, manifest, plan, ledger = self.fixture()
        source = Path(plan["jobs"][0]["input_source_dir"])
        (source / "model.xyz").write_bytes(b"modified")
        with self.assertRaisesRegex(freezer.FreezeError, "inventory hash mismatch"):
            freezer.validate_jobs(manifest, plan, ledger, input_root)

    def test_ledger_job_tampering_is_rejected(self):
        input_root, manifest, plan, ledger = self.fixture()
        ledger["jobs"][0]["result_file"] += ".changed"
        with self.assertRaisesRegex(freezer.FreezeError, "ledger/plan job mismatch"):
            freezer.validate_jobs(manifest, plan, ledger, input_root)


if __name__ == "__main__":
    unittest.main(verbosity=2)
