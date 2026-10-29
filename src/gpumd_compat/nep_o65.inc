// O6.5 experiment, included inside namespace gpumd_compat by nep.cu only when
// DMGMD_ENABLE_O65_DIAGNOSTICS is enabled. The target kernels above reproduce
// GPUMD src/force/nep.cu (locked 9d23496); Compact changes only center mapping.
// No physical array, neighbor row, stride or per-center arithmetic is changed.
namespace o65_detail {
namespace fs = std::filesystem;

template<class T> void write(const fs::path& path, const std::vector<T>& values)
{
  if (fs::exists(path)) throw std::runtime_error("O65 refuses overwrite: " + path.string());
  std::ofstream out(path, std::ios::binary);
  const std::uint64_t size = values.size();
  out.write(reinterpret_cast<const char*>(&size), sizeof(size));
  out.write(reinterpret_cast<const char*>(values.data()), size * sizeof(T));
  if (!out) throw std::runtime_error("O65 snapshot write failed");
}
template<class T> std::vector<T> read(const fs::path& path)
{
  std::ifstream in(path, std::ios::binary);
  std::uint64_t size = 0;
  in.read(reinterpret_cast<char*>(&size), sizeof(size));
  if (!in || fs::file_size(path) != sizeof(size) + size * sizeof(T))
    throw std::runtime_error("O65 invalid array: " + path.string());
  std::vector<T> values(size);
  in.read(reinterpret_cast<char*>(values.data()), size * sizeof(T));
  if (!in) throw std::runtime_error("O65 snapshot read failed");
  return values;
}
template<class T> void dump(const fs::path& path, const GPU_Vector<T>& values)
{ write(path, partition_download(values)); }
template<class T> void upload(const fs::path& path, GPU_Vector<T>& values)
{
  const auto host = read<T>(path);
  values.resize_reuse(host.size());
  if (!host.empty()) values.copy_from_host(host.data());
}
template<class T> void equal(const std::vector<T>& a, const std::vector<T>& b, const char* name)
{
  if (a.size() != b.size() || std::memcmp(a.data(), b.data(), a.size()*sizeof(T)))
    throw std::runtime_error(std::string("O65 bitwise mismatch: ") + name);
}
inline double milliseconds(std::chrono::steady_clock::time_point start)
{ return std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now()-start).count(); }
} // namespace o65_detail

void NEP::capture_o65(Box& box, int N, const GPU_Vector<int>& type,
  const GPU_Vector<double>& position, const GPU_Vector<double>& potential,
  const GPU_Vector<double>& force, const GPU_Vector<double>& virial,
  const GPU_Vector<unsigned long long>& gid, std::uint64_t layout,
  const DomainForceContext& context, int call)
{
  using namespace o65_detail;
  const char* destination = std::getenv("DMGMD_O65_CAPTURE_ROOT");
  const char* rank = std::getenv("OMPI_COMM_WORLD_RANK");
  if (!destination || !rank || N1 || ND1 || paramb.model_type || need_B_projection)
    throw std::runtime_error("O65 capture requires a supported domain snapshot");
  const fs::path directory = fs::path(destination)/(std::string("rank-")+rank);
  if (!fs::create_directory(directory)) throw std::runtime_error("O65 snapshot already exists");
  CHECK(cudaDeviceSynchronize()); // halo and every producer/consumer are done
  DomainPartitionKey key;
  key.layout=layout; key.neighbor=neighbor.domain_generation(); key.storage=workspace_generation_;
  key.owned=N2; key.dependency=ND2; key.local=N;
  for (int i=0; i<18; ++i) { key.box[i]=box.cpu_h[i]; key.box[i+18]=box.float_h[i]; }
  key.box[36]=box.pbc_x; key.box[37]=box.pbc_y; key.box[38]=box.pbc_z;
  DomainPartition analysis; // separate buffers: never alter the production mask
  analysis.classify(key, neighbor.domain_row_capacity(), neighbor.NN.data(), neighbor.NL.data(), gid.data());
  std::ofstream meta(directory/"meta.txt");
  meta << N << ' ' << N2 << ' ' << ND2 << ' ' << layout << ' ' << key.neighbor << ' '
       << key.storage << ' ' << context.step << ' ' << call << ' ' << paramb.MN_radial << ' '
       << paramb.MN_angular << ' ' << annmb.dim << ' '
       << (paramb.n_max_angular+1)*((paramb.L_max+1)*(paramb.L_max+1)-1) << '\n';
  write(directory/"box.bin", std::vector<Box>{box});
  dump(directory/"type.bin",type); dump(directory/"gid.bin",gid);
  dump(directory/"position.bin",position); dump(directory/"potential.bin",potential);
  dump(directory/"force.bin",force); dump(directory/"virial.bin",virial);
  dump(directory/"interior.bin",analysis.interior); dump(directory/"early.bin",analysis.early);
  dump(directory/"verlet_nn.bin",neighbor.NN); dump(directory/"verlet_nl.bin",neighbor.NL);
  dump(directory/"nn_radial.bin",nep_data.NN_radial); dump(directory/"nl_radial.bin",nep_data.NL_radial);
  dump(directory/"nn_angular.bin",nep_data.NN_angular); dump(directory/"nl_angular.bin",nep_data.NL_angular);
  dump(directory/"fp.bin",nep_data.Fp); dump(directory/"sums.bin",nep_data.sum_fxyz);
  dump(directory/"partial_x.bin",nep_data.f12x); dump(directory/"partial_y.bin",nep_data.f12y);
  dump(directory/"partial_z.bin",nep_data.f12z);
  std::cout << "O65_CAPTURE rank=" << rank << " step=" << context.step << " call=" << call
            << " layout=" << layout << " neighbor=" << key.neighbor << " workspace=" << key.storage << '\n';
}

void NEP::replay_o65(const std::string& source, const std::string& destination,
  const std::string& target, unsigned seed, int warmup, int iterations)
{
  using namespace o65_detail;
  const fs::path directory(source), output(destination);
  if ((target!="radial" && target!="partial") || warmup<1 || iterations<1 ||
      paramb.model_type || need_B_projection)
    throw std::runtime_error("O65 invalid replay configuration");
  if (!fs::create_directory(output)) throw std::runtime_error("O65 output already exists");
  int N=0, O=0, D=0, mr=0, ma=0, dim=0, sums_dim=0;
  std::uint64_t layout=0, verlet=0, workspace=0, step=0, call=0;
  std::ifstream meta(directory/"meta.txt");
  meta >> N >> O >> D >> layout >> verlet >> workspace >> step >> call >> mr >> ma >> dim >> sums_dim;
  if (!meta || O<=0 || D<O || N<D || mr!=paramb.MN_radial || ma!=paramb.MN_angular || dim!=annmb.dim)
    throw std::runtime_error("O65 incompatible snapshot dimensions/model");
  allocate_workspace(N); N1=0; N2=O; ND1=0; ND2=D;
  const auto boxes=read<Box>(directory/"box.bin");
  if (boxes.size()!=1) throw std::runtime_error("O65 invalid box");
  Box box=boxes[0];
  GPU_Vector<int> type, interior, early, all;
  GPU_Vector<unsigned long long> gid;
  GPU_Vector<double> position, potential, force, virial;
  upload(directory/"type.bin",type); upload(directory/"gid.bin",gid);
  upload(directory/"position.bin",position); upload(directory/"potential.bin",potential);
  upload(directory/"force.bin",force); upload(directory/"virial.bin",virial);
  upload(directory/"interior.bin",interior); upload(directory/"early.bin",early);
  upload(directory/"nn_radial.bin",nep_data.NN_radial); upload(directory/"nl_radial.bin",nep_data.NL_radial);
  upload(directory/"nn_angular.bin",nep_data.NN_angular); upload(directory/"nl_angular.bin",nep_data.NL_angular);
  upload(directory/"fp.bin",nep_data.Fp); upload(directory/"sums.bin",nep_data.sum_fxyz);
  upload(directory/"partial_x.bin",nep_data.f12x); upload(directory/"partial_y.bin",nep_data.f12y);
  upload(directory/"partial_z.bin",nep_data.f12z);
  const auto captured_force=partition_download(force), captured_virial=partition_download(virial);
  const auto captured_pe=partition_download(potential);
  const auto captured_x=partition_download(nep_data.f12x), captured_y=partition_download(nep_data.f12y),
             captured_z=partition_download(nep_data.f12z);
  const auto fp=partition_download(nep_data.Fp), sums=partition_download(nep_data.sum_fxyz);
  const auto angular_counts=partition_download(nep_data.NN_angular);
  const bool radial=target=="radial";
  const int count=radial ? O : D;
  auto& mask=radial ? interior : early;
  all.resize_reuse(N,1);
  GPU_Vector<int> indices[2];
  int selected[2]{};
  // Prototype maintenance cost includes blocking mask download, host list
  // construction, allocations and upload. It is excluded from kernel timing.
  const auto maintenance_start=std::chrono::steady_clock::now();
  const auto host_mask=partition_download(mask);
  std::vector<int> host_indices[2];
  for (int c=0; c<count; ++c) host_indices[host_mask[c] ? 0 : 1].push_back(c);
  for (int p=0;p<2;++p) {
    selected[p]=host_indices[p].size();
    indices[p].resize_reuse(std::max(1,selected[p]));
    if (selected[p]) indices[p].copy_from_host(host_indices[p].data(), selected[p]);
  }
  CHECK(cudaDeviceSynchronize());
  const double maintenance_ms=milliseconds(maintenance_start);
  DomainPartitionKey index_key;
  index_key.layout=layout; index_key.neighbor=verlet; index_key.storage=workspace;
  index_key.owned=O; index_key.dependency=D; index_key.local=N;
  for (int i=0;i<18;++i) { index_key.box[i]=box.cpu_h[i]; index_key.box[18+i]=box.float_h[i]; }
  index_key.box[36]=box.pbc_x; index_key.box[37]=box.pbc_y; index_key.box[38]=box.pbc_z;
  const auto frozen_index_key=index_key;
  auto cache_hit=[&](const DomainPartitionKey& next) { return next==frozen_index_key; };
  // Exercise every generation/shape/box invalidation. Same-shape gid reorders
  // require a new layout generation, as in the production partition contract.
  for (int field=0;field<7;++field) {
    auto next=index_key;
    if (field==0) ++next.layout;
    if (field==1) ++next.neighbor;
    if (field==2) ++next.storage;
    if (field==3) ++next.owned;
    if (field==4) ++next.dependency;
    if (field==5) ++next.local;
    if (field==6) next.box[0]+=1;
    if (cache_hit(next)) throw std::runtime_error("O65 stale compact index cache key");
  }
  double cache_lookup_ms=0;

  auto launch=[&](const int* selection, bool complement, const int* index, int active, bool compact) {
    const int grid=((compact ? active : count)+63)/64;
    if (!grid) return;
    if (radial) {
#define O65_RADIAL_ARGS paramb, annmb, N, 0, O, box, nep_data.NN_radial.data(), nep_data.NL_radial.data(), type.data(), position.data(), position.data()+N, position.data()+2*static_cast<size_t>(N), nep_data.Fp.data(), false, force.data(), force.data()+N, force.data()+2*static_cast<size_t>(N), virial.data(), selection, complement
      if (compact) find_force_radial<true><<<grid,64>>>(O65_RADIAL_ARGS,index,active);
      else find_force_radial<false><<<grid,64>>>(O65_RADIAL_ARGS);
#undef O65_RADIAL_ARGS
    } else {
#define O65_PARTIAL_ARGS paramb, annmb, N, 0, D, box, nep_data.NN_angular.data(), nep_data.NL_angular.data(), type.data(), position.data(), position.data()+N, position.data()+2*static_cast<size_t>(N), nep_data.Fp.data(), nep_data.sum_fxyz.data(), nep_data.f12x.data(), nep_data.f12y.data(), nep_data.f12z.data(), selection, complement
      if (compact) find_partial_force_angular<true><<<grid,64>>>(O65_PARTIAL_ARGS,index,active);
      else find_partial_force_angular<false><<<grid,64>>>(O65_PARTIAL_ARGS);
#undef O65_PARTIAL_ARGS
    }
    GPU_CHECK_KERNEL
  };
  auto run=[&](char variant) {
    if (variant=='A') launch(nullptr,false,nullptr,0,false);
    if (variant=='B') launch(all.data(),false,nullptr,0,false);
    if (variant=='C') for (int p=0;p<2;++p) launch(mask.data(),p,nullptr,0,false);
    if (variant=='D') for (int p=0;p<2;++p) launch(nullptr,false,indices[p].data(),selected[p],true);
  };
  auto reset=[&]() {
    force.fill(0.0); virial.fill(0.0);
    // All valid edges are overwritten by partial replay. A nonzero poison
    // catches missing writes without comparing unused workspace slots.
    if (!radial) { nep_data.f12x.fill(12345.f); nep_data.f12y.fill(12345.f); nep_data.f12z.fill(12345.f); }
  };
  auto check_partial=[&]() {
    const auto x=partition_download(nep_data.f12x), y=partition_download(nep_data.f12y), z=partition_download(nep_data.f12z);
    for (int c=0;c<D;++c) for (int e=0;e<angular_counts[c];++e) {
      const size_t at=static_cast<size_t>(e)*N+c;
      if (std::memcmp(&x[at],&captured_x[at],sizeof(float)) ||
          std::memcmp(&y[at],&captured_y[at],sizeof(float)) ||
          std::memcmp(&z[at],&captured_z[at],sizeof(float)))
        throw std::runtime_error("O65 bitwise mismatch: valid angular partial");
    }
  };
  auto finish_force=[&]() {
    if (!radial) {
      find_force_radial<<<(O+63)/64,64>>>(paramb,annmb,N,0,O,box,
        nep_data.NN_radial.data(),nep_data.NL_radial.data(),type.data(),position.data(),position.data()+N,
        position.data()+2*static_cast<size_t>(N),nep_data.Fp.data(),false,
        force.data(),force.data()+N,force.data()+2*static_cast<size_t>(N),virial.data());
      GPU_CHECK_KERNEL
    }
    find_properties_many_body_domain(box,nep_data.NN_angular.data(),nep_data.NL_angular.data(),
      nep_data.f12x.data(),nep_data.f12y.data(),nep_data.f12z.data(),false,gid,N,position,force,virial);
    GPU_CHECK_KERNEL
    // The two frozen O6.5 fixtures have no ZBL. Refuse an incomplete final
    // consumer validation rather than silently accepting such a snapshot.
    if (zbl.enabled) throw std::runtime_error("O65 replay final consumer does not support ZBL");
    equal(partition_download(force),captured_force,"complete force including ghost zeros");
    equal(partition_download(virial),captured_virial,"all nine virial components");
    equal(partition_download(potential),captured_pe,"potential");
    equal(partition_download(nep_data.Fp),fp,"Fp read-only");
    equal(partition_download(nep_data.sum_fxyz),sums,"sums read-only");
  };
  std::vector<double> reference_force,reference_virial;
  for (char variant : {'A','B','C','D'}) {
    reset(); run(variant);
    if (radial) {
      auto f=partition_download(force),v=partition_download(virial);
      if (variant=='A') { reference_force=f; reference_virial=v; }
      else { equal(f,reference_force,"target radial force"); equal(v,reference_virial,"target radial virial"); }
    } else check_partial();
    finish_force();
  }
  cudaEvent_t start,stop;
  CHECK(cudaEventCreate(&start)); CHECK(cudaEventCreate(&stop));
  std::vector<char> order={'A','B','C','D'};
  std::mt19937 generator(seed); std::shuffle(order.begin(),order.end(),generator);
  std::ofstream csv(output/"timings.csv");
  csv << "variant,iteration,cuda_ms\n" << std::setprecision(9);
  for (char variant : order) {
    for (int i=-warmup;i<iterations;++i) {
      const auto lookup_start=std::chrono::steady_clock::now();
      if (!cache_hit(index_key)) throw std::runtime_error("O65 invalid compact cache");
      if (i>=0) cache_lookup_ms+=milliseconds(lookup_start);
      reset(); // deliberately outside the events, on the same default stream
      CHECK(cudaEventRecord(start)); run(variant); CHECK(cudaEventRecord(stop));
      CHECK(cudaEventSynchronize(stop));
      float ms=0; CHECK(cudaEventElapsedTime(&ms,start,stop));
      if (i>=0) csv << variant << ',' << i << ',' << ms << '\n';
    }
  }
  CHECK(cudaEventDestroy(start)); CHECK(cudaEventDestroy(stop));
  std::ofstream result(output/"result.json");
  result << std::setprecision(12) << "{\"status\":\"PASS\",\"target\":\"" << target
    << "\",\"seed\":" << seed << ",\"warmup\":" << warmup << ",\"iterations\":" << iterations
    << ",\"center_count\":" << count << ",\"early_count\":" << selected[0]
    << ",\"late_count\":" << selected[1] << ",\"compact_build_host_ms\":" << maintenance_ms
    << ",\"cache_lookup_host_ms_mean\":" << cache_lookup_ms/(4*iterations)
    << ",\"cache_invalidation_checks\":7"
    << ",\"compact_logical_bytes\":" << count*sizeof(int)
    << ",\"compact_device_bytes\":" << (indices[0].capacity()+indices[1].capacity())*sizeof(int)
    << ",\"compact_host_index_bytes\":" << (host_indices[0].capacity()+host_indices[1].capacity())*sizeof(int)
    << ",\"order\":\"";
  for (char v:order) result << v;
  result << "\",\"correctness\":\"bitwise target, valid partial, PE, final force/all virial; Fp/sums unchanged\"}\n";
}
