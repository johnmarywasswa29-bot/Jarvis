"""
Runtime dependency graph generator (P0-2 deliverable).

Emits a Mermaid diagram describing the construction order, ownership, injection,
and lifecycle of every subsystem wired by runtime.runtime.build_runtime().

Run:  python scripts/gen_runtime_graph.py
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

# (node, label, layer)
NODES = [
    ("config", "JarvisConfig", "L0 Config"),
    ("event_bus", "EventBus", "L1 Observability"),
    ("telemetry", "TelemetryManager", "L1 Observability"),
    ("event_logger", "EventLogger", "L1 Observability"),
    ("plugin_events", "PluginEvents", "L2 Plugins"),
    ("plugin_manager", "PluginManager", "L2 Plugins"),
    ("calendar_plugin", "CalendarPlugin", "L2 Plugins"),
    ("permission_manager", "PermissionManager", "L3 Tools"),
    ("tool_registry", "ToolRegistry", "L3 Tools"),
    ("memory_manager", "MemoryManager (V3)", "L4 Memory"),
    ("chat_memory", "JarvisMemoryV2", "L4 Memory"),
    ("knowledge", "KnowledgeEngine (RAG)", "L4 Memory"),
    ("intent_router", "FastIntentRouter", "L5 Intent"),
    ("intent_analyzer", "IntentAnalyzer", "L5 Intent"),
    ("habit_manager", "HabitManager", "L6 Learning"),
    ("workspace_manager", "WorkspaceManager", "L7 Workspace"),
    ("workflow_manager", "WorkflowManager", "L8 Workflow"),
    ("proactive_manager", "ProactiveManager", "L9 Proactive"),
    ("goal_manager", "GoalManager", "L10 Goals/Tasks"),
    ("task_queue", "TaskQueue", "L10 Goals/Tasks"),
    ("brain", "JarvisBrain (LLM)", "L11 Orchestration"),
]

# (from, to, label) injection edges
EDGES = [
    ("config", "event_bus", "config"),
    ("config", "telemetry", "config"),
    ("config", "event_logger", "repo/logs"),
    ("event_bus", "plugin_manager", "bridge_plugin_events"),
    ("plugin_events", "plugin_manager", "events="),
    ("config", "permission_manager", "config"),
    ("config", "tool_registry", "config+perms"),
    ("config", "memory_manager", "config"),
    ("config", "chat_memory", "config"),
    ("config", "knowledge", "knowledge_root"),
    ("tool_registry", "intent_router", "tools"),
    ("intent_router", "intent_analyzer", "router"),
    ("memory_manager", "intent_analyzer", "memory"),
    ("config", "habit_manager", "defaults"),
    ("config", "workspace_manager", "defaults"),
    ("config", "workflow_manager", "defaults"),
    ("memory_manager", "proactive_manager", "memory"),
    ("habit_manager", "proactive_manager", "habits"),
    ("knowledge", "proactive_manager", "rag"),
    ("workflow_manager", "proactive_manager", "workflow_manager"),
    ("workspace_manager", "proactive_manager", "workspace_manager"),
    ("intent_analyzer", "proactive_manager", "intent_analyzer"),
    ("goal_manager", "task_queue", "goal_manager"),
    ("config", "brain", "config+tools+memory"),
    ("plugin_manager", "calendar_plugin", "load+enable (SDK)"),
    ("memory_manager", "calendar_plugin", "PluginAPI.memory"),
    ("knowledge", "calendar_plugin", "PluginAPI.rag"),
    ("workflow_manager", "calendar_plugin", "PluginAPI.workflow_manager"),
    ("workspace_manager", "calendar_plugin", "PluginAPI.workspace_manager"),
    ("intent_analyzer", "calendar_plugin", "PluginAPI.intent_analyzer"),
    ("habit_manager", "calendar_plugin", "PluginAPI.habit_manager"),
    ("tool_registry", "calendar_plugin", "PluginAPI.tool_registry"),
    ("plugin_events", "calendar_plugin", "PluginAPI.events"),
]


def render_mermaid() -> str:
    lines = ["graph TD"]
    for nid, label, _ in NODES:
        lines.append(f'    {nid}["{label}"]')
    for a, b, label in EDGES:
        lines.append(f"    {a} -->|{label}| {b}")
    # lifecycle note
    lines.append("    %% Startup order: workspace.start(), proactive.start(), publish APP_STARTED")
    lines.append("    %% Shutdown order (reverse): proactive -> workspace -> workflow -> habit -> knowledge -> memory -> plugins")
    return "\n".join(lines)


def render_ascii() -> str:
    out = []
    out.append("Jarvis Runtime Construction Order (build_runtime)")
    out.append("=" * 60)
    layers: dict[str, list] = {}
    for nid, label, layer in NODES:
        layers.setdefault(layer, []).append((nid, label))
    for layer in sorted(layers):
        out.append(f"\n{layer}")
        for nid, label in layers[layer]:
            deps = [f"{b}<-{lab}" for a, b, lab in EDGES if b == nid]
            out.append(f"  - {label}  ({nid})")
            if deps:
                out.append(f"      injects: {', '.join(deps)}")
    return "\n".join(out)


def main() -> None:
    out_dir = REPO / "docs"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "runtime_dependency_graph.mmd").write_text(render_mermaid(), encoding="utf-8")
    (out_dir / "runtime_dependency_graph.txt").write_text(render_ascii(), encoding="utf-8")
    print(render_ascii())
    print("\nWrote docs/runtime_dependency_graph.mmd and .txt")


if __name__ == "__main__":
    main()
