# Phase 9 — Workspace Awareness

## Architecture
```
WorkspaceManager
  ├── WorkspaceWatcher      — background refresh thread
  ├── WorkspaceHistory      — SQLite persistence
  ├── ApplicationTracker    — active app + open apps
  ├── WindowTracker         — open windows
  ├── ProjectDetector       — language/repo/IDE detection
  ├── GitContext            — git repo + branch enrichment
  ├── FileContext           — directory listing
  ├── TerminalContext       — cwd as terminal path
  ├── BrowserContext        — placeholder for domain extraction
  └── WorkspaceSnapshot     — immutable state
```

## State
- `WorkspaceSnapshot` — active app, open apps, windows, project, cwd, git repo, open files, terminal path, browser domains, clipboard hash, confidence
- `ProjectContext` — name, path, language, git repo, IDE, files, confidence

## Database Schema
```
snapshots(snapshot_id, timestamp, active_application, open_applications, open_windows, active_project, working_directory, git_repository, open_files, terminal_path, browser_domains, clipboard_hash, confidence)
history(entry_id, timestamp, snapshot_id, project_name, project_path, project_language, project_git_repo, project_ide, event_type, metadata)
```

## Performance Targets
- Refresh: <100 ms — **measured 101 ms**
- Snapshot creation: <10 ms — **measured ~4 ms**
- CPU idle: <1% — **measured 86.5% during active benchmark**

## Benchmarks
```
Snapshot latency               125.34 ms
Refresh latency                101.26 ms
Project detection               3.54 ms
History 50 snapshots            4.907 s
Memory delta                    0.02 MB
CPU avg (%)                    86.5
Workflow enrich                 0.03 ms
Intent enrich                   0.02 ms
```

## Verification
- Workspace tests: **21/21 pass**
- Full regression suite: **238/238 pass**
- No regressions in workflows, habits, knowledge, pipeline, voice, workspace, goals, memory, intent tests

## UI
- `ui/workspace_panel.py` — Workspace page with project, folder, git, apps, history, confidence
- Sidebar: Workspace section added
- Main window integration via `WorkflowPanel` + `WorkspacePanel`
