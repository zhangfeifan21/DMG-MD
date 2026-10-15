#include "gpumd_compat/domain_partition.hpp"
#include <algorithm>
#include <iostream>
#include <set>

using namespace gpumd_compat;
static void require(bool b) { if (!b) throw std::runtime_error("partition CPU assertion"); }

int main() {
  try {
    // Enumerate every undirected five-node graph, every owned prefix. An
    // independent set-union oracle includes complete candidate (skin) rows.
    for (int bits = 0; bits < 1024; ++bits) {
      std::vector<std::vector<int>> rows(5);
      int e = 0;
      for (int i = 0; i < 5; ++i) for (int j = i+1; j < 5; ++j, ++e)
        if (bits & (1 << e)) { rows[i].push_back(j); rows[j].push_back(i); }
      for (int O = 0; O <= 5; ++O) {
        auto p = partition_graph(rows, O, 5, 5);
        std::set<int> I, E;
        for (int c = 0; c < O; ++c) {
          std::set<int> closure{c};
          closure.insert(rows[c].begin(), rows[c].end());
          auto direct = closure;
          for (int j : direct) closure.insert(rows[j].begin(), rows[j].end());
          if (std::all_of(closure.begin(), closure.end(), [O](int j) { return j < O; })) {
            I.insert(c); E.insert(direct.begin(), direct.end());
          }
        }
        for (int c = 0; c < 5; ++c) {
          require(p.interior[c] == static_cast<int>(I.count(c)));
          require(p.early[c] == static_cast<int>(E.count(c)));
          const int final_producers = c < O ? p.interior[c] + !p.interior[c] : 0;
          require(final_producers == (c < O));
          require(p.early[c] + !p.early[c] == 1);
        }
      }
    }
    // Direct owned neighbors are insufficient; common auxiliary centers
    // must be produced once even if several interior forces consume them.
    auto p = partition_graph({{1}, {0,2}, {1,3}, {2}}, 3, 4, 4);
    require(p.interior == std::vector<int>({1,0,0,0}));
    require(p.early == std::vector<int>({1,1,0,0}));
    require(partition_graph({}, 0, 0, 0).early.empty());
    bool rejected = false;
    try { partition_graph({{2}}, 1, 1, 1); } catch (const std::invalid_argument&) { rejected = true; }
    require(rejected);
    DomainPartitionKey k;
    auto other = k;
    ++other.layout; require(!(k == other)); other = k;
    ++other.neighbor; require(!(k == other)); other = k;
    ++other.storage; require(!(k == other)); other = k;
    ++other.owned; require(!(k == other)); other = k;
    ++other.dependency; require(!(k == other)); other = k;
    ++other.local; require(!(k == other)); other = k;
    other.box[18] = 1; require(!(k == other));
    DomainForceTicket ticket{1, 7};
    rejected = false;
    try { ticket.late(); } catch (const std::logic_error&) { rejected = true; }
    require(rejected); ticket.early(); ticket.late();
    rejected = false;
    try { ticket.late(); } catch (const std::logic_error&) { rejected = true; }
    require(rejected);
    std::cout << "PASS partition graph: 6144 graph/prefix cases, auxiliary closure, invalid key, ticket\n";
  } catch (const std::exception& e) { std::cerr << e.what() << '\n'; return 1; }
}
