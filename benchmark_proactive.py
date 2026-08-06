import os, sys, time, tracemalloc, threading, psutil
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
os.chdir(REPO)

from proactive.proactive_manager import ProactiveManager
from proactive.history import ProactiveHistory

DB = REPO / "tests" / "tmp_proactive_bench" / "proactive.sqlite"
mgr = ProactiveManager(history=ProactiveHistory(DB))
mgr.context_analyzer.workspace_manager = SimpleNamespace(
    snapshot=lambda: SimpleNamespace(active_application='code.exe', active_project='bench', working_directory='/tmp', git_repository='/tmp', open_applications=['code.exe'], confidence=0.5),
    current_project=lambda: SimpleNamespace(name='bench', path='/tmp', language='Python', git_repo='/tmp', ide=''),
)

try:
    mgr.start()

    # Suggestion latency
    t0 = time.perf_counter()
    suggestions = mgr.analyze("hello")
    latency = time.perf_counter() - t0
    print(f"analyze latency: {latency*1000:.2f} ms")
    print(f"suggestions generated: {len(suggestions)}")

    # notify latency
    t0 = time.perf_counter()
    notifications = mgr.notify(limit=3)
    print(f"notify latency: {(time.perf_counter()-t0)*1000:.2f} ms")
    print(f"notifications: {len(notifications)}")

    # repeated analyze
    t0 = time.perf_counter()
    for i in range(20):
        mgr.analyze("repeat benchmark")
    print(f"20x analyze: {(time.perf_counter()-t0)*1000:.2f} ms")

    # memory + CPU
    tracemalloc.start()
    mem_before = tracemalloc.get_traced_memory()[0] / 1024 / 1024
    cpu_samples = []
    def sample_cpu():
        for _ in range(20):
            cpu_samples.append(psutil.cpu_percent(interval=0.1))
    threading.Thread(target=sample_cpu, daemon=True).start()
    for i in range(20):
        mgr.analyze(f"bench {i}")
    mem_after = tracemalloc.get_traced_memory()[0] / 1024 / 1024
    tracemalloc.stop()
    print(f"memory before: {mem_before:.2f} MB")
    print(f"memory after: {mem_after:.2f} MB")
    print(f"cpu avg: {sum(cpu_samples)/len(cpu_samples):.1f}%" if cpu_samples else "cpu avg: n/a")

    # dismissal behavior
    if suggestions:
        mgr.dismiss(suggestions[0])
        print("dismissal: ok")

    print("BENCHMARK OK")
finally:
    mgr.close()
    if DB.parent.exists():
        import shutil
        shutil.rmtree(DB.parent, ignore_errors=True)
