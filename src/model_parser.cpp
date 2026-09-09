#include "dmgmd/model.hpp"

#include "dmgmd/input_error.hpp"
// Physical constants (TIME_UNIT_CONVERSION) come from the DMG-MD-owned
// replication of GPUMD's utilities/common.cuh in src/gpumd_compat; the
// whitespace tokenizer below is the replicated get_tokens rule.
#include "gpumd_compat/common.cuh"
#include "gpumd_compat/error.cuh"

#include <algorithm>
#include <array>
#include <cerrno>
#include <cmath>
#include <cstdlib>
#include <fstream>
#include <limits>
#include <stdexcept>
#include <string_view>
#include <unordered_map>
#include <utility>

namespace dmgmd {
namespace {

using gpumd_compat::get_tokens;

using MassEntry = std::pair<const char*, double>;

// Compatibility data copied verbatim from the pinned GPUMD
// src/model/read_xyz.cu.  Units and NEP math are source-shared; this small
// host-only default table is kept here because GPUMD does not expose it.
constexpr MassEntry kMasses[] = {
    {"H", 1.0080000000}, {"He", 4.0026020000}, {"Li", 6.9400000000},
    {"Be", 9.0121831000}, {"B", 10.8100000000}, {"C", 12.0110000000},
    {"N", 14.0070000000}, {"O", 15.9990000000}, {"F", 18.9984031630},
    {"Ne", 20.1797000000}, {"Na", 22.9897692800}, {"Mg", 24.3050000000},
    {"Al", 26.9815385000}, {"Si", 28.0850000000}, {"P", 30.9737619980},
    {"S", 32.0600000000}, {"Cl", 35.4500000000}, {"Ar", 39.9480000000},
    {"K", 39.0983000000}, {"Ca", 40.0780000000}, {"Sc", 44.9559080000},
    {"Ti", 47.8670000000}, {"V", 50.9415000000}, {"Cr", 51.9961000000},
    {"Mn", 54.9380440000}, {"Fe", 55.8450000000}, {"Co", 58.9331940000},
    {"Ni", 58.6934000000}, {"Cu", 63.5460000000}, {"Zn", 65.3800000000},
    {"Ga", 69.7230000000}, {"Ge", 72.6300000000}, {"As", 74.9215950000},
    {"Se", 78.9710000000}, {"Br", 79.9040000000}, {"Kr", 83.7980000000},
    {"Rb", 85.4678000000}, {"Sr", 87.6200000000}, {"Y", 88.9058400000},
    {"Zr", 91.2240000000}, {"Nb", 92.9063700000}, {"Mo", 95.9500000000},
    {"Tc", 98.0}, {"Ru", 101.0700000000}, {"Rh", 102.9055000000},
    {"Pd", 106.4200000000}, {"Ag", 107.8682000000}, {"Cd", 112.4140000000},
    {"In", 114.8180000000}, {"Sn", 118.7100000000}, {"Sb", 121.7600000000},
    {"Te", 127.6000000000}, {"I", 126.9044700000}, {"Xe", 131.2930000000},
    {"Cs", 132.9054519600}, {"Ba", 137.3270000000}, {"La", 138.9054700000},
    {"Ce", 140.1160000000}, {"Pr", 140.9076600000}, {"Nd", 144.2420000000},
    {"Pm", 145.0}, {"Sm", 150.3600000000}, {"Eu", 151.9640000000},
    {"Gd", 157.2500000000}, {"Tb", 158.9253500000}, {"Dy", 162.5000000000},
    {"Ho", 164.9303300000}, {"Er", 167.2590000000}, {"Tm", 168.9342200000},
    {"Yb", 173.0450000000}, {"Lu", 174.9668000000}, {"Hf", 178.4900000000},
    {"Ta", 180.9478800000}, {"W", 183.8400000000}, {"Re", 186.2070000000},
    {"Os", 190.2300000000}, {"Ir", 192.2170000000}, {"Pt", 195.0840000000},
    {"Au", 196.9665690000}, {"Hg", 200.5920000000}, {"Tl", 204.3800000000},
    {"Pb", 207.2000000000}, {"Bi", 208.9804000000}, {"Po", 210.0},
    {"At", 210.0}, {"Rn", 222.0}, {"Fr", 223.0}, {"Ra", 226.0},
    {"Ac", 227.0}, {"Th", 232.0377000000}, {"Pa", 231.0358800000},
    {"U", 238.0289100000}, {"Np", 237.0}, {"Pu", 244.0}, {"Am", 243.0},
    {"Cm", 247.0}, {"Bk", 247.0}, {"Cf", 251.0}, {"Es", 252.0},
    {"Fm", 257.0}, {"Md", 258.0}, {"No", 259.0}, {"Lr", 262.0},
};

struct Property {
  std::string name;
  char type = '\0';
  int width = 0;
  std::size_t offset = 0;
};

[[noreturn]] void fail(
    const std::string& file,
    std::size_t line,
    const std::string& text,
    std::string message)
{
  throw InputError(SourceLocation{file, line, text}, std::move(message));
}

std::vector<std::string> whitespace_tokens(const std::string& line)
{
  return get_tokens(line);
}

int parse_int(
    const std::string& token,
    const std::string& file,
    std::size_t line,
    const std::string& text,
    const char* name)
{
  char* end = nullptr;
  errno = 0;
  const long value = std::strtol(token.c_str(), &end, 0);
  if (errno != 0 || end == token.c_str() || *end != '\0' ||
      value < std::numeric_limits<int>::min() ||
      value > std::numeric_limits<int>::max()) {
    fail(file, line, text, std::string(name) + " should be an integer");
  }
  return static_cast<int>(value);
}

double parse_real(
    const std::string& token,
    const std::string& file,
    std::size_t line,
    const std::string& text,
    const char* name)
{
  char* end = nullptr;
  errno = 0;
  const double value = std::strtod(token.c_str(), &end);
  if (errno != 0 || end == token.c_str() || *end != '\0' || !std::isfinite(value)) {
    fail(file, line, text, std::string(name) + " should be a finite real number");
  }
  return value;
}

std::string lowercase(std::string value)
{
  std::transform(value.begin(), value.end(), value.begin(), [](unsigned char c) {
    return static_cast<char>(std::tolower(c));
  });
  return value;
}

std::string assignment(
    const std::string& original,
    const std::string& lowercase_line,
    const std::string& key,
    const std::string& file)
{
  const std::string needle = lowercase(key) + "=";
  std::size_t position = lowercase_line.find(needle);
  if (position == std::string::npos) {
    // GPUMD permits whitespace around '=' in this line.
    position = lowercase_line.find(lowercase(key));
    if (position == std::string::npos) {
      return {};
    }
    std::size_t cursor = position + key.size();
    while (cursor < original.size() && std::isspace(static_cast<unsigned char>(original[cursor]))) {
      ++cursor;
    }
    if (cursor >= original.size() || original[cursor] != '=') {
      return {};
    }
    position = cursor;
  } else {
    position += key.size();
  }

  std::size_t cursor = position + 1;
  while (cursor < original.size() && std::isspace(static_cast<unsigned char>(original[cursor]))) {
    ++cursor;
  }
  if (cursor >= original.size()) {
    return {};
  }
  if (original[cursor] == '"') {
    const std::size_t end = original.find('"', cursor + 1);
    if (end == std::string::npos) {
      fail(file, 2, original, "unterminated quoted value for " + key);
    }
    return original.substr(cursor + 1, end - cursor - 1);
  }
  const std::size_t end = original.find_first_of(" \t\r\n", cursor);
  return original.substr(cursor, end == std::string::npos ? end : end - cursor);
}

std::vector<Property> parse_properties(
    const std::string& schema,
    const std::string& file,
    const std::string& source_line)
{
  std::vector<std::string> tokens;
  std::size_t start = 0;
  while (true) {
    const std::size_t end = schema.find(':', start);
    tokens.push_back(schema.substr(start, end == std::string::npos ? end : end - start));
    if (end == std::string::npos) {
      break;
    }
    start = end + 1;
  }
  if (tokens.empty() || tokens.size() % 3 != 0) {
    fail(file, 2, source_line, "Properties should contain name:type:width triples");
  }

  std::vector<Property> properties;
  std::size_t offset = 0;
  for (std::size_t index = 0; index < tokens.size(); index += 3) {
    const int width = parse_int(tokens[index + 2], file, 2, source_line, "property width");
    if (width <= 0 || tokens[index + 1].empty()) {
      fail(file, 2, source_line, "property width should be > 0");
    }
    properties.push_back(
        Property{lowercase(tokens[index]), tokens[index + 1].front(), width, offset});
    offset += static_cast<std::size_t>(width);
  }
  return properties;
}

const Property* find_property(const std::vector<Property>& properties, std::string_view name)
{
  const auto iterator = std::find_if(
      properties.begin(), properties.end(), [name](const Property& property) {
        return property.name == name;
      });
  return iterator == properties.end() ? nullptr : &*iterator;
}

double default_mass(
    const std::string& symbol,
    const std::string& file,
    std::size_t line,
    const std::string& text)
{
  const auto iterator = std::find_if(std::begin(kMasses), std::end(kMasses),
                                     [&symbol](const MassEntry& entry) {
                                       return symbol == entry.first;
                                     });
  if (iterator == std::end(kMasses)) {
    fail(file, line, text, "no GPUMD default mass for species '" + symbol + "'");
  }
  return iterator->second;
}

}  // namespace

void HostAtoms::validate() const
{
  if (counts.ghost_count >
      std::numeric_limits<std::size_t>::max() - counts.owned_count) {
    throw std::logic_error("owned_count + ghost_count overflows local_count");
  }
  const std::size_t local = counts.local_count();
  if (counts.owned_count > counts.global_count) {
    throw std::logic_error("owned_count exceeds global_count");
  }
  if (global_id.size() != local || species.size() != local || type.size() != local ||
      mass.size() != local || charge.size() != local || position.size() != 3 * local ||
      velocity.size() != 3 * local) {
    throw std::logic_error("local atom array size does not match local_count");
  }
  for (const auto& labels : group_labels) {
    if (labels.size() != local) {
      throw std::logic_error("group label size does not match local_count");
    }
  }
}

PotentialMetadata load_potential_metadata(const std::string& filename)
{
  std::ifstream input(filename);
  std::string line;
  if (!input || !std::getline(input, line)) {
    fail(filename, 1, {}, "cannot open or read potential file");
  }
  const auto tokens = whitespace_tokens(line);
  if (tokens.size() < 3) {
    fail(filename, 1, line, "potential header should have at least 3 items");
  }
  static const std::array<std::string_view, 4> supported{
      "nep4", "nep5", "nep4_zbl", "nep5_zbl"};
  if (std::find(supported.begin(), supported.end(), tokens[0]) == supported.end()) {
    fail(filename, 1, line, "unsupported NEP model '" + tokens[0] + "'");
  }
  const int type_count = parse_int(tokens[1], filename, 1, line, "number of atom types");
  if (type_count <= 0 || tokens.size() != static_cast<std::size_t>(type_count + 2)) {
    fail(filename, 1, line, "potential header atom-symbol count mismatch");
  }
  return PotentialMetadata{tokens[0], {tokens.begin() + 2, tokens.end()}};
}

Model parse_model_file(const std::string& filename, const PotentialMetadata& potential)
{
  std::ifstream input(filename);
  if (!input) {
    fail(filename, 0, {}, "cannot open model.xyz");
  }

  std::string line1;
  std::string line2;
  if (!std::getline(input, line1) || !std::getline(input, line2)) {
    fail(filename, 1, line1, "model.xyz should contain at least two lines");
  }
  const auto count_tokens = whitespace_tokens(line1);
  if (count_tokens.size() != 1) {
    fail(filename, 1, line1, "the first line should have one value");
  }
  const int atom_count = parse_int(count_tokens[0], filename, 1, line1, "number of atoms");
  if (atom_count < 2) {
    fail(filename, 1, line1, "number of atoms should be >= 2");
  }

  Model model;
  const std::string line2_lower = lowercase(line2);
  const std::string pbc = assignment(line2, line2_lower, "pbc", filename);
  if (!pbc.empty()) {
    const auto flags = whitespace_tokens(pbc);
    if (flags.size() != 3) {
      fail(filename, 2, line2, "pbc should contain three T/F values");
    }
    for (std::size_t axis = 0; axis < 3; ++axis) {
      const std::string flag = lowercase(flags[axis]);
      if (flag != "t" && flag != "f") {
        fail(filename, 2, line2, "pbc should contain only T or F");
      }
      model.box.periodic[axis] = flag == "t" ? 1 : 0;
    }
  }

  const std::string lattice = assignment(line2, line2_lower, "lattice", filename);
  const auto lattice_tokens = whitespace_tokens(lattice);
  if (lattice_tokens.size() != 9) {
    fail(filename, 2, line2, "Lattice should contain nine values");
  }
  constexpr int transpose[9] = {0, 3, 6, 1, 4, 7, 2, 5, 8};
  for (int index = 0; index < 9; ++index) {
    model.box.h[transpose[index]] =
        parse_real(lattice_tokens[index], filename, 2, line2, "lattice value");
  }

  const std::string schema = assignment(line2, line2_lower, "properties", filename);
  const auto properties = parse_properties(lowercase(schema), filename, line2);
  const Property* species_property = find_property(properties, "species");
  const Property* position_property = find_property(properties, "pos");
  const Property* mass_property = find_property(properties, "mass");
  const Property* charge_property = find_property(properties, "charge");
  const Property* velocity_property = find_property(properties, "vel");
  const Property* group_property = find_property(properties, "group");
  if (species_property == nullptr || position_property == nullptr) {
    fail(filename, 2, line2, "Properties should contain species and pos");
  }

  const std::size_t local = static_cast<std::size_t>(atom_count);
  HostAtoms& atoms = model.atoms;
  atoms.counts = AtomCounts{local, local, 0};
  atoms.has_input_velocity = velocity_property != nullptr;
  atoms.global_id.resize(local);
  atoms.species.resize(local);
  atoms.type.resize(local);
  atoms.mass.resize(local);
  atoms.charge.assign(local, 0.0f);
  atoms.position.resize(3 * local);
  atoms.velocity.assign(3 * local, 0.0);
  if (group_property != nullptr) {
    atoms.group_labels.resize(static_cast<std::size_t>(group_property->width),
                              std::vector<int>(local));
  }

  std::size_t expected_columns = 0;
  for (const Property& property : properties) {
    expected_columns += static_cast<std::size_t>(property.width);
  }

  for (std::size_t atom = 0; atom < local; ++atom) {
    std::string line;
    if (!std::getline(input, line)) {
      fail(filename, atom + 3, {}, "model.xyz has fewer atom lines than declared");
    }
    const auto tokens = whitespace_tokens(line);
    if (tokens.size() != expected_columns) {
      fail(filename, atom + 3, line, "number of columns does not match Properties");
    }

    atoms.global_id[atom] = atom;
    atoms.species[atom] = tokens[species_property->offset];
    const auto symbol = std::find(
        potential.symbols.begin(), potential.symbols.end(), atoms.species[atom]);
    if (symbol == potential.symbols.end()) {
      fail(filename, atom + 3, line,
           "species '" + atoms.species[atom] + "' is not allowed by the potential");
    }
    atoms.type[atom] = static_cast<int>(symbol - potential.symbols.begin());
    for (std::size_t axis = 0; axis < 3; ++axis) {
      atoms.position[axis * local + atom] = parse_real(
          tokens[position_property->offset + axis], filename, atom + 3, line, "position");
    }
    atoms.mass[atom] = mass_property == nullptr
                           ? default_mass(atoms.species[atom], filename, atom + 3, line)
                           : parse_real(tokens[mass_property->offset], filename, atom + 3, line,
                                        "atom mass");
    if (atoms.mass[atom] <= 0.0) {
      fail(filename, atom + 3, line, "atom mass should be > 0");
    }
    if (charge_property != nullptr) {
      atoms.charge[atom] = static_cast<float>(parse_real(
          tokens[charge_property->offset], filename, atom + 3, line, "charge"));
    }
    if (velocity_property != nullptr) {
      for (std::size_t axis = 0; axis < 3; ++axis) {
        atoms.velocity[axis * local + atom] =
            parse_real(tokens[velocity_property->offset + axis], filename, atom + 3, line,
                       "velocity") *
            gpumd_compat::TIME_UNIT_CONVERSION;
      }
    }
    if (group_property != nullptr) {
      for (std::size_t method = 0; method < atoms.group_labels.size(); ++method) {
        const int label = parse_int(tokens[group_property->offset + method], filename,
                                    atom + 3, line, "group label");
        if (label < 0 || label >= atom_count) {
          fail(filename, atom + 3, line, "group label should be >= 0 and < N");
        }
        atoms.group_labels[method][atom] = label;
      }
    }
  }
  atoms.validate();
  return model;
}

}  // namespace dmgmd
