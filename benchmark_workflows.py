import os, sys, time, tracemalloc, threading, platform, psutil
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
os.chdir(REPO)

from workflows.manager import WorkflowManager
from workflows.history import WorkflowHistory
from workflows.state import WorkflowState, WorkflowStep, StepStatus
from workflows.executor import WorkflowExecutor

DB = REPO / "tests" / "tmp_workflows_bench" / "workflows.sqlite"
shutil = __import__("shutil")

# fake tool helpers
class FakeTool:
    def __init__(self, name, response=None, fail=False, delay=0):
        self.name = name
        self.enabled = True
        self._response = response
        self._fail = fail
        self._delay = delay
    def run(self, **kw):
        time.sleep(self._delay)
        if self._fail:
            raise RuntimeError("boom")
        return self._response or {"ok": True}

def make_registry(*tools):
    def run_tool(t, **kw):
        r = t.run(**kw)
        class R: pass
        obj = R(); obj.__dict__ = r
        return obj
    return SimpleNamespace(tools=list(tools), run_tool=run_tool)

def make_failing_registry(*tools):
    def run_tool(t, **kw):
        r = t.run(**kw)
        class R: pass
        obj = R(); obj.__dict__ = r
        return obj
    return SimpleNamespace(tools=list(tools), run_tool=run_tool)

results = {}
mgr = WorkflowManager(history=WorkflowHistory(DB))

try:
    # Planning latency
    t0 = time.perf_counter()
    for i in range(20):
        mgr.create(f"bench workflow {i}")
    planning = time.perf_counter() - t0
    results["planning_latency_s"] = planning
    results["planning_avg_ms"] = planning / 20 * 1000

    # Step execution latency
    registry_ok = make_registry(FakeTool("tool"))
    executor = WorkflowExecutor(tool_registry=registry_ok)
    state = WorkflowState(name="exec", steps=[WorkflowStep(tool="tool", parameters={"prompt": "hi"})])
    t0 = time.perf_counter()
    res = executor.execute(state)
    results["step_execution_s"] = time.perf_counter() - t0
    results["step_execution_status"] = res.status.name

    # Retry latency
    class FlakyTool:
        name = "tool"; enabled = True
        def __init__(self):
            self.calls = 0
        def run(self, **kw):
            self.calls += 1
            if self.calls < 2:
                raise RuntimeError("transient")
            return {"ok": True}
    flaky = FlakyTool()
    registry_flaky = SimpleNamespace(tools=[flaky], run_tool=lambda t, **kw: (lambda r: type("R", (), {"__dict__": lambda self: r})())(t.run(**kw)))
    executor_retry = WorkflowExecutor(tool_registry=registry_flaky, max_retries=2)
    state = WorkflowState(name="retry", steps=[WorkflowStep(tool="tool", parameters={"prompt": "hi"})])
    t0 = time.perf_counter()
    res = executor_retry.execute(state)
    results["retry_latency_s"] = time.perf_counter() - t0
    results["retry_status"] = res.status.name
    results["retry_count"] = state.steps[0].retry_count

    # Recovery latency (replan after failure)
    registry_fail = make_registry(FakeTool("tool", fail=True))
    executor_recover = WorkflowExecutor(tool_registry=registry_fail, max_retries=1)
    state = WorkflowState(name="recover", steps=[WorkflowStep(tool="tool", parameters={"prompt": "hi"})])
    t0 = time.perf_counter()
    res = executor_recover.execute(state)
    recovery = time.perf_counter() - t0
    results["recovery_latency_s"] = recovery
    results["recovery_status"] = res.status.name

    # Throughput
    t0 = time.perf_counter()
    for i in range(10):
        s = mgr.create(f"throughput {i}")
        mgr.run(s)
    throughput = time.perf_counter() - t0
    results["throughput_10_wf_s"] = throughput
    results["throughput_wf_per_s"] = 10 / throughput if throughput > 0 else 0

    # Memory + CPU snapshot
    tracemalloc.start()
    mem_before = tracemalloc.get_traced_memory()[0] / 1024 / 1024
    cpu_samples = []
    def sample_cpu():
        for _ in range(20):
            cpu_samples.append(psutil.cpu_percent(interval=0.1))
    threading.Thread(target=sample_cpu, daemon=True).start()
    for i in range(20):
        mgr.create(f"mem cpu {i}")
    mem_after = tracemalloc.get_traced_memory()[0] / 1024 / 1024
    tracemalloc.stop()
    results["memory_before_mb"] = mem_before
    results["memory_after_mb"] = mem_after
    results["memory_delta_mb"] = mem_after - mem_before
    results["cpu_samples"] = cpu_samples
    results["cpu_avg"] = sum(cpu_samples) / len(cpu_samples) if cpu_samples else 0

finally:
    mgr.close()
    if DB.parent.exists():
        shutil.rmtree(DB.parent, ignore_errors=True)

# Print benchmark tables
print("\n=== Workflow Benchmark Results ===")
print(f"{'Metric':<30} {'Value':>15}")
print("-" * 47)
print(f"{'Planning latency (20 workflows)':<30} {results['planning_latency_s']:.3f} s")
print(f"{'Planning avg per workflow':<30} {results['planning_avg_ms']:.2f} ms")
print(f"{'Step execution latency':<30} {results['step_execution_s']*1000:.2f} ms")
print(f"{'Step execution status':<30} {results['step_execution_status']}")
print(f"{'Retry latency (1 retry)':<30} {results['retry_latency_s']*1000:.2f} ms")
print(f"{'Retry status':<30} {results['retry_status']}")
print(f"{'Retry count':<30} {results['retry_count']}")
print(f"{'Recovery latency (failed)':<30} {results['recovery_latency_s']*1000:.2f} ms")
print(f"{'Recovery status':<30} {results['recovery_status']}")
print(f"{'Throughput (10 workflows)':<30} {results['throughput_10_wf_s']:.3f} s")
print(f"{'Throughput (wf/s)':<30} {results['throughput_wf_per_s']:.2f}")
print(f"{'Memory before (MB)':<30} {results['memory_before_mb']:.2f}")
print(f"{'Memory after (MB)':<30} {results['memory_after_mb']:.2f}")
print(f"{'Memory delta (MB)':<30} {results['memory_delta_mb']:.2f}")
print(f"{'CPU avg (%)':<30} {results['cpu_avg']:.1f}")
print("=" * 47)
