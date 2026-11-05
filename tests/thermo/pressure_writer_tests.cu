#include "runtime_internal.hpp"

#include <cstdio>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <sstream>
#include <stdexcept>
#include <string>
#include <unistd.h>
#include <vector>

namespace {

void require(bool condition, const std::string& message)
{
  if (!condition) throw std::runtime_error(message);
}

void close_enough(double actual, double expected, const std::string& label)
{
  const double tolerance = 2.0e-9 + 2.0e-12 * std::abs(expected);
  if (std::abs(actual - expected) > tolerance) {
    throw std::runtime_error(label + ": got " + std::to_string(actual) +
                             ", expected " + std::to_string(expected));
  }
}

std::vector<double> numeric_tokens(const std::string& line)
{
  std::istringstream input(line);
  std::vector<double> values;
  double value = 0.0;
  while (input >> value) values.push_back(value);
  return values;
}

}  // namespace

int main()
{
  using namespace dmgmd;
  using namespace dmgmd::detail;
  namespace fs = std::filesystem;
  try {
    const fs::path root = fs::temp_directory_path() /
        ("dmgmd-pressure-writer-" + std::to_string(static_cast<long long>(::getpid())));
    fs::create_directories(root);
    const fs::path thermo_path = root / "thermo.out";
    const fs::path xyz_path = root / "pressure.xyz";

    HostAtoms atoms;
    atoms.counts = {1, 1, 0};
    atoms.global_id = {0};
    atoms.species = {"C"};
    atoms.type = {0};
    atoms.mass = {12.0};
    atoms.position = {1.0, 2.0, 3.0};
    atoms.velocity = {0.0, 0.0, 0.0};

    BoxData input_box;
    input_box.h = {10.0, 0.0, 0.0, 0.0, 11.0, 0.0, 0.0, 0.0, 12.0};
    const Box box = make_box(input_box);

    ThermoState thermo;
    // Internal layout: [T, PE, sxx, syy, szz, sxy, sxz, syz].
    thermo.values = {300.0, -17.0, 0.11, 0.22, 0.33, 0.44, 0.55, 0.66};

    FILE* thermo_file = std::fopen(thermo_path.c_str(), "w");
    require(thermo_file != nullptr, "open thermo output");
    write_thermo_header(thermo_file, 1, atoms, 0.5);
    write_thermo_row(thermo_file, thermo, atoms, box);
    std::fclose(thermo_file);

    std::ifstream thermo_input(thermo_path);
    std::string line;
    std::string columns;
    std::vector<double> row;
    while (std::getline(thermo_input, line)) {
      if (line.rfind("# columns ", 0) == 0) columns = line;
      else if (!line.empty() && line.front() != '#') row = numeric_tokens(line);
    }
    require(columns == "# columns T KE PE sxx syy szz syz sxz sxy ax ay az bx by bz cx cy cz",
            "production thermo header column order");
    require(row.size() == 18, "production thermo row width");
    const double conversion = gpumd_compat::PRESSURE_UNIT_CONVERSION;
    const std::vector<double> expected_stress = {
        0.11 * conversion, 0.22 * conversion, 0.33 * conversion,
        0.66 * conversion, 0.55 * conversion, 0.44 * conversion};
    for (std::size_t i = 0; i < expected_stress.size(); ++i) {
      close_enough(row[3 + i], expected_stress[i], "production thermo stress column " + std::to_string(i));
    }
    close_enough(row[1], 1.5 * K_B * 300.0, "production thermo kinetic energy");
    for (std::size_t i = 0; i < expected_stress.size(); ++i) {
      close_enough(row[3 + i] / conversion, std::vector<double>{0.11, 0.22, 0.33, 0.66, 0.55, 0.44}[i],
                   "single pressure conversion");
    }

    HostSnapshot snapshot;
    snapshot.global_id = {0};
    snapshot.position = {1.0, 2.0, 3.0};
    snapshot.velocity = {0.0, 0.0, 0.0};
    snapshot.force = {0.0, 0.0, 0.0};
    snapshot.potential = {-17.0};
    snapshot.virial = {10.0, 11.0, 12.0, 13.0, 14.0, 15.0, 16.0, 17.0, 18.0};

    DumpXyzCommand xyz;
    xyz.filename = xyz_path.string();
    xyz.precision = OutputPrecision::double_precision;
    xyz.quantities.virial = true;
    write_xyz(xyz, 0, 0.0, box, atoms, snapshot, thermo);

    std::ifstream xyz_input(xyz_path);
    std::string count_line, metadata, atom_line;
    std::getline(xyz_input, count_line);
    std::getline(xyz_input, metadata);
    std::getline(xyz_input, atom_line);
    require(count_line == "1", "production dump_xyz atom count");
    require(metadata.find("Properties=species:S:1:pos:R:3:virial:R:9") != std::string::npos,
            "production dump_xyz virial schema");
    const auto virial_pos = metadata.find("virial=\"");
    require(virial_pos != std::string::npos, "production dump_xyz frame virial field");
    const auto virial_begin = virial_pos + std::string("virial=\"").size();
    const auto virial_end = metadata.find('"', virial_begin);
    const auto frame_virial = numeric_tokens(metadata.substr(virial_begin, virial_end - virial_begin));
    require(frame_virial.size() == 9, "production dump_xyz frame virial width");
    const std::vector<double> expected_frame_virial = {10.0, 13.0, 14.0, 13.0, 11.0, 15.0, 14.0, 15.0, 12.0};
    for (std::size_t i = 0; i < expected_frame_virial.size(); ++i) {
      close_enough(frame_virial[i], expected_frame_virial[i], "production frame virial mapping");
    }
    const std::vector<double> expected_atom_virial = {10.0, 13.0, 14.0, 16.0, 11.0, 15.0, 17.0, 18.0, 12.0};
    const auto atom_virial = numeric_tokens(atom_line.substr(atom_line.find(' ') + 1));
    require(atom_virial.size() == 12, "production dump_xyz per-atom virial width");
    for (std::size_t i = 0; i < expected_atom_virial.size(); ++i) {
      close_enough(atom_virial[3 + i], expected_atom_virial[i], "production per-atom virial mapping");
    }

    fs::remove_all(root);
    std::cout << "PASS pressure writer: production thermo 6-column conversion and dump_xyz 9-virial mappings\n";
    return 0;
  } catch (const std::exception& error) {
    std::cerr << "FAIL pressure writer: " << error.what() << '\n';
    return 1;
  }
}
