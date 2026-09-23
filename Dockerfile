# CUDA 12.8 GA keeps the native Linux driver baseline at 570.26.
FROM nvidia/cuda:12.8.0-devel-ubuntu22.04

SHELL ["/bin/bash", "-o", "pipefail", "-c"]
ARG BUILD_JOBS=4
ARG CUDA_ARCHITECTURES="75;80;86;89;90"
ARG DMGMD_UID=1000
ARG DMGMD_GID=1000

# Do not install distro MPI/UCX/PMIx: build one coherent CUDA-aware stack.
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        build-essential gcc-11 g++-11 ca-certificates curl bzip2 pkg-config \
        python3 python3-pip libnuma-dev libibverbs-dev librdmacm-dev \
    && rm -rf /var/lib/apt/lists/* \
    && python3 -m pip install --no-cache-dir cmake==3.28.4

ENV CUDA_HOME=/usr/local/cuda \
    UCX_HOME=/opt/ucx \
    OMPI_HOME=/opt/openmpi \
    CC=gcc-11 CXX=g++-11 CUDAHOSTCXX=g++-11

# Official release archives, with SHA-256 checks before extraction.
RUN curl -fsSL --retry 3 https://github.com/openucx/ucx/releases/download/v1.18.1/ucx-1.18.1.tar.gz -o /tmp/ucx.tar.gz \
    && echo '8018dd75f11b5e8d6e57dcdb5b798d2c1f000982c353efde1f3170025c6c3b4c  /tmp/ucx.tar.gz' | sha256sum -c - \
    && tar -xzf /tmp/ucx.tar.gz -C /tmp \
    && cd /tmp/ucx-1.18.1 \
    && ./configure --prefix="$UCX_HOME" --libdir="$UCX_HOME/lib" \
         --with-cuda="$CUDA_HOME" --enable-mt --without-rocm --without-gdrcopy --disable-static \
    && make -j"$BUILD_JOBS" && make install \
    && rm -rf /tmp/ucx-1.18.1 /tmp/ucx.tar.gz

RUN curl -fsSL --retry 3 https://download.open-mpi.org/release/open-mpi/v5.0/openmpi-5.0.10.tar.bz2 -o /tmp/openmpi.tar.bz2 \
    && echo '0acecc4fc218e5debdbcb8a41d182c6b0f1d29393015ed763b2a91d5d7374cc6  /tmp/openmpi.tar.bz2' | sha256sum -c - \
    && tar -xjf /tmp/openmpi.tar.bz2 -C /tmp \
    && cd /tmp/openmpi-5.0.10 \
    && ./configure --prefix="$OMPI_HOME" --libdir="$OMPI_HOME/lib" \
         --with-cuda="$CUDA_HOME" --with-cuda-libdir="$CUDA_HOME/lib64/stubs" \
         --with-ucx="$UCX_HOME" --with-pmix=internal --with-prrte=internal \
         --with-hwloc=internal --with-libevent=internal \
         --without-hcoll --without-ucc --disable-mpi-fortran --disable-static \
    && make -j"$BUILD_JOBS" && make install \
    && rm -rf /tmp/openmpi-5.0.10 /tmp/openmpi.tar.bz2

# libcuda stubs are used only by configure/linking, NEVER at runtime.
COPY docker/md-mpi.sh /opt/dmgmd/env/md-mpi.sh
COPY docker/entrypoint.sh /usr/local/bin/dmgmd-entrypoint
WORKDIR /opt/dmgmd/newmd
COPY . .
RUN source ../env/md-mpi.sh \
    && cmake -S . -B build -DCMAKE_BUILD_TYPE=Release \
         -DCMAKE_CUDA_ARCHITECTURES="$CUDA_ARCHITECTURES" \
    && cmake --build build --parallel "$BUILD_JOBS" \
    && ctest --test-dir build --output-on-failure -E domain_neighbor_cuda \
    && ln -s /opt/dmgmd/newmd/build/dmg-md /usr/local/bin/dmg-md \
    && chmod 755 /usr/local/bin/dmgmd-entrypoint \
    && test "$DMGMD_UID" -gt 0 && test "$DMGMD_GID" -gt 0 \
    && groupadd --gid "$DMGMD_GID" dmgmd \
    && useradd --uid "$DMGMD_UID" --gid "$DMGMD_GID" --create-home dmgmd \
    && mkdir /work && chown dmgmd:dmgmd /work \
    && chown -R dmgmd:dmgmd /opt/dmgmd \
    && chmod -R a+rX /opt/dmgmd

# Keep compiler and fixtures available for deployment acceptance/rebuilding.
USER dmgmd
WORKDIR /work
ENTRYPOINT ["/usr/local/bin/dmgmd-entrypoint"]
CMD ["dmg-md"]
