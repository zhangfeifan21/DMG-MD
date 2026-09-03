#include "dmgmd/partition.hpp"

#include <cstdlib>
#include <iostream>
#include <stdexcept>
#include <vector>

namespace {

void require(bool condition, const char* message)
{
  if (!condition) throw std::runtime_error(message);
}

void check_partition(std::size_t atoms, int ranks)
{
  std::vector<int> owners(atoms, 0);
  std::size_t previous_end = 0;
  std::size_t minimum = atoms;
  std::size_t maximum = 0;
  for (int rank = 0; rank < ranks; ++rank) {
    const dmgmd::OwnedRange range = dmgmd::balanced_owned_range(atoms, rank, ranks);
    require(range.begin == previous_end, "rank ranges are not contiguous");
    require(range.end <= atoms, "rank range exceeds the global atom count");
    previous_end = range.end;
    minimum = std::min(minimum, range.size());
    maximum = std::max(maximum, range.size());
    for (std::size_t atom = range.begin; atom < range.end; ++atom) ++owners[atom];
  }
  require(previous_end == atoms, "rank ranges do not cover the final atom");
  require(maximum - minimum <= 1, "balanced ranges differ by more than one atom");
  for (int owner_count : owners) {
    require(owner_count == 1, "an atom is missing an owner or has multiple owners");
  }
}

}  // namespace

int main()
{
  try {
    for (std::size_t atoms : {std::size_t{1}, std::size_t{2}, std::size_t{7},
                              std::size_t{8}, std::size_t{9}, std::size_t{1025}}) {
      for (int ranks : {1, 2, 4}) check_partition(atoms, ranks);
    }
    bool rejected = false;
    try {
      static_cast<void>(dmgmd::balanced_owned_range(8, 2, 2));
    } catch (const std::invalid_argument&) {
      rejected = true;
    }
    require(rejected, "invalid MPI rank was not rejected");
    return EXIT_SUCCESS;
  } catch (const std::exception& error) {
    std::cerr << error.what() << '\n';
    return EXIT_FAILURE;
  }
}
