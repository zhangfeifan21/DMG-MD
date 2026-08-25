#pragma once

#include "newmd/devicebuffer.cuh"

#include <cuda_runtime.h>

#include <cstddef>
#include <limits>
#include <stdexcept>
#include <type_traits>
#include <utility>

namespace newmd {

using FPFormat = double;
using AtomType = int;

enum class Axis : unsigned char {
  x = 0,
  y = 1,
  z = 2,
};

// This ordering matches GPUMD's per-atom 9N virial layout.
enum class VirialComponent : unsigned char {
  xx = 0,
  yy = 1,
  zz = 2,
  xy = 3,
  xz = 4,
  yz = 5,
  yx = 6,
  zx = 7,
  zy = 8,
};

template <typename T>
struct ScalarSoAView {
  T* base = nullptr;
  std::size_t count = 0;

  __host__ __device__ T& operator[](std::size_t atom) const noexcept {
    return base[atom];
  }

  __host__ __device__ T* data() const noexcept {
    return base;
  }
};

template <typename T>
struct VectorSoAView {
  T* base = nullptr;
  std::size_t count = 0;

  __host__ __device__ T& operator()(Axis axis,
                                    std::size_t atom) const noexcept {
    return base[static_cast<std::size_t>(axis) * count + atom];
  }

  __host__ __device__ T& x(std::size_t atom) const noexcept {
    return (*this)(Axis::x, atom);
  }

  __host__ __device__ T& y(std::size_t atom) const noexcept {
    return (*this)(Axis::y, atom);
  }

  __host__ __device__ T& z(std::size_t atom) const noexcept {
    return (*this)(Axis::z, atom);
  }

  __host__ __device__ T* component_data(Axis axis) const noexcept {
    return base + static_cast<std::size_t>(axis) * count;
  }

  __host__ __device__ T* data() const noexcept {
    return base;
  }
};

template <typename T>
struct VirialSoAView {
  T* base = nullptr;
  std::size_t count = 0;

  __host__ __device__ T& operator()(VirialComponent component,
                                    std::size_t atom) const noexcept {
    return base[static_cast<std::size_t>(component) * count + atom];
  }

  __host__ __device__ T* component_data(
      VirialComponent component) const noexcept {
    return base + static_cast<std::size_t>(component) * count;
  }

  __host__ __device__ T* data() const noexcept {
    return base;
  }
};

template <typename FPType, typename AtomTypeValue>
struct BasicAtomView {
  std::size_t count = 0;

  VectorSoAView<FPType> position;
  VectorSoAView<FPType> velocity;
  VectorSoAView<FPType> force;

  ScalarSoAView<AtomTypeValue> type;
  ScalarSoAView<FPType> mass;
  ScalarSoAView<FPType> potential_energy;
  VirialSoAView<FPType> virial;

  __host__ __device__ std::size_t size() const noexcept {
    return count;
  }

  __host__ __device__ bool empty() const noexcept {
    return count == 0;
  }
};

using AtomView = BasicAtomView<FPFormat, AtomType>;
using ConstAtomView = BasicAtomView<const FPFormat, const AtomType>;

static_assert(std::is_trivially_copyable_v<AtomView>);
static_assert(std::is_trivially_copyable_v<ConstAtomView>);

class AtomStorage {
public:
  AtomStorage() = default;

  explicit AtomStorage(std::size_t atom_count)
      : AtomStorage(atom_count, AllocateTag{}) {}

  ~AtomStorage() = default;

  AtomStorage(const AtomStorage&) = delete;
  AtomStorage& operator=(const AtomStorage&) = delete;

  AtomStorage(AtomStorage&& other) noexcept
      : atom_count_(std::exchange(other.atom_count_, 0)),
        position_(std::move(other.position_)),
        velocity_(std::move(other.velocity_)),
        force_(std::move(other.force_)),
        type_(std::move(other.type_)),
        mass_(std::move(other.mass_)),
        potential_energy_(std::move(other.potential_energy_)),
        virial_(std::move(other.virial_)) {}

  AtomStorage& operator=(AtomStorage&& other) noexcept {
    if (this != &other) {
      position_ = std::move(other.position_);
      velocity_ = std::move(other.velocity_);
      force_ = std::move(other.force_);
      type_ = std::move(other.type_);
      mass_ = std::move(other.mass_);
      potential_energy_ = std::move(other.potential_energy_);
      virial_ = std::move(other.virial_);
      atom_count_ = std::exchange(other.atom_count_, 0);
    }
    return *this;
  }

  [[nodiscard]] std::size_t size() const noexcept {
    return atom_count_;
  }

  [[nodiscard]] bool empty() const noexcept {
    return atom_count_ == 0;
  }

  void resize(std::size_t atom_count) {
    if (atom_count == atom_count_) {
      return;
    }

    AtomStorage replacement(atom_count);
    *this = std::move(replacement);
  }

  void clear() {
    resize(0);
  }

  void copy_positions_from_host(const FPFormat* source) {
    position_.copy_from_host(source, component_count(atom_count_, 3));
  }

  void copy_positions_to_host(FPFormat* destination) const {
    position_.copy_to_host(destination, component_count(atom_count_, 3));
  }

  [[nodiscard]] AtomView view() noexcept {
    return AtomView{
        atom_count_,
        {position_.data(), atom_count_},
        {velocity_.data(), atom_count_},
        {force_.data(), atom_count_},
        {type_.data(), atom_count_},
        {mass_.data(), atom_count_},
        {potential_energy_.data(), atom_count_},
        {virial_.data(), atom_count_},
    };
  }

  [[nodiscard]] ConstAtomView view() const noexcept {
    return ConstAtomView{
        atom_count_,
        {position_.data(), atom_count_},
        {velocity_.data(), atom_count_},
        {force_.data(), atom_count_},
        {type_.data(), atom_count_},
        {mass_.data(), atom_count_},
        {potential_energy_.data(), atom_count_},
        {virial_.data(), atom_count_},
    };
  }

private:
  struct AllocateTag {};

  static std::size_t component_count(std::size_t atom_count,
                                     std::size_t components) {
    if (atom_count > std::numeric_limits<std::size_t>::max() / components) {
      throw std::length_error("AtomStorage size exceeds addressable range.");
    }
    return atom_count * components;
  }

  AtomStorage(std::size_t atom_count, AllocateTag)
      : atom_count_(atom_count),
        position_(component_count(atom_count, 3)),
        velocity_(component_count(atom_count, 3)),
        force_(component_count(atom_count, 3)),
        type_(atom_count),
        mass_(atom_count),
        potential_energy_(atom_count),
        virial_(component_count(atom_count, 9)) {}

  std::size_t atom_count_ = 0;

  DeviceBuffer<FPFormat> position_;
  DeviceBuffer<FPFormat> velocity_;
  DeviceBuffer<FPFormat> force_;
  DeviceBuffer<AtomType> type_;
  DeviceBuffer<FPFormat> mass_;
  DeviceBuffer<FPFormat> potential_energy_;
  DeviceBuffer<FPFormat> virial_;
};

}  // namespace newmd
