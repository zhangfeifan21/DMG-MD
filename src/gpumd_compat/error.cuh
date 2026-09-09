/*
    Copyright 2017 Zheyong Fan and GPUMD development team
    This file is part of GPUMD.
    GPUMD is free software: you can redistribute it and/or modify
    it under the terms of the GNU General Public License as published by
    the Free Software Foundation, either version 3 of the License, or
    (at your option) any later version.
    GPUMD is distributed in the hope that it will be useful,
    but WITHOUT ANY WARRANTY; without even the implied warranty of
    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
    GNU General Public License for more details.
    You should have received a copy of the GNU General Public License
    along with GPUMD.  If not, see <http://www.gnu.org/licenses/>.
*/

/*----------------------------------------------------------------------------
DMG-MD replicated GPUMD core -- DO NOT include or link ../gpumd-reference.

This directory is the DMG-MD-owned replication of the minimal GPUMD subset
that the DMG-MD runtime needs (tokenizer, Box math, GPU_Vector, neighbor
builder, Potential base, and the NEP/NEP-ZBL CUDA kernels).  It was copied
from the read-only reference checkout

    ../gpumd-reference  (commit 9d23496e41319b9e2af5221a7df6285387401d1e)

and is adapted only as documented in each file header: symbols that are
unreachable from the DMG-MD runtime are removed, includes are flattened to
"gpumd_compat/*.cuh", and everything is placed in namespace gpumd_compat.
Floating-point expressions, memory layouts, kernel launch parameters and
accumulation order are unchanged so the GPUMD golden baselines keep holding.

House rules (see AGENTS.md):
- DMG-MD sources must include "gpumd_compat/*.cuh", never a path into
  ../gpumd-reference, and the build must not compile reference sources.
- Numerics in this directory may only change together with a golden-baseline
  re-validation (tests/baseline, tests/long_nve).
------------------------------------------------------------------------------
*/

/*----------------------------------------------------------------------------
Origin file: src/utilities/error.cuh
Purpose: Input-error/check macros plus the tokenizer and token-conversion
declarations used by the NEP loader and the DMG-MD parsers.

Adaptations for this replica are listed at the bottom of this header block.
----------------------------------------------------------------------------*/

#include "gpu_macro.cuh"
#include <fstream>
#include <stdio.h>
#include <string>
#include <vector>

namespace gpumd_compat {

#pragma once

// Fatal CUDA error check used by GPU_Vector and the kernels; exits the
// process (reference error.cuh:23).

#define CHECK(call)                                                                                \
  do {                                                                                             \
    const gpuError_t error_code = call;                                                            \
    if (error_code != gpuSuccess) {                                                                \
      fprintf(stderr, "CUDA Error:\n");                                                            \
      fprintf(stderr, "    File:       %s\n", __FILE__);                                           \
      fprintf(stderr, "    Line:       %d\n", __LINE__);                                           \
      fprintf(stderr, "    Error code: %d\n", error_code);                                         \
      fprintf(stderr, "    Error text: %s\n", gpuGetErrorString(error_code));                      \
      exit(1);                                                                                     \
    }                                                                                              \
  } while (0)

#define PRINT_SCANF_ERROR(count, n, text)                                                          \
  do {                                                                                             \
    if (count != n) {                                                                              \
      fprintf(stderr, "Input Error:\n");                                                           \
      fprintf(stderr, "    File:       %s\n", __FILE__);                                           \
      fprintf(stderr, "    Line:       %d\n", __LINE__);                                           \
      fprintf(stderr, "    Error text: %s\n", text);                                               \
      exit(1);                                                                                     \
    }                                                                                              \
  } while (0)

#define PRINT_INPUT_ERROR(text)                                                                    \
  do {                                                                                             \
    fprintf(stderr, "Input Error:\n");                                                             \
    fprintf(stderr, "    File:       %s\n", __FILE__);                                             \
    fprintf(stderr, "    Line:       %d\n", __LINE__);                                             \
    fprintf(stderr, "    Error text: %s\n", text);                                                 \
    exit(1);                                                                                       \
  } while (0)

#define PRINT_KEYWORD_ERROR(keyword)                                                               \
  do {                                                                                             \
    fprintf(stderr, "Input Error:\n");                                                             \
    fprintf(stderr, "    File:       %s\n", __FILE__);                                             \
    fprintf(stderr, "    Line:       %d\n", __LINE__);                                             \
    fprintf(stderr, "    Error text: '%s' is an invalid keyword.\n", keyword);                     \
    exit(1);                                                                                       \
  } while (0)

#ifdef STRONG_DEBUG
#define GPU_CHECK_KERNEL                                                                           \
  {                                                                                                \
    CHECK(gpuGetLastError());                                                                      \
    CHECK(gpuDeviceSynchronize());                                                                 \
  }
#else
#define GPU_CHECK_KERNEL                                                                           \
  {                                                                                                \
    CHECK(gpuGetLastError());                                                                      \
  }
#endif

// NOTE(dmg-md): print_line_1/2, my_fopen and get_tokens_without_unwanted_spaces
// from the reference header are unused by the NEP path and the DMG-MD parsers
// and are therefore not replicated (reference error.cuh:78-85).
std::vector<std::string> get_tokens(const std::string& line);
std::vector<std::string> get_tokens(std::ifstream& input);
int get_int_from_token(const std::string& token, const char* filename, const int line);
double get_double_from_token(const std::string& token, const char* filename, const int line);

}  // namespace gpumd_compat
