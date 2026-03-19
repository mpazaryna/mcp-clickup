"""
clickup-daily-queue MCP server

Exposes a daily task queue from ClickUp for Claude Desktop.
Handles the unreliable ClickUp API date filters with post-filtering.

Features:
- Tasks with start_date = target date
- Overdue tasks (start_date in past, still open)
- Reminders (due_date from a configurable Reminders list)
- Subtask expansion for completed parent tasks (shutdown mode)
- Shutdown mode for end-of-day view (includes closed tasks)

Configuration via environment variables:
- CLICKUP_API_KEY: API key (or store in ~/.config/clickup/api_key)
- CLICKUP_TEAM_ID: Workspace/team ID
- CLICKUP_USER_ID: Your user ID for assignee filtering
- CLICKUP_REMINDERS_LIST: List ID for the Reminders list
"""

import os
import json
import httpx
from datetime import datetime, timezone, timedelta
from pathlib import Path
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("clickup-daily-queue")

# --- Config ---

def get_api_key() -> str:
    key = os.environ.get("CLICKUP_API_KEY")
    if key:
        return key
    key_file = Path.home() / ".config" / "clickup" / "api_key"
    if key_file.exists():
        return key_file.read_text().strip()
    raise ValueError("Set CLICKUP_API_KEY or store in ~/.config/clickup/api_key")

def get_config() -> dict:
    return {
        "api_key": get_api_key(),
        "team_id": os.environ.get("CLICKUP_TEAM_ID", "9017822495"),
        "user_id": os.environ.get("CLICKUP_USER_ID", "192168377"),
        "reminders_list": os.environ.get("CLICKUP_REMINDERS_LIST", "901711903455"),
        "base_url": "https://api.clickup.com/api/v2",
    }

# --- API helpers ---

async def fetch(client: httpx.AsyncClient, url: str, params: dict, config: dict) -> dict:
    resp = await client.get(
        url,
        headers={"Authorization": config["api_key"]},
        params=params,
    )
    resp.raise_for_status()
    return resp.json()

def day_boundaries(date_str: str) -> tuple[int, int]:
    """Return (start_ms, end_ms) for UTC midnight boundaries of a date."""
    dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    start_ms = int(dt.timestamp() * 1000)
    end_ms = start_ms + 86400000
    return start_ms, end_ms

def priority_sort_key(task: dict) -> int:
    p = (task.get("priority") or {}).get("priority", "")
    return {"urgent": 0, "high": 1, "normal": 2, "low": 3}.get(p, 4)

def format_task(task: dict, prefix: str = "-") -> str:
    status = task.get("status", {}).get("status", "unknown")
    name = task.get("name", "untitled")
    list_name = task.get("list", {}).get("name", "")
    priority = (task.get("priority") or {}).get("priority", "")
    task_id = task.get("id", "")
    priority_str = f" · {priority}" if priority else ""
    list_str = f" ({list_name}{priority_str})" if list_name else ""
    return f"{prefix} [{status}] {name}{list_str} — {task_id}"

def filter_by_start_date(tasks: list, start_ms: int, end_ms: int) -> list:
    """Post-filter tasks where start_date falls within range."""
    result = []
    for t in tasks:
        sd = t.get("start_date")
        if sd and start_ms <= int(sd) < end_ms:
            result.append(t)
    return sorted(result, key=priority_sort_key)

def filter_overdue(tasks: list, start_ms: int) -> list:
    """Open tasks with start_date before target date."""
    result = []
    for t in tasks:
        sd = t.get("start_date")
        status = t.get("status", {}).get("status", "")
        if sd and int(sd) < start_ms and status != "complete":
            result.append(t)
    return sorted(result, key=priority_sort_key)

def filter_reminders(tasks: list, start_ms: int, end_ms: int) -> list:
    """Reminders due on or before target date, not complete."""
    result = []
    for t in tasks:
        dd = t.get("due_date")
        status = t.get("status", {}).get("status", "")
        if dd and int(dd) < end_ms and status != "complete":
            t["_overdue"] = int(dd) < start_ms
            result.append(t)
    return result

def filter_closed_today(tasks: list, start_ms: int, end_ms: int) -> list:
    """Tasks closed on target date that weren't in the start_date range."""
    result = []
    for t in tasks:
        dc = t.get("date_closed")
        sd = t.get("start_date")
        if dc and start_ms <= int(dc) < end_ms:
            # Exclude tasks already shown via start_date
            if not sd or not (start_ms <= int(sd) < end_ms):
                result.append(t)
    return result

# --- Tools ---

@mcp.tool()
async def daily_queue(date: str = "", shutdown: bool = False) -> str:
    """Get your ClickUp daily task queue.

    Shows tasks scheduled for the target date, overdue tasks, and reminders.
    Use shutdown=True at end of day to also see completed tasks and subtask details.

    Args:
        date: Target date in YYYY-MM-DD format. Defaults to today.
        shutdown: If true, includes closed tasks and expands subtasks of completed parents.
    """
    config = get_config()
    base = config["base_url"]
    team_id = config["team_id"]
    user_id = config["user_id"]

    if not date:
        date = datetime.now().strftime("%Y-%m-%d")

    start_ms, end_ms = day_boundaries(date)
    include_closed = "true" if shutdown else "false"

    output = []

    async with httpx.AsyncClient(timeout=30.0) as client:
        # 1. Tasks with start_date near target date
        data = await fetch(client, f"{base}/team/{team_id}/task", {
            "assignees[]": user_id,
            "start_date_gt": str(start_ms - 86400000),
            "start_date_lt": str(end_ms),
            "include_closed": include_closed,
            "subtasks": "true",
        }, config)

        tasks_today = filter_by_start_date(data.get("tasks", []), start_ms, end_ms)

        output.append(f"## ClickUp Tasks for {date}\n")
        for t in tasks_today:
            output.append(format_task(t))
        output.append(f"\nTotal: {len(tasks_today)} tasks")

        # 2. Overdue tasks
        overdue_data = await fetch(client, f"{base}/team/{team_id}/task", {
            "assignees[]": user_id,
            "start_date_lt": str(start_ms),
            "include_closed": "false",
            "subtasks": "true",
        }, config)

        overdue = filter_overdue(overdue_data.get("tasks", []), start_ms)
        if overdue:
            output.append("\n## Overdue\n")
            for t in overdue:
                output.append(format_task(t))
            output.append(f"\nOverdue: {len(overdue)} tasks")

        # 3. Reminders
        reminder_data = await fetch(client, f"{base}/list/{config['reminders_list']}/task", {
            "include_closed": include_closed,
        }, config)

        reminders = filter_reminders(reminder_data.get("tasks", []), start_ms, end_ms)
        if reminders:
            output.append("\n## Reminders\n")
            for t in reminders:
                overdue_tag = " (overdue)" if t.get("_overdue") else ""
                status = t.get("status", {}).get("status", "unknown")
                name = t.get("name", "untitled")
                task_id = t.get("id", "")
                output.append(f"- [{status}] {name}{overdue_tag} — {task_id}")

        # 4. Shutdown mode extras
        if shutdown:
            # Closed today
            closed_data = await fetch(client, f"{base}/team/{team_id}/task", {
                "assignees[]": user_id,
                "date_done_gt": str(start_ms - 86400000),
                "date_done_lt": str(end_ms),
                "include_closed": "true",
                "subtasks": "true",
            }, config)

            closed_today = filter_closed_today(closed_data.get("tasks", []), start_ms, end_ms)
            if closed_today:
                output.append("\n## Also Completed Today (no start_date match)\n")
                for t in closed_today:
                    output.append(format_task(t, prefix="-"))
                output.append(f"\nAdditional: {len(closed_today)} tasks")

            # Subtask expansion for completed parents
            completed_parents = [t for t in tasks_today if t.get("status", {}).get("status") == "complete"]
            if completed_parents:
                output.append("\n## Subtasks of Completed Parents\n")
                for parent in completed_parents:
                    parent_detail = await fetch(
                        client,
                        f"{base}/task/{parent['id']}",
                        {"include_subtasks": "true"},
                        config,
                    )
                    subtasks = parent_detail.get("subtasks", [])
                    if subtasks:
                        closed_count = sum(1 for s in subtasks if s.get("status", {}).get("status") == "complete")
                        output.append(f"**{parent['name']}** ({closed_count}/{len(subtasks)} subtasks closed)")
                        for s in subtasks:
                            output.append(format_task(s, prefix="  -"))
                        output.append("")

    return "\n".join(output)
