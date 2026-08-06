# Phase 5 — Calendar Plugin

## Architecture
```
plugins/calendar_plugin/
  manifest.json
  plugin.py
  state.py
  provider_ics.py
  provider_google.py
  provider_outlook.py
  schedule.py
  proactive.py
  memory.py
  ui/
  config/
```

## Provider Abstraction
All providers implement the same interface:
- `get_events(start, end) -> list[CalendarEvent]`
- `create_event(event) -> CalendarEvent`
- `edit_event(event_id, updates) -> CalendarEvent`
- `delete_event(event_id) -> bool`

### ICS
Robust local `.ics` parser with folding, date filtering, recurrence, and delete-in-file.

### Google
Optional Google Calendar API integration via `google-api-python-client`, `google-auth-oauthlib`, and `google-auth-httplib2`. Configure via env:
- `GOOGLE_CLIENT_SECRETS`
- `GOOGLE_TOKEN_PATH`

### Outlook
Optional Microsoft Graph integration via `msal`. Configure via env:
- `OUTLOOK_CLIENT_ID`
- `OUTLOOK_TENANT_ID`
- `OUTLOOK_CLIENT_SECRET`
- `OUTLOOK_TOKEN_PATH`

## Capabilities
- Today's schedule
- Tomorrow's schedule
- This week's agenda
- Upcoming meetings
- Free time detection
- Event search
- Create event
- Edit event
- Delete event
- Recurring events
- Time zone support
- Proactive reminders
- Memory for frequent meetings / preferred calendars
- Recovery plan

## Workflow Integration
Workflow Manager can consume calendar data through `CalendarPlugin`:
```python
plugin = CalendarPlugin()
events = plugin.scheduler.today("ics")
free = plugin.scheduler.free_blocks("ics")
plan = plugin.recovery_plan("ics")
```

## Permissions
Calendar plugin requires `calendar` permission. `PluginPermissions.check("calendar")` is enforced by the Plugin SDK sandbox when access is mediated through the sandbox.

## Benchmarks
```
load_latency_ms:          1.42
query_latency_avg_us:   324.17
scheduler_latency_ms:    0.31
reminder_latency_avg_ms: 0.02
```

## Verification
- Calendar tests: **34/34 pass**
- Plugin SDK tests: **34/34 pass**
- Calendar benchmarks: **BENCHMARK_OK**
