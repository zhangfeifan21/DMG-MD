"""CPU regressions for independent holdout and portable static evidence."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from analyze_preparation import THERMO_COLUMNS
import handoff
import prepare
import replay
import static_observables as static
import water_selection


def write_preparation_stage(base, stage, fixture, previous, parent_execution):
    job = base / stage
    analysis = job / "analysis"
    analysis.mkdir(parents=True)
    seed = prepare.INITIAL_VELOCITY_SEED if stage == "ber" else None
    shutil.copyfile(previous, job / "model.xyz")
    shutil.copyfile(base / "nep.txt", job / "nep.txt")
    (job / "run.in").write_text(prepare.input_script(fixture, stage, seed))
    restart = job / "restart.xyz"
    x = {"ber": 1, "scr": 2, "scr-extension": 3, "scr-extension-2": 4}[stage]
    restart.write_text(
        '1\npbc="T T T" Lattice="4 0 0 0 4 0 0 0 4" '
        f'Properties=species:S:1:pos:R:3:mass:R:1:vel:R:3\nO {x} 0 0 15.9994 1 0 0\n'
    )
    thermo = job / "thermo.out"
    profile = prepare.NPT_PROFILES[stage]
    schedule = prepare.thermo_schedule(profile)
    dt = fixture["time_step_fs"]
    with thermo.open("w") as stream:
        for steps, interval in schedule:
            stream.write(f"# dump_thermo {interval}\n# format_version 1\n# num_atoms 1\n"
                         f"# dt_output {dt * interval:g} fs\n# columns {THERMO_COLUMNS}\n")
            stream.write("300 0 0 0 0 0 0 0 0 4 0 0 0 4 0 0 0 4\n" * (steps // interval))
    execution = job / "execution.json"
    result = {
        "status": "PASS", "exit_code": 0, "velocity_seed": seed, "gpumd_binary_sha256": "c" * 64,
        "model_in_sha256": prepare.sha256(previous), "potential_sha256": prepare.sha256(job / "nep.txt"),
        "run_in_sha256": prepare.sha256(job / "run.in"), "thermo_sha256": prepare.sha256(thermo),
        "snapshot": prepare.snapshot_record(restart, base / "public-model.xyz", fixture),
    }
    if stage in prepare.SCR_EXTENSIONS:
        result["resumes_execution_sha256"] = prepare.sha256(parent_execution)
    prepare.write_json(execution, result)
    atoms, samples = prepare.read_thermo(thermo, schedule, dt)
    prepare.write_json(analysis / "summary.json", {
        **prepare.check_stage(profile, samples), "stage": profile, "thermo_sha256": prepare.sha256(thermo),
        "thermo": str(thermo),
        "atoms": atoms, "time_step_fs": dt, "schedule_steps_stride": schedule, "observed_samples": len(samples),
    })
    return restart, execution


def pack_water_preparation(bundle, water, frame, ref_sha, execution):
    evidence = bundle / "evidence"
    evidence.mkdir(parents=True)
    shutil.copyfile(execution.parent / "restart.xyz", bundle / "model.xyz")
    shutil.copyfile(water / "nep.txt", bundle / "nep.txt")
    shutil.copyfile(water / "source.json", evidence / "source.json")
    for stage in prepare.preparation_stages("water", evidence_dir=water):
        target = evidence / stage
        target.mkdir()
        for name in ("run.in", "execution.json", "thermo.out"):
            shutil.copyfile(water / stage / name, target / name)
        shutil.copyfile(water / stage / "analysis/summary.json", target / "summary.json")
    approval = static.water_selection_provenance(water, frame["sha256"])
    for name in static.WATER_SELECTION_FILES:
        shutil.copyfile(water / name, evidence / name)
    final_stage = execution.parent.name
    candidate = {
        "material": "water", "engines": {"gpumd": {"sha256": ref_sha}},
        "source": {"model": frame["sha256"], "potential": prepare.sha256(water / "nep.txt")},
        "started_utc": datetime.now(timezone.utc).isoformat(),
        "trial_evidence": {"preparation_stage": final_stage, "sha256": prepare.sha256(execution),
                           "water_selection": approval},
    }
    manifest = {
        "material": "water", "preparation_stage": final_stage, "model": frame,
        "potential_sha256": candidate["source"]["potential"],
        "evidence_sha256": {p.relative_to(bundle).as_posix(): prepare.sha256(p)
                            for p in evidence.rglob("*") if p.is_file()},
    }
    return candidate, evidence / final_stage / "execution.json", manifest


class StaticProvenanceTests(unittest.TestCase):
    def test_lammps_replay_preserves_source_and_uses_registered_conditions(self):
        source = b"nep4 2 O H\r\ncutoff 6 4 6 4 100 50\r\n"
        with TemporaryDirectory() as tmp:
            job = Path(tmp)
            shutil.copyfile(Path(__file__).parent / "regression/exact-face-water.xyz", job / "model.xyz")
            (job / "nep.txt").write_bytes(source)
            conditions = dict(temperature_K=310, pressure_GPa=0.012,
                              temperature_damping_steps=60, pressure_damping_steps=1200)
            with patch.dict(replay.CONDITIONS, conditions):
                replay.write_job(job, "water", "lammps", 2, "gpumd_5_9_1")
            self.assertEqual((job / "nep.gpumd.txt").read_bytes(), source)
            self.assertEqual((job / "nep.txt").read_text(), "nep4 2 O H\ncutoff 6 4 100 50\n")
            identity = json.loads((job / "conversion.json").read_text())
            self.assertEqual(identity["source_sha256"], static.sha256(job / "nep.gpumd.txt"))
            self.assertEqual(identity["lammps_sha256"], static.sha256(job / "nep.txt"))
            script = (job / "in.lmp").read_text()
            self.assertIn("processors 1 2 1\nread_data data.lmp", script)
            self.assertIn("fix thermostat all temp/berendsen/kk 310 310 0.03\n", script)
            self.assertIn("fix barostat all press/berendsen iso 120 120 0.6 modulus 22000\n", script)

    def test_holdout_requires_a_freeze_before_touching_inputs(self):
        with self.assertRaisesRegex(ValueError, "frozen --calibration"):
            static.holdout_provenance(SimpleNamespace(calibration=None))

    def test_holdout_keeps_logical_root_for_linked_archive_evidence(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            archived = root / "archive-execution.json"
            archived.write_text("{}")
            stage = root / "new/carbon/scr"
            stage.mkdir(parents=True)
            execution = stage / "execution.json"
            execution.symlink_to(archived)
            binary = root / "engine"
            binary.write_text("unit binary")
            binary_sha = prepare.sha256(binary)
            calibration = root / "calibration.json"
            calibration.write_text("{}")
            source = {"potential_sha256": binary_sha}
            engine = {name: {"sha256": binary_sha} for name in static.ENGINE_NAMES}
            engine["wpe"]["library"] = {"sha256": binary_sha}
            freeze = {"calibration_status": "PASS", "material": "carbon", "snapshot_sha256": [],
                      "baseline": {"gpu_uuid": "unit-gpu", "engines": engine, "source": {"potential": binary_sha}}}
            args = SimpleNamespace(
                calibration=calibration, material="carbon", gpu="unit-gpu", lammps_geometry="native",
                potential=binary, trial_evidence=None, model=stage / "restart.xyz",
                **{f"{name}_bin": binary for name in static.ENGINE_NAMES},
            )
            with (patch.object(static, "checked_calibration", return_value=freeze),
                  patch.object(static, "wpe_library", return_value=binary),
                  patch.object(static, "check_holdout_execution",
                               return_value=(source, {"sha256": "unit-final"}, binary_sha)) as checked):
                evidence = static.holdout_provenance(args)
            checked.assert_called_once_with(execution, "carbon")
            self.assertEqual(evidence["path"], str(execution))

    def test_water_bundle_requires_approval_before_reading_the_holdout(self):
        with TemporaryDirectory() as tmp:
            bundle = Path(tmp)
            prepare.write_json(bundle / "manifest.json", {"material": "water", "evidence_sha256": {}})
            args = SimpleNamespace(frame=None, calibration=None, output=None, bundle=bundle)
            with self.assertRaisesRegex(ValueError, "water selection evidence missing"):
                static.verify(args)

    def test_water_holdout_requires_extension_and_replays_the_preparation_chain(self):
        with TemporaryDirectory() as tmp:
            water = Path(tmp) / "water"
            water.mkdir()
            initial = water / "public-model.xyz"
            initial.write_text(
                '1\npbc="T T T" Lattice="4 0 0 0 4 0 0 0 4" '
                'Properties=species:S:1:pos:R:3:mass:R:1\nO 0 0 0 15.9994\n'
            )
            potential = water / "nep.txt"
            potential.write_text("unit-test potential")
            fixture = dict(prepare.FIXTURES["water"], public_model_sha256=prepare.sha256(initial),
                           potential_sha256=prepare.sha256(potential))
            prepare.write_json(water / "source.json", {
                "material": "water", "atoms": 1, "model_sha256": fixture["public_model_sha256"],
                "potential_sha256": fixture["potential_sha256"],
            })
            ber, ber_execution = write_preparation_stage(water, "ber", fixture, initial, None)
            scr, scr_execution = write_preparation_stage(water, "scr", fixture, ber, ber_execution)
            with patch.dict(prepare.FIXTURES, water=fixture):
                _, initial_frame, _ = prepare.check_preparation(water, "water", through_stage="scr")
                self.assertEqual(initial_frame["sha256"], prepare.sha256(scr))
                with self.assertRaisesRegex(FileNotFoundError, "scr-extension"):
                    prepare.check_preparation(water, "water")
                with self.assertRaisesRegex(ValueError, "scr-extension"):
                    static.check_holdout_execution(scr_execution, "water")

                summary = scr_execution.parent / "analysis/summary.json"
                current = json.loads(summary.read_text())
                tampered = json.loads(summary.read_text())
                tampered["effective_samples"]["volume_A3"]["effective_samples"] = 99.0
                prepare.write_json(summary, tampered)
                with self.assertRaisesRegex(ValueError, "analysis is stale"):
                    prepare.check_preparation(water, "water", through_stage="scr")
                legacy = dict(current, status="FAIL", checks={**current["checks"], prepare.LEGACY_ESS_KEY: False})
                prepare.write_json(summary, legacy)
                with self.assertRaisesRegex(ValueError, "failed the recorded criteria"):
                    prepare.check_preparation(water, "water", through_stage="scr")
                prepare.write_json(summary, current)

                final, execution = write_preparation_stage(water, "scr-extension", fixture, scr, scr_execution)
                _, frame, ref_sha = static.check_holdout_execution(execution, "water")
                self.assertEqual(frame["sha256"], prepare.sha256(final))
                self.assertEqual(prepare.thermo_schedule("scr-extension"), ((100000, 100),))
                self.assertNotIn("velocity", (execution.parent / "run.in").read_text())

                with self.assertRaisesRegex(ValueError, "missing or unreadable water selection"):
                    static.water_selection_provenance(water, frame["sha256"])
                prepare.write_json(water / water_selection.DIAGNOSTICS_FILENAME,
                                   water_selection.build_diagnostics(water))
                water_selection.record_decision(water, "select", "CPU test fixture")
                bundle = Path(tmp) / "bundle"
                candidate, packaged_execution, manifest = pack_water_preparation(bundle, water, frame, ref_sha, execution)
                static.verify_holdout_preparation(candidate, packaged_execution, bundle, manifest)
                early = dict(candidate, started_utc=candidate["trial_evidence"]["water_selection"]["created_utc"])
                with self.assertRaisesRegex(ValueError, "must start after"):
                    static.verify_holdout_preparation(early, packaged_execution, bundle, manifest)
                continued = Path(tmp) / "continued"
                continued.mkdir()
                for name in ("source.json", "public-model.xyz", "nep.txt", "ber", "scr", "scr-extension"):
                    (continued / name).symlink_to(water / name)
                latest = continued / "scr-extension-2"
                latest.symlink_to(continued / "missing")
                with self.assertRaisesRegex(FileNotFoundError, "scr-extension-2"):
                    prepare.check_preparation(continued, "water")
                latest.unlink()
                tail, continuation = write_preparation_stage(continued, "scr-extension-2", fixture, final, execution)
                self.assertEqual((latest / "run.in").read_bytes(), (execution.parent / "run.in").read_bytes())
                with self.assertRaisesRegex(ValueError, "scr-extension-2"):
                    static.check_holdout_execution(continued / "scr-extension/execution.json", "water")
                _, earlier, _ = prepare.check_preparation(continued, "water", through_stage="scr-extension")
                self.assertEqual(earlier, frame)
                _, latest_frame, latest_ref = static.check_holdout_execution(continuation, "water")
                self.assertEqual(latest_frame["sha256"], prepare.sha256(tail))

                prepare.write_json(continued / water_selection.DIAGNOSTICS_FILENAME,
                                   water_selection.build_diagnostics(continued))
                water_selection.record_decision(continued, "select", "CPU continuation test fixture")
                extended_bundle = Path(tmp) / "extended-bundle"
                later, later_execution, latest_manifest = pack_water_preparation(
                    extended_bundle, continued, latest_frame, latest_ref, continuation)
                static.verify_holdout_preparation(later, later_execution, extended_bundle, latest_manifest)
                with self.assertRaisesRegex(ValueError, "selected preparation endpoint"):
                    static.verify_holdout_preparation(candidate, packaged_execution, extended_bundle, manifest)
                incomplete_manifest = dict(latest_manifest, evidence_sha256=dict(latest_manifest["evidence_sha256"]))
                del incomplete_manifest["evidence_sha256"]["evidence/scr-extension/execution.json"]
                with self.assertRaisesRegex(ValueError, "preparation evidence missing"):
                    static.verify_holdout_preparation(later, later_execution, extended_bundle, incomplete_manifest)

                with patch.dict(os.environ, OMPI_HOME="unit", UCX_HOME="unit"):
                    for model in (final, tail):
                        args = SimpleNamespace(trial="calibration-403", gpu="GPU-aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                                               model=model, potential=potential, calibration=None, trial_evidence=None)
                        with self.assertRaisesRegex(ValueError, "require --trial holdout"):
                            static.measure(args)

                tampered = json.loads(continuation.read_text())
                tampered["resumes_execution_sha256"] = prepare.sha256(scr_execution)
                prepare.write_json(continuation, tampered)
                with self.assertRaisesRegex(ValueError, "configuration differs"):
                    static.check_holdout_execution(continuation, "water")

                (bundle / "model.xyz").write_text(scr.read_text())
                with self.assertRaisesRegex(ValueError, "packaged SCR snapshot changed"):
                    static.verify_holdout_preparation(candidate, packaged_execution, bundle, manifest)

                record = json.loads(execution.read_text())
                record["resumes_execution_sha256"] = prepare.sha256(ber_execution)
                prepare.write_json(execution, record)
                with self.assertRaisesRegex(ValueError, "configuration differs"):
                    static.check_holdout_execution(execution, "water")

    def test_handoff_matches_relocated_independent_trials_and_water_endpoint(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "new-root"
            base = root / "water"
            static_root = root / "static/water"
            static_root.mkdir(parents=True)
            source = {"model_sha256": "public-model", "potential_sha256": "unit-potential"}
            engine = {"gpumd": {"sha256": "unit-gpumd"}}
            measurements = {}
            snapshots = []
            for seed in prepare.CALIBRATION_SEEDS:
                trial = f"calibration-{seed}"
                directory = base / trial
                directory.mkdir(parents=True)
                snapshots.append(f"independent-{seed}")
                execution = directory / "execution.json"
                prepare.write_json(execution, {"snapshot": {"sha256": snapshots[-1]}})
                original = Path(tmp) / "old-root" / trial
                original.mkdir(parents=True)
                (static_root / trial).symlink_to(original, target_is_directory=True)
                measurement = original / "measurement.json"
                prepare.write_json(measurement, {
                    "trial": trial, "material": "water", "engines": engine,
                    "source": {"model": snapshots[-1], "potential": source["potential_sha256"]},
                    "trial_evidence": {"seed": seed, "sha256": prepare.sha256(execution)},
                })
                measurements[str(measurement)] = prepare.sha256(measurement)

            library = root / "wisevolve.so"
            library.write_text("unit-test library")
            wpe = {"sha256": "unit-wpe", "library": {"path": str(library), "sha256": prepare.sha256(library)}}
            diagnosis = static_root.parent / "wpe-diagnosis.json"
            prepare.write_json(diagnosis, {
                "release_qualification_status": "PASS",
                "sha256": {"gpumd_5_9_1": "unit-gpumd", "wpe": "unit-wpe", "wpe_library": wpe["library"]["sha256"]},
            })
            freeze = static_root / "calibration.json"
            prepare.write_json(freeze, {
                "calibration_status": "PASS", "material": "water", "measurements": measurements,
                "snapshot_sha256": snapshots, "wpe_diagnosis": {"sha256": prepare.sha256(diagnosis)},
            })

            extension = base / "scr-extension/execution.json"
            extension.parent.mkdir()
            prepare.write_json(extension, {"snapshot": {"sha256": "extension-frame"}})
            holdout = static_root / "holdout/measurement.json"
            holdout.parent.mkdir()
            approval = {"sha256": "unit-selection", "created_utc": "2026-11-26T00:00:00+00:00"}
            trial_evidence = {"preparation_stage": "scr-extension", "sha256": prepare.sha256(extension),
                              "calibration_sha256": prepare.sha256(freeze), "water_selection": approval}
            candidate = {
                "material": "water", "trial": "holdout", "engines": {**engine, "wpe": wpe},
                "started_utc": "2026-11-26T00:01:00+00:00",
                "trial_evidence": trial_evidence,
                "source": {"model": "extension-frame", "potential": source["potential_sha256"]},
            }
            verification = static_root / "holdout-verification.json"
            prepare.write_json(holdout, candidate)
            prepare.write_json(verification, {
                "material": "water", "numeric_status": "PASS", "calibration_sha256": prepare.sha256(freeze),
                "holdout_measurement_sha256": prepare.sha256(holdout),
            })
            with (patch.object(handoff, "check_completed_stage",
                               side_effect=lambda job, *_: handoff.read(job / "execution.json")),
                  patch.object(handoff, "water_selection_provenance", return_value=approval)):
                args = (root, "water", source, {"sha256": "extension-frame"}, "unit-gpumd")
                self.assertEqual(handoff.check_static(*args, "scr-extension"), candidate)
                with self.assertRaisesRegex(ValueError, "held-out static verification"):
                    handoff.check_static(*args, "scr")

    def test_native_and_compatible_geometry_cannot_share_a_calibration(self):
        frame = {"material": "water", "source": {"potential": "water"}, "gpu_uuid": "test",
                 "guards": {}, "engines": {"lammps": {"geometry": "gpumd_5_9_1"}}}
        old = dict(frame, engines={"lammps": {}})
        with self.assertRaisesRegex(ValueError, "geometry changed"):
            static.compatible(old, frame)

    def test_portable_verification_uses_frozen_time_and_survives_a_move(self):
        with TemporaryDirectory() as tmp:
            original = Path(tmp) / "original"
            evidence = original / "evidence"
            evidence.mkdir(parents=True)
            engines = {name: {"sha256": name} for name in static.ENGINE_NAMES}
            engines["wpe"]["library"] = {"sha256": "unit-library"}
            quantities = {name: {"max_abs": 0.0, "max_guard_fraction": 0.0}
                          for name, _, _ in static.QUANTITIES}
            comparisons = {name: {"atoms": 1, "quantities": quantities,
                                  "total_energy_difference_per_atom_eV": 0.0}
                           for name in static.ENGINE_NAMES[1:]}
            identity = {"material": "unit", "engines": engines, "guards": {},
                        "gpu_uuid": "unit-gpu", "comparisons": comparisons}
            trace = evidence / "trace"
            trace.write_text("unit-test backend trace")
            diagnosis = evidence / "wpe-diagnosis.json"
            static.record(diagnosis, {"release_qualification_status": "PASS",
                          "sha256": {"gpumd_5_9_1": "gpumd", "wpe": "wpe", "wpe_library": "unit-library"},
                          "gpu": {"uuid": "unit-gpu"}, "material_results": {"unit": {"potential_sha256": "unit"}},
                          "evidence": {trace.name: static.sha256(trace)}})
            frames = []
            for seed in (401, 402, 403):
                execution = evidence / f"run-{seed}.json"
                static.record(execution, {"seed": seed})
                frame = evidence / f"frame-{seed}.json"
                static.record(frame, dict(identity, trial=f"calibration-{seed}",
                              source={"model": str(seed), "potential": "unit"},
                              trial_evidence={"seed": seed, "path": str(execution), "sha256": static.sha256(execution)}))
                frames.append(frame)
            frozen = evidence / "static-calibration.json"
            self.assertEqual(static.calibrate(SimpleNamespace(frames=frames, wpe_diagnosis=diagnosis,
                                                              output=frozen)), 0)
            source = evidence / "scr-run.json"
            static.record(source, {"stage": "unit-test"})
            sample = dict(identity, source={"model": "unit-final", "potential": "unit"}, trial="holdout",
                          trial_evidence={"path": str(source), "sha256": static.sha256(source),
                                          "calibration_sha256": static.sha256(frozen), "model_sha256": "unit-final"})
            frame = evidence / "static-measurement.json"
            sample["started_utc"] = json.loads(frozen.read_text())["created_utc"]
            static.record(frame, sample)
            args = SimpleNamespace(frame=frame, calibration=frozen, output=Path(tmp) / "original-result", bundle=None)
            with self.assertRaisesRegex(ValueError, "predates tolerance freeze"):
                static.verify(args)
            sample["started_utc"] = datetime.now(timezone.utc).isoformat()
            frame.write_text(json.dumps(sample))
            self.assertEqual(static.verify(args), 0)
            index = {str(p.relative_to(original)): static.sha256(p) for p in evidence.iterdir()}
            static.record(original / "manifest.json", {"evidence_sha256": index})
            moved = Path(tmp) / "moved"
            shutil.move(original, moved)
            copied_result = Path(tmp) / "moved-result"
            self.assertEqual(static.verify(SimpleNamespace(frame=None, calibration=None, output=copied_result,
                                                           bundle=moved)), 0)
            self.assertEqual(copied_result.read_bytes(), args.output.read_bytes())

    def test_relocated_evidence_is_read_from_the_package_and_rehashed(self):
        with TemporaryDirectory() as tmp:
            original = Path(tmp) / "original.json"
            original.write_text('{"trial": 401}')
            checksum = static.sha256(original)
            target = original.rename(Path(tmp) / "packed.json")
            lookup = {checksum: target}
            self.assertEqual(static.evidence_file(original, checksum, lookup), target)
            target.write_text('{"trial": 402}')
            with self.assertRaisesRegex(ValueError, "evidence changed"):
                static.evidence_file(original, checksum, lookup)


if __name__ == "__main__":
    unittest.main()
