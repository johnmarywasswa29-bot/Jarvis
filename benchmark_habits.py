"""Phase 2 benchmarks: learn speed, memory, suggestion latency."""
from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
os.chdir(REPO)

from habits.habit_manager import HabitManager
from habits.habit_store import HabitStore, Habit
from habits.scorer import HabitScorer


def bench_learn(events: int) -> dict:
    store = HabitStore(REPO / "tests" / "tmp_habits_bench" / "habits.sqlite")
    mgr = HabitManager(store=store, background=False)
    t0 = time.perf_counter()
    for i in range(events):
        app = ["code", "terminal", "browser", "music", "word"][i % 5]
        mgr.record_event("app_launch", {"app": app})
    habits = mgr.learn_patterns()
    elapsed = time.perf_counter() - t0
    size = sum(f.stat().st_size for f in (REPO / "tests" / "tmp_habits_bench").rglob("*") if f.is_file())
    mgr.close()
    return {
        "events": events,
        "habits_detected": len(habits),
        "learn_s": round(elapsed, 3),
        "db_bytes": size,
    }


def bench_suggest(habits: int) -> dict:
    store = HabitStore(REPO / "tests" / "tmp_habits_bench" / "habits.sqlite")
    for i in range(habits):
        store.add_habit(Habit(name=f"h{i}", confidence=0.5, frequency=i + 1, recency=1.0))
    scorer = HabitScorer()
    habits_list = store.list_habits()
    count = len(habits_list)
    t0 = time.perf_counter()
    for _ in range(20):
        scorer.suggest(habits_list, threshold=0.3)
    elapsed = time.perf_counter() - t0
    store.close()
    return {"habits": habits, "suggestions": count, "20x_suggest_s": round(elapsed, 3)}


def main() -> int:
    import shutil
    bench_dir = REPO / "tests" / "tmp_habits_bench"
    if bench_dir.exists():
        shutil.rmtree(bench_dir)
    bench_dir.mkdir(parents=True, exist_ok=True)

    print("=== Phase 2 Habit Learning Benchmark ===")
    r1 = bench_learn(200)
    print("\nLearn:")
    for k, v in r1.items():
        print(f"  {k}: {v}")
    r2 = bench_suggest(20)
    print("\nSuggest:")
    for k, v in r2.items():
        print(f"  {k}: {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
