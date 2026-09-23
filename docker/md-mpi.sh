# Canonical container environment; also installed as ../env/md-mpi.sh.
export CUDA_HOME=/usr/local/cuda
export UCX_HOME=/opt/ucx
export OMPI_HOME=/opt/openmpi
export PATH="$OMPI_HOME/bin:$UCX_HOME/bin:$CUDA_HOME/bin:$PATH"
export LD_LIBRARY_PATH="$OMPI_HOME/lib:$UCX_HOME/lib:$CUDA_HOME/lib64:${LD_LIBRARY_PATH:-}"
export OMPI_MCA_mca_base_component_path="$OMPI_HOME/lib/openmpi"
export OMPI_MCA_pml=ucx
export OMPI_MCA_coll=^hcoll
export UCX_WARN_UNUSED_ENV_VARS=n
# Bundled PMIx/PRRTE use their compiled-in component directories.
unset PMIX_MCA_mca_base_component_path PRTE_MCA_mca_base_component_path
