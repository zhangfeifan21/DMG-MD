# NPT acceptance suite

`manifest.json` is the approved B2/B3 preregistration. B2 formal results must not
be opened until `freeze_b2.py` records the built candidate SHA, pinned GPUMD SHA,
all generated input hashes, baseline tolerance manifest SHA, test-code hashes, and
the three reference checkout states. The freeze file is separate from the run
evidence and is never regenerated to accommodate a result. SCR sampling remains
gated on a B3 freeze plus successful short repeat/seed, route, and negative
controls. The approved B3 matrix is P1 HostStaged and P2/P4 with both
communication backends at base dt; half dt is P1 HostStaged only. Reference jobs
are single-process, full-budget, one-segment GPUMD runs. Distinct UTC launch
seconds are conservative isolation only; GPUMD's internal seed is not observed.
The release source and pinned executable branch are verified as `system_clock`
epoch-count initialization in the B3 freeze.

The two protocol files are historical preregistration records frozen by SHA-256; keep their bytes unchanged. For current human-readable mode names, see the [MPI runtime standard](../../docs/standards/mpi-runtime.md).

The B2 Ber golden uses the same explicit-velocity `model.xyz`, `run.in`, and NEP
potential bytes for GPUMD and DMG-MD. It checks 200 thermo rows, six stress
columns, the nine box columns, sparse XYZ frames, and restart snapshots against
the existing `tests/baseline/manifest.json` tolerances. Those tolerances and the
existing golden files are immutable. The short matrix covers P1/P2/P4 with
HostStaged and CudaAware after the CUDA/MPI environment gate; 5184 atoms exercises
local-domain eligibility but must still report the replicated-full NPT-only route. Negative cases run
with timeouts and require nonzero exit, diagnostics, and no trajectory/restart
artifact.

SCR statistics collect only per-step thermo (`T`, pressure, and volume). They do
not emit per-step XYZ. Sparse output and restart semantics are covered by the
separate Ber smoke tests. The 8-seed and half-dt statistics thresholds are frozen
in the manifest; B3 is not run as part of B2.

Run offline fixture checks from the repository root in a fresh supported
environment:

```bash
source ../env/md-mpi.sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests/npt/test_manifest.py
python3 tests/npt/generate_inputs.py --output /tmp/npt-fixture-check --write
```

After the B2 build owner provides the candidate binary and build log, freeze the
formal inputs before running either engine. Use a new empty input directory and
freeze file for this one B2 candidate:

```bash
source ../env/md-mpi.sh
python3 tests/npt/freeze_b2.py \
  --candidate /tmp/dmgmd-b0-b4-20261103/build-b2/dmg-md \
  --build-log /tmp/dmgmd-b0-b4-20261103/build-b2.log \
  --input-root /tmp/dmgmd-b0-b4-20261103/npt-b2/registered-inputs \
  --freeze /tmp/dmgmd-b0-b4-20261103/npt-b2/freeze-b2.json
```

Only after checking the emitted identity and recording the freeze SHA may the
formal matrix run. Select four free physical GPUs after the environment gate and
use a fresh, nonexistent evidence directory:

```bash
source ../env/md-mpi.sh
python3 tests/npt/run_npt_ber.py \
  --candidate /tmp/dmgmd-b0-b4-20261103/build-b2/dmg-md \
  --freeze /tmp/dmgmd-b0-b4-20261103/npt-b2/freeze-b2.json \
  --devices 0,1,2,3 \
  --work-root /tmp/dmgmd-b0-b4-20261103/npt-b2/results
```

The runner is sequential to keep GPU demand bounded. It stores exact commands,
environment, input/output hashes, stdout/stderr, route messages, tolerances,
first numerical mismatch, restart/output checks, and before/after reference
state in the evidence directory. It does not write to the GPUMD, LAMMPS, or
MatPL reference checkouts. A failed comparison is preserved as a failure; neither
the manifest threshold nor the old golden is adjusted.

After build-b3 is available, create the immutable SCR input tree and execution
freeze. The freeze records the native ACF helper used by the existing Geyer
estimator, GPUMD RNG source plus `nm`/`objdump` evidence, input hashes, and
reference checkout states. Use only the allocated GPUs 4–7 after the CUDA/MPI
environment gate:

```bash
source ../env/md-mpi.sh
PYTHONDONTWRITEBYTECODE=1 python3 tests/npt/freeze_b3.py \
  --candidate /tmp/dmgmd-b0-b4-20261103/build-b3/dmg-md \
  --build-log /tmp/dmgmd-b0-b4-20261103/b3-build-final.log \
  --input-root /tmp/dmgmd-b0-b4-20261103/npt-b3/registered-inputs \
  --freeze /tmp/dmgmd-b0-b4-20261103/npt-b3/freeze-b3.json \
  --acf-helper /tmp/dmgmd-b0-b4-20261103/build-b2/tests/dmgmd_autocorrelation
```

Run B3 short controls first and retain their result directory. Formal sampling
may be sharded across GPUs and conditions after the environment gate. Each
candidate `(profile, condition, seed)` and each reference `(profile, seed)` must
occur exactly once in the selected evidence set; separate shards for the same
condition are merged only with duplicate-key rejection. Record each
GPUMD seed's actual UTC launch time/PID and serialize its launches so distinct
reference jobs cannot start in the same UTC second. A reusable environment proof
may replace a repeated four-rank gate only when candidate SHA, MPI/UCX/CUDA
fingerprint, physical GPU order/UUID/driver, and CudaAware probe status all
match. Preserve the proof path and hash in each runner result.

The candidate `DMGMD_ENSEMBLE_SEED` equals its registered initial-model seed.
GPUMD jobs run once per seed at the full profile budget; those reference files
are reused across condition records. Candidate longer looks are complete reruns
from the same initial state and fixed seed, with earlier prefixes verified
byte-for-byte before reuse. Never resume from restart or stitch segments.
`build_records.py` makes the analyzer input from hashed execution evidence. Set
`DMGMD_ACF_HELPER` to the frozen native helper when running the analyzer; do not
use the Python O(N×lag) fallback for formal budgets. No thresholds, seeds,
budgets, or bootstrap replicates are changed after observing results.

## B4 output and restart contracts

These short B4 runners are separate from the B2/B3 statistics suites. Use the
frozen candidate and input trees shown below, an unused `--work-root`, and the
allocated GPUs only. Every new shell starts with `source ../env/md-mpi.sh`.

P2/P4 output, SCR repeatability, and P2↔P4 restart checks for dense water and
BaTiO₃-ZBL:

```bash
source ../env/md-mpi.sh
python3 tests/npt/run_npt_contracts.py --run \
  --candidate /tmp/dmgmd-b0-b4-20261103/build-b3/dmg-md \
  --reference ../gpumd-reference/src/gpumd \
  --freeze /tmp/dmgmd-b0-b4-20261103/npt-b4/freeze-v7/freeze.json \
  --work-root /tmp/dmgmd-b0-b4-20261103/npt-b4/p2-p4-replay-new \
  --devices 0,1,2,3 --ranks 2,4 --backends HostStaged,CudaAware --timeout 900
```

P1 input/environment negative cases use `negative-freeze-v6`. To run the P4
all-rank seed and partition mismatch cases, add
`--devices 0,1,2,3 --collective-ranks 4 --seed-mismatch --env-mismatch
--seed-mismatch-only`; this freeze includes a hashed per-rank wrapper for
ordinary `mpirun -n 4`, not an MPMD launcher command.

```bash
source ../env/md-mpi.sh
python3 tests/npt/run_npt_contract_negatives.py --run \
  --candidate /tmp/dmgmd-b0-b4-20261103/build-b3/dmg-md \
  --reference ../gpumd-reference/src/gpumd \
  --freeze /tmp/dmgmd-b0-b4-20261103/npt-b4/negative-freeze-v6/freeze.json \
  --work-root /tmp/dmgmd-b0-b4-20261103/npt-b4/negative-replay-new \
  --devices 0,1,2,3 --collective-ranks 4 --seed-mismatch --env-mismatch \
  --seed-mismatch-only --timeout 120
```

The NEP5 short matrix uses the registered `nep5_equivalent` potential
transformation, records both NEP4/NEP5 potential hashes, and compares P1
HostStaged Berendsen against GPUMD:

```bash
source ../env/md-mpi.sh
python3 tests/npt/run_npt_nep5_contract.py --run \
  --candidate /tmp/dmgmd-b0-b4-20261103/build-b3/dmg-md \
  --reference ../gpumd-reference/src/gpumd \
  --freeze /tmp/dmgmd-b0-b4-20261103/npt-b4/nep5-freeze-v2/freeze.json \
  --work-root /tmp/dmgmd-b0-b4-20261103/npt-b4/nep5-replay-new \
  --devices 0,1,2,3 --ranks 1,2,4 --backends HostStaged,CudaAware --timeout 900
```

The focused dense-water restart check launches four 5-step new jobs: P1
checkpoint→P2 and P2 checkpoint→P1 for both NPT ensembles. It checks snapshot
shape, finite thermo, replicated-full routing, and `%g` restart serialization; it does not
assert trajectory continuity.

```bash
source ../env/md-mpi.sh
python3 tests/npt/run_npt_crossrank_restart.py --run \
  --candidate /tmp/dmgmd-b0-b4-20261103/build-b3/dmg-md \
  --freeze /tmp/dmgmd-b0-b4-20261103/npt-b4/crossrank-p1-p2-freeze-v1/freeze.json \
  --work-root /tmp/dmgmd-b0-b4-20261103/npt-b4/crossrank-replay-new \
  --devices 0,1 --timeout 600
```

The exact input/result identities and reference checkout snapshots for the
accepted B4 subset are recorded in
[`docs/status/validation/npt-validation.md#npt-output-20261103`](../../docs/status/validation/npt-validation.md#npt-output-20261103).

## Independent SCR confirmation analyzer (CPU only)

The original independent paired confirmation was approved in
[`independent-confirmation-manifest.json`](independent-confirmation-manifest.json)
and [`independent-confirmation-protocol.md`](independent-confirmation-protocol.md).
Its interrupted 63-completed/6-partial/91-planned execution remains archived.
The n3 migration uses the separately approved
[`independent-confirmation-n3-manifest.json`](independent-confirmation-n3-manifest.json)
and [successor protocol](independent-confirmation-n3-protocol.md): all 160 chains
start fresh with the isolated CUDA 12.8 reference build, preserving the original
scientific parameters and analysis. Root approved the execution freeze before
sampling. All 160 full chains completed on 2026-11-04; the reviewed guard then
claimed `analysis.start` exclusively and launched the sole frozen analysis.
It completed at 11:39 UTC with scientific status **PASS**. Primary base/half
and all four MPI bridge conditions passed; final scope and review evidence
are in the [dated result](../../docs/status/validation/npt-validation.md#npt-scr-confirmation-20261104)
and [successor index](../../docs/status/validation/npt-scr-n3-confirmation-index.json).
The
paired analyzer consumes a derived immutable records bundle and verifies its
manifest/protocol, executable, source, input, output, route, RNG-source-audit,
and reference launch identities before reading the registered thermo series.
It does not modify the original B3 result records or old analyzer.

Run the synthetic math and full 160-chain metadata-loader tests from the
repository root:

```bash
source ../env/md-mpi.sh
PYTHONDONTWRITEBYTECODE=1 python3 tests/npt/test_paired_confirmation_math.py
PYTHONDONTWRITEBYTECODE=1 python3 tests/npt/test_paired_confirmation_integration.py
```

The following CTest command is a historical invocation from the B3 scratch
tree. Its old `/tmp` path is no longer available; the legacy archive remains
under `artifacts/validation/npt-scr-gb/pre-reboot-archive/`, while the detailed
migration locator was removed. The final NPT/G-B outcome is summarized in the
[n3 result report](../../docs/status/validation/npt-validation.md#npt-scr-confirmation-20261104)
and [confirmation index](../../docs/status/validation/npt-scr-n3-confirmation-index.json).

```bash
source ../env/md-mpi.sh
cmake -S . -B /tmp/dmgmd-b0-b4-20261103/build-b3
ctest --test-dir /tmp/dmgmd-b0-b4-20261103/build-b3 \
  --output-on-failure \
  -R '^dmgmd\.npt_paired_confirmation_(math|integration)$'
```

The registered n3 one-shot analysis used the frozen analyzer, build-specific
native ACF helper, and four CPU workers. Per-job records, execution bundle,
and migration locator are not retained as review artifacts; original raw
trajectories remain in the artifact archive. The n3 guard already ran the
analyzer exactly once, so that historical invocation is not a rerun procedure.
The paired analysis result and final identity summary remain available from the
[n3 result report](../../docs/status/validation/npt-validation.md#npt-scr-confirmation-20261104)
and [confirmation index](../../docs/status/validation/npt-scr-n3-confirmation-index.json).

The analysis uses the frozen 20,000 bootstrap replicates and seed for every
variance component. Fork workers share the loaded read-only series and return
only summaries. The synthetic suite checks that the `--workers 1` serial path
matches the parallel path. Run the formal
analysis once, after the complete queue and environment audit pass root review;
do not inspect interim scientific results or add looks.

## n3 execution and migration checks

[`dispatch_scr_confirmation.py`](dispatch_scr_confirmation.py) runs each job
through the unchanged frozen runner. It validates the successor's restricted
registration differences, environment entry point, library identities, and two
four-GPU proofs. GPU 0 is excluded; ranks within a job use distinct GPUs, and
concurrent jobs lease disjoint devices. A stop file drains active full chains
before exiting. Failed or stale-running jobs require review; the dispatcher
does not retry or splice trajectories. After all 160 jobs complete it audits
the records and writes the analyzer bundle, without starting statistical analysis.

[`check_migration_numerics.py`](check_migration_numerics.py) provides separate
`prepare`, `run`, and `analyze` commands for legacy pilot seeds 101/102. Its
200-step P1 base/half repeats and P2/P4 base backend checks use the original
baseline tolerances. These diagnostic trajectories never enter confirmation
records. Pure CPU checks are:

```bash
source ../env/md-mpi.sh
python3 tests/npt/test_migration_numerics.py
python3 tests/npt/test_dispatch_scr_confirmation.py
```

Execution identities, the original PTX/driver failure, isolated reference build,
qualification results, and current confirmation state are recorded in the
[2026-11-04 NPT report](../../docs/status/validation/npt-validation.md#npt-scr-confirmation-20261104).

The two draft-refusal runner tests now create explicit temporary DRAFT fixtures;
the real approved registration remains unchanged. This deferred test-only edit
was applied after the sole analysis completed, and all six runner CPU tests
passed. The temporary source snapshot and maintenance receipt were removed under
the final-results-only retention request. The concise result is recorded in the
[NPT regression report](../../docs/status/validation/npt-validation.md#npt-regression-20261104).
