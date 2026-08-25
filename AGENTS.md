# AGENTS.md

## Project

This repository implements a minimal single-GPU molecular dynamics
engine derived from the design study of GPUMD. The project is named
as 'DMG-MD', shortend version of 'Distributed Multi-GPU Molecular
Dynamics Runtime for Machine-Learned Interatomic Potentials'. Always 
use Madarin Chinese in your result though prompt sometimes are given
in English.

v0.1 goal:

- single NVIDIA GPU only
- NEP potential only
- minimal MD loop
- numerical validation against a pinned GPUMD reference
- no MPI
- no multi-GPU
- no additional ML potentials

## Reference repository

GPUMD is maintained separately at:

../gpumd-reference

Treat it as READ ONLY.

Never modify files under ../gpumd-reference.

Do not copy GPUMD source code into this repository unless explicitly
requested by the human maintainer.

GPUMD may be used to:

- understand algorithms
- inspect data flow
- generate reference outputs
- compare numerical results

## Architecture

Keep the v0.1 core limited to:

- DeviceBuffer
- AtomData
- Box
- NeighborList
- Potential
- NEP
- Integrator
- Simulation loop

Do not introduce factories, plugin systems, generic backends,
MPI abstractions, schedulers, or distributed runtime abstractions
in v0.1.

## Language and toolchain

- C++17
- CUDA
- CMake
- Python is allowed only for tests, validation, and analysis scripts.
- Core MD runtime must not depend on Python.

## Physics / numerical rules

Internal units must be documented and consistent.

Do not change:

- unit conventions
- precision
- cutoff interpretation
- force sign conventions
- virial conventions
- periodic-boundary behavior

without explicit human approval.

Do not optimize physics code until a correctness test exists.

## Coding rules

Prefer simple data-oriented structures.

GPU-resident simulation data should remain on GPU unless transfer
is required for initialization, output, or validation.

Avoid unnecessary host-device copies inside the timestep loop.

Keep CUDA kernels small and explicit in v0.1.

Do not perform unrelated refactors.

## Build

Configure:

    cmake -S . -B build -DCMAKE_BUILD_TYPE=Release

Build:

    cmake --build build -j

## Test

Run all tests:

    ctest --test-dir build --output-on-failure

Before declaring a task complete:

1. build succeeds
2. relevant unit tests pass
3. no unrelated files changed
4. run git diff and review the patch
5. explain numerical changes
6. if CUDA memory code changed, run compute-sanitizer when practical

## Validation

GPUMD is the numerical reference for v0.1.

Important comparisons:

- neighbor counts
- total potential energy
- per-atom force
- virial
- short MD trajectory
- NVE total-energy stability

Never weaken a numerical tolerance merely to make a failing test pass.

## Agent behavior

For non-trivial tasks:

1. inspect relevant code
2. describe the proposed change
3. identify affected tests
4. implement the smallest change
5. build
6. test
7. review git diff

If the task changes architecture, NEP mathematics, data layout,
numerical precision, or GPU ownership semantics, stop after analysis
and request human review before implementation.

Do not commit or push unless explicitly requested.
