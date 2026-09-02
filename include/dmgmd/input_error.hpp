#pragma once

#include <cstddef>
#include <stdexcept>
#include <string>

namespace dmgmd {

struct SourceLocation {
  std::string file;
  std::size_t line = 0;
  std::string text;
};

class InputError : public std::runtime_error {
 public:
  InputError(SourceLocation location, std::string message)
      : std::runtime_error(std::move(message)),
        location_(std::move(location)) {}

  [[nodiscard]] const SourceLocation& location() const noexcept {
    return location_;
  }

 private:
  SourceLocation location_;
};

}  // namespace dmgmd
