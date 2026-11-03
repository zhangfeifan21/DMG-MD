// Standalone CPU helper for the Geyer autocorrelation estimate. Input format:
// max_lag N followed by N finite samples; output is one tau value.

#include <cmath>
#include <algorithm>
#include <iomanip>
#include <iostream>
#include <limits>
#include <string>
#include <vector>

int main()
{
  long long input_lag = -1;
  long long input_count = -1;
  if (!(std::cin >> input_lag >> input_count) || input_lag < 0 || input_count < 2 ||
      input_count > static_cast<long long>(std::numeric_limits<int>::max()) ||
      input_lag >= input_count) {
    std::cerr << "autocorrelation: expected max_lag >= 0 and 2 <= N with max_lag < N\n";
    return 2;
  }
  const int max_lag = static_cast<int>(input_lag);
  const int count = static_cast<int>(input_count);

  std::vector<double> values(static_cast<std::size_t>(count));
  long double sum = 0.0L;
  for (double& value : values) {
    if (!(std::cin >> value) || !std::isfinite(value)) {
      std::cerr << "autocorrelation: expected exactly N finite samples\n";
      return 2;
    }
    sum += static_cast<long double>(value);
  }
  std::string trailing;
  if (std::cin >> trailing) {
    std::cerr << "autocorrelation: extra input after N samples\n";
    return 2;
  }

  const long double mean = sum / static_cast<long double>(count);
  std::vector<long double> centered(static_cast<std::size_t>(count));
  long double denominator = 0.0L;
  for (int i = 0; i < count; ++i) {
    centered[static_cast<std::size_t>(i)] = static_cast<long double>(values[static_cast<std::size_t>(i)]) - mean;
    denominator += centered[static_cast<std::size_t>(i)] * centered[static_cast<std::size_t>(i)];
  }
  if (!std::isfinite(denominator)) {
    std::cerr << "autocorrelation: centered sum of squares is not finite\n";
    return 2;
  }
  if (denominator == 0.0L) {
    std::cout << "1\n";
    return 0;
  }

  const auto rho_at = [&](int lag) {
    long double covariance = 0.0L;
    for (int index = 0; index < count - lag; ++index) {
      covariance += centered[static_cast<std::size_t>(index)] *
                    centered[static_cast<std::size_t>(index + lag)];
    }
    return covariance / denominator;
  };

  long double pair_sum = 0.0L;
  for (int lag = 1; lag + 1 <= max_lag; lag += 2) {
    const long double pair = rho_at(lag) + rho_at(lag + 1);
    if (!std::isfinite(pair) || pair <= 0.0L) break;
    pair_sum += pair;
  }
  const long double tau = std::max(1.0L, 1.0L + 2.0L * pair_sum);
  if (!std::isfinite(tau)) {
    std::cerr << "autocorrelation: tau is not finite\n";
    return 2;
  }
  std::cout << std::setprecision(17) << static_cast<double>(tau) << '\n';
  return 0;
}
