# clickup-daily-queue

MCP server that exposes a daily ClickUp task queue for Claude Desktop.

ClickUp's API date filters are unreliable -- they return tasks outside the requested range. This server handles the post-filtering so you get accurate results.

## Features

- **Today's tasks** -- start_date matches target date, sorted by priority
- **Overdue** -- open tasks with start_date in the past (do it or reschedule)
- **Reminders** -- due_date items from a dedicated Reminders list
- **Shutdown mode** -- includes closed tasks and expands subtasks of completed parents

## Setup

```bash
cd mcp/clickup-daily-queue
uv venv && uv pip install -e .
```

### Claude Desktop config

Add to `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "clickup-daily-queue": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/mcp/clickup-daily-queue", "mcp", "run", "server.py"],
      "env": {
        "CLICKUP_TEAM_ID": "your-team-id",
        "CLICKUP_USER_ID": "your-user-id",
        "CLICKUP_REMINDERS_LIST": "your-reminders-list-id"
      }
    }
  }
}
```

API key is read from `~/.config/clickup/api_key` or the `CLICKUP_API_KEY` environment variable.

## Usage

In Claude Desktop:

- "What's on my queue today?" → calls `daily_queue()`
- "What's on tap for Friday?" → calls `daily_queue(date="2026-03-13")`
- "Run shutdown" → calls `daily_queue(shutdown=true)`

## Why this exists

ClickUp's native MCP tools don't reliably filter by date. This server wraps proven post-filtering logic (originally from a bash script) into an MCP tool that Claude Desktop can use natively.
