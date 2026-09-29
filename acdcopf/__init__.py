"""Tool1 public API. Engine modules remain available under acdcpf_opf."""
__version__ = "0.3.0"
__all__ = ["ImportOptions", "ImportedNetwork", "load_custom_network", "convert_custom_network", "BenchmarkRequest", "run_benchmark_request", "custom_grid_case"]

def __getattr__(name):
    if name in __all__[:4]:
        from acdcpf_opf.data import custom_network
        return getattr(custom_network, name)
    if name in {"BenchmarkRequest", "run_benchmark_request"}:
        from acdcpf_opf.benchmarks.stagg5 import service
        return getattr(service, name)
    if name == "custom_grid_case":
        from acdcpf_opf.benchmarks.stagg5.case_variants import custom_grid_case
        return custom_grid_case
    raise AttributeError(name)
