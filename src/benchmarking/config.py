"""Backward-compatible benchmark configuration imports."""

from __future__ import annotations

from src.benchmarking.policy import (
    BenchmarkPolicy,
    DEFAULT_BENCHMARK_POLICY,
    DEFAULT_POLICY_PATH,
    default_threshold,
    load_benchmark_config,
    load_benchmark_policy,
    load_benchmark_thresholds,
    threshold_value,
)

__all__ = [
    "BenchmarkPolicy",
    "DEFAULT_BENCHMARK_POLICY",
    "DEFAULT_POLICY_PATH",
    "default_threshold",
    "load_benchmark_config",
    "load_benchmark_policy",
    "load_benchmark_thresholds",
    "threshold_value",
]
