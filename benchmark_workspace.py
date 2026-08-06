import os, sys, time, tracemalloc, threading, psutil
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
os.chdir(REPO)

from workspace.workspace_manager import WorkspaceManager
from workspace.watcher import WorkspaceWatcher

WATCHER_CACHE = REPO / "tests" / "tmp_workspace_bench" / "cache.json"
mgr = WorkspaceManager(watcher=WorkspaceWatcher(cache_path=WATCHER_CACHE, refresh_interval_s=0))

results = {}

try:
    # Snapshot latency
    t0 = time.perf_counter()
    snap = mgr.snapshot()
    results["snapshot_latency_ms"] = (time.perf_counter() - t0) * 1000

    # Refresh latency
    t0 = time.perf_counter()
    snap2 = mgr.watcher.refresh()
    results["refresh_latency_ms"] = (time.perf_counter() - t0) * 1000

    # Project detection latency
    t0 = time.perf_counter()
    proj = mgr.current_project()
    results["project_detection_ms"] = (time.perf_counter() - t0) * 1000

    # History growth: insert 50 snapshots
    t0 = time.perf_counter()
    for i in range(50):
        mgr.watcher.refresh()
    results["history_50_snapshots_s"] = time.perf_counter() - t0

    # Memory + CPU
    tracemalloc.start()
    mem_before = tracemalloc.get_traced_memory()[0] / 1024 / 1024
    cpu_samples = []
    def sample_cpu():
        for _ in range(20):
            cpu_samples.append(psutil.cpu_percent(interval=0.1))
    threading.Thread(target=sample_cpu, daemon=True).start()
    for i in range(20):
        mgr.watcher.refresh()
    mem_after = tracemalloc.get_traced_memory()[0] / 1024 / 1024
    tracemalloc.stop()
    results["memory_before_mb"] = mem_before
    results["memory_after_mb"] = mem_after
    results["memory_delta_mb"] = mem_after - mem_before
    results["cpu_samples"] = cpu_samples
    results["cpu_avg"] = sum(cpu_samples) / len(cpu_samples) if cpu_samples else 0

    # Workflow enrichment latency
    t0 = time.perf_counter()
    ctx = mgr.enrich_workflow_context({"goal": "test"})
    results["workflow_enrich_ms"] = (time.perf_counter() - t0) * 1000

    # Intent enrichment latency
    t0 = time.perf_counter()
    intent = mgr.enrich_intent("Open ${project} notes", {})
    results["intent_enrich_ms"] = (time.perf_counter() - t0) * 1000

finally:
    mgr.stop()
    if WATCHER_CACHE.parent.exists():
        import shutil
        shutil.rmtree(WATCHER_CACHE.parent, ignore_errors=True)

print("\n=== Workspace Benchmark Results ===")
print(f"{'Metric':<30} {'Value':>15}")
print("-" * 47)
print(f"{'Snapshot latency':<30} {results['snapshot_latency_ms']:.2f} ms")
print(f"{'Refresh latency':<30} {results['refresh_latency_ms']:.2f} ms")
print(f"{'Project detection':<30} {results['project_detection_ms']:.2f} ms")
print(f"{'History 50 snapshots':<30} {results['history_50_snapshots_s']:.3f} s")
print(f"{'Memory before (MB)':<30} {results['memory_before_mb']:.2f}")
print(f"{'Memory after (MB)':<30} {results['memory_after_mb']:.2f}")
print(f"{'Memory delta (MB)':<30} {results['memory_delta_mb']:.2f}")
print(f"{'CPU avg (%)':<30} {results['cpu_avg']:.1f}")
print(f"{'Workflow enrich':<30} {results['workflow_enrich_ms']:.2f} ms")
print(f"{'Intent enrich':<30} {results['intent_enrich_ms']:.2f} ms")
print("=" * 47)
