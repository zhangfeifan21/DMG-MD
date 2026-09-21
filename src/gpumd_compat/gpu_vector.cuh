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
Origin file: src/utilities/gpu_vector.cuh
Purpose: GPUMD's RAII device-memory container used by every replicated kernel
and by the DMG-MD runtime's device buffers; replicated verbatim.

Adaptations for this replica are listed at the bottom of this header block.
----------------------------------------------------------------------------*/

#include "error.cuh"
#include "gpu_macro.cuh"

#include <algorithm>
#include <cstdint>
#include <limits>
#include <stdexcept>

namespace gpumd_compat {

#pragma once


namespace
{
template <typename T>
void __global__ gpu_fill(const size_t size, const T value, T* data)
{
  const int i = blockDim.x * blockIdx.x + threadIdx.x;
  if (i < size)
    data[i] = value;
}
} // anonymous namespace

enum class Memory_Type {
  global = 0, // global memory, also called (linear) device memory
  managed     // managed memory, also called unified memory
};

// DMG-MD diagnostic counter: successful GPU_Vector device/managed
// allocations in this process. It counts allocations rather than resize
// calls so M2a can distinguish logical layout updates from capacity growth
// without adding synchronization.
inline std::uint64_t& gpu_vector_allocation_counter()
{
  static std::uint64_t count = 0;
  return count;
}

inline std::uint64_t gpu_vector_allocation_count()
{
  return gpu_vector_allocation_counter();
}

// RAII device-memory vector with host<->device copies and a fill kernel;
// the container behind every replicated kernel's buffers and the DMG-MD
// runtime's device arrays (reference gpu_vector.cuh:37).

template <typename T>
class GPU_Vector
{
public:
  // default constructor
  GPU_Vector()
  {
    size_ = 0;
    capacity_ = 0;
    memory_ = 0;
    memory_type_ = Memory_Type::global;
    data_ = nullptr;
    allocated_ = false;
  }

  // only allocate memory
  GPU_Vector(const size_t size, const Memory_Type memory_type = Memory_Type::global)
  {
    size_ = 0;
    capacity_ = 0;
    memory_ = 0;
    data_ = nullptr;
    allocated_ = false;
    resize(size, memory_type);
  }

  // allocate memory and initialize
  GPU_Vector(const size_t size, const T value, const Memory_Type memory_type = Memory_Type::global)
  {
    size_ = 0;
    capacity_ = 0;
    memory_ = 0;
    data_ = nullptr;
    allocated_ = false;
    resize(size, value, memory_type);
  }

  // deallocate memory
  ~GPU_Vector()
  {
    if (allocated_) {
      CHECK(gpuFree(data_));
      allocated_ = false;
    }
  }

  // only allocate memory
  void resize(const size_t size, const Memory_Type memory_type = Memory_Type::global)
  {
    size_ = size;
    capacity_ = size;
    memory_ = size_ * sizeof(T);
    memory_type_ = memory_type;
    if (allocated_) {
      CHECK(gpuFree(data_));
      allocated_ = false;
      data_ = nullptr;
    }
    if (memory_type_ == Memory_Type::global) {
      CHECK(gpuMalloc((void**)&data_, memory_));
      allocated_ = true;
    } else {
      CHECK(gpuMallocManaged((void**)&data_, memory_));
      allocated_ = true;
    }
    ++gpu_vector_allocation_counter();
  }

  // allocate memory and initialize
  void resize(const size_t size, const T value, const Memory_Type memory_type = Memory_Type::global)
  {
    size_ = size;
    capacity_ = size;
    memory_ = size_ * sizeof(T);
    memory_type_ = memory_type;
    if (allocated_) {
      CHECK(gpuFree(data_));
      allocated_ = false;
      data_ = nullptr;
    }
    if (memory_type == Memory_Type::global) {
      CHECK(gpuMalloc((void**)&data_, memory_));
      allocated_ = true;
    } else {
      CHECK(gpuMallocManaged((void**)&data_, memory_));
      allocated_ = true;
    }
    ++gpu_vector_allocation_counter();
    fill(value);
  }

  // DMG-MD M2a capacity-aware resize. Unlike the GPUMD-compatible resize()
  // above, this retains a sufficiently large allocation while always
  // updating the logical size (and therefore size()/copy/fill semantics).
  // Growth intentionally does not preserve contents, matching resize().
  void resize_reuse(const size_t size, const Memory_Type memory_type = Memory_Type::global)
  {
    if (memory_type == memory_type_ && size <= capacity_) {
      size_ = size;
      memory_ = size_ * sizeof(T);
      return;
    }
    reallocate_reuse(size, memory_type);
  }

  // Initialization is never skipped when capacity is reused.
  void resize_reuse(
    const size_t size,
    const T value,
    const Memory_Type memory_type = Memory_Type::global)
  {
    resize_reuse(size, memory_type);
    if (size_ != 0) fill(value);
  }

  // copy data from host with the default size
  void copy_from_host(const T* h_data)
  {
    CHECK(gpuMemcpy(data_, h_data, memory_, gpuMemcpyHostToDevice));
  }

  // copy data from host with a given size
  void copy_from_host(const T* h_data, const size_t size)
  {
    const size_t memory = sizeof(T) * size;
    CHECK(gpuMemcpy(data_, h_data, memory, gpuMemcpyHostToDevice));
  }

  // copy data from host with a given size and a gpu offset
  void copy_from_host(const T* h_data, const size_t size, const int offset)
  {
    const size_t memory = sizeof(T) * size;
    CHECK(gpuMemcpy(data_ + offset, h_data, memory, gpuMemcpyHostToDevice));
  }

  // copy data from device with the default size
  void copy_from_device(const T* d_data)
  {
    CHECK(gpuMemcpy(data_, d_data, memory_, gpuMemcpyDeviceToDevice));
  }

  // copy data from device with a given size
  void copy_from_device(const T* d_data, const size_t size)
  {
    const size_t memory = sizeof(T) * size;
    CHECK(gpuMemcpy(data_, d_data, memory, gpuMemcpyDeviceToDevice));
  }

  // copy data to host with the default size
  void copy_to_host(T* h_data)
  {
    CHECK(gpuMemcpy(h_data, data_, memory_, gpuMemcpyDeviceToHost));
  }

  // copy data to host with a given size
  void copy_to_host(T* h_data, const size_t size)
  {
    const size_t memory = sizeof(T) * size;
    CHECK(gpuMemcpy(h_data, data_, memory, gpuMemcpyDeviceToHost));
  }

  // copy data to host with a given size and a gpu offset
  void copy_to_host(T* h_data, const size_t size, const int offset)
  {
    const size_t memory = sizeof(T) * size;
    CHECK(gpuMemcpy(h_data, data_ + offset, memory, gpuMemcpyDeviceToHost));
  }

  // copy data to device with the default size
  void copy_to_device(T* d_data)
  {
    CHECK(gpuMemcpy(d_data, data_, memory_, gpuMemcpyDeviceToDevice));
  }

  // copy data to device with a given size
  void copy_to_device(T* d_data, const size_t size)
  {
    const size_t memory = sizeof(T) * size;
    CHECK(gpuMemcpy(d_data, data_, memory, gpuMemcpyDeviceToDevice));
  }

  // give "value" to each element
  void fill(const T value)
  {
    if (memory_type_ == Memory_Type::global) {
      const int block_size = 128;
      const int grid_size = (size_ + block_size - 1) / block_size;
      gpu_fill<<<grid_size, block_size>>>(size_, value, data_);
      GPU_CHECK_KERNEL
    } else // managed (or unified) memory
    {
      for (int i = 0; i < size_; ++i)
        data_[i] = value;
    }
  }

  // the [] operator
  T& operator[](int index) { return data_[index]; }

  // some getters
  size_t size() const { return size_; }
  size_t capacity() const { return capacity_; }
  T const* data() const { return data_; }
  T* data() { return data_; }

private:
  void reallocate_reuse(const size_t size, const Memory_Type memory_type)
  {
    const size_t max_elements = std::numeric_limits<size_t>::max() / sizeof(T);
    if (size > max_elements) {
      throw std::length_error("GPU_Vector capacity exceeds the addressable byte range");
    }
    const auto with_headroom = [max_elements](const size_t base) {
      const size_t extra = base / 4 + 1;
      return base > max_elements - extra ? max_elements : base + extra;
    };
    const size_t next_capacity =
      size == 0 ? 0 : std::max(with_headroom(capacity_), with_headroom(size));
    if (allocated_) {
      CHECK(gpuFree(data_));
      allocated_ = false;
      data_ = nullptr;
    }
    size_ = size;
    memory_ = size_ * sizeof(T);
    memory_type_ = memory_type;
    if (size == 0) {
      capacity_ = 0;
      return;
    }
    capacity_ = next_capacity;
    const size_t allocation_memory = capacity_ * sizeof(T);
    if (memory_type_ == Memory_Type::global) {
      CHECK(gpuMalloc((void**)&data_, allocation_memory));
    } else {
      CHECK(gpuMallocManaged((void**)&data_, allocation_memory));
    }
    allocated_ = true;
    ++gpu_vector_allocation_counter();
  }

  bool allocated_;          // true for allocated memory
  size_t size_;             // number of elements
  size_t capacity_;         // allocated elements; may exceed logical size_
  size_t memory_;           // memory in bytes
  Memory_Type memory_type_; // global or unified memory
  T* data_;                 // data pointer
};

}  // namespace gpumd_compat
