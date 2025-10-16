import os
import json
from pathlib import Path
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum

from aw_client import ActivityWatchClient
from aw_core.models import Event
from dotenv import load_dotenv
from garminconnect import Garmin  # type: ignore[reportMissingTypeStubs]
from typing import TypedDict, List, Dict, Optional, Tuple


class SleepLevelType(Enum):
    DEEP = 0
    LIGHT = 1
    REM = 2
    AWAKE = 3


@dataclass
class SleepLevel:
    start: datetime
    end: datetime
    level: str

    def __init__(self, startGMT: str, endGMT: str, activityLevel: int):
        self.start = datetime.strptime(startGMT, "%Y-%m-%dT%H:%M:%S.0")
        self.end = datetime.strptime(endGMT, "%Y-%m-%dT%H:%M:%S.0")
        self.level = SleepLevelType(activityLevel).name

    def __repr__(self):
        return f"SleepLevel(start={self.start}, end={self.end}, level={self.level})"


def sync_sleep_data(
    api: Garmin,
    awc: ActivityWatchClient,
    date: str,
    last_synced_utc: Optional[datetime] = None,
) -> Tuple[int, Optional[datetime]]:
    """Sync sleep data from Garmin to ActivityWatch.

    Filters out events whose end time is <= last_synced_utc.
    Returns (inserted_count, max_end_time_inserted).
    """
    sleep = api.get_sleep_data(date)
    inserted = 0
    max_end: Optional[datetime] = None
    for level in sleep["sleepLevels"]:
        sleep_level = SleepLevel(**level)
        duration_seconds = (sleep_level.end - sleep_level.start).total_seconds()
        # Compute end time
        end_time = sleep_level.start + timedelta(seconds=duration_seconds)
        # Filter if already synced
        if last_synced_utc is not None and end_time <= last_synced_utc:
            continue

        event = Event(
            timestamp=sleep_level.start,
            duration=duration_seconds,
            data={"title": f"Sleep: {sleep_level.level}"},
        )
        awc.insert_event("garmin-health", event)
        inserted += 1
        if max_end is None or end_time > max_end:
            max_end = end_time

    print(f"Synced {inserted} sleep events for {date}")
    return inserted, max_end


class AllDayEvent(TypedDict, total=False):
    startTimestampGMT: str
    endTimestampGMT: str
    duration: int  # minutes
    activityType: str


def sync_workout_data(
    api: Garmin,
    awc: ActivityWatchClient,
    date: str,
    last_synced_utc: Optional[datetime] = None,
) -> Tuple[int, Optional[datetime]]:
    """Sync workout/activity data from Garmin to ActivityWatch.

    Filters out events whose end time is <= last_synced_utc.
    Returns (inserted_count, max_end_time_inserted).
    """
    all_day_events: List[AllDayEvent] = api.get_all_day_events(date)  # type: ignore[assignment]
    inserted = 0
    max_end: Optional[datetime] = None
    for activity in all_day_events:
        # Parse the start time
        start_str = str(activity.get("startTimestampGMT", "1970-01-01T00:00:00.0"))
        start_time = datetime.strptime(start_str, "%Y-%m-%dT%H:%M:%S.0")
        # Duration is in minutes; convert to seconds
        duration_minutes = int(activity.get("duration", 0) or 0)
        duration_seconds: int = duration_minutes * 60
        end_time = start_time + timedelta(seconds=duration_seconds)

        # Filter if already synced
        if last_synced_utc is not None and end_time <= last_synced_utc:
            continue

        # Extract activity data
        activity_type: str = str(activity.get("activityType", "activity")).title()
        workout_data: Dict[str, object] = {
            "title": f"Activity: {activity_type}",
            "type": activity.get("activityType", "unknown"),
            "duration_minutes": duration_minutes,
        }

        # Remove None values
        workout_data = {k: v for k, v in workout_data.items() if v is not None}

        event = Event(
            timestamp=start_time, duration=duration_seconds, data=workout_data
        )
        awc.insert_event("garmin-health", event)
        inserted += 1
        if max_end is None or end_time > max_end:
            max_end = end_time

    print(f"Synced {inserted} activity events for {date}")
    return inserted, max_end


# -------------------- Persistent state management --------------------

ISO_FMT = "%Y-%m-%dT%H:%M:%SZ"  # UTC format with Z suffix


def _dt_to_iso(dt: datetime) -> str:
    # Treat naive datetimes as UTC
    return dt.strftime(ISO_FMT)


def _iso_to_dt(s: str) -> datetime:
    return datetime.strptime(s, ISO_FMT)


def load_state(state_path: Path) -> Dict[str, Optional[datetime]]:
    if not state_path.exists():
        return {"sleep": None, "activity": None}
    try:
        raw = json.loads(state_path.read_text())
        sleep_ts = raw.get("sleep")
        activity_ts = raw.get("activity")
        return {
            "sleep": _iso_to_dt(sleep_ts) if isinstance(sleep_ts, str) else None,
            "activity": (
                _iso_to_dt(activity_ts) if isinstance(activity_ts, str) else None
            ),
        }
    except Exception:
        # If state is corrupt, start fresh rather than crash
        return {"sleep": None, "activity": None}


def save_state(state_path: Path, state: Dict[str, Optional[datetime]]) -> None:
    sleep_dt = state.get("sleep")
    activity_dt = state.get("activity")
    data = {
        "sleep": _dt_to_iso(sleep_dt) if isinstance(sleep_dt, datetime) else None,
        "activity": (
            _dt_to_iso(activity_dt) if isinstance(activity_dt, datetime) else None
        ),
    }
    tmp = state_path.with_suffix(state_path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(state_path)


def sync_garmin_data(
    email: str,
    password: str,
    date: str | None = None,
    host: str = "localhost",
    port: int = 5600,
    days_back: int = 2,
    state_file: Optional[Path] = None,
) -> None:
    """
    Sync Garmin data to ActivityWatch

    Args:
        email: Garmin account email
        password: Garmin account password
        date: Date to sync (YYYY-MM-DD format). If None, sync a sliding window.
        host: ActivityWatch server host (default: localhost)
        port: ActivityWatch server port (default: 5600)
        days_back: When date is None, fetch this many days back including today (default: 2)
        state_file: Path to persistent state file (defaults to .aw-garmin next to script)
    """
    # Resolve state path
    state_path = state_file or Path(__file__).with_name(".aw-garmin")

    # Build list of dates to fetch
    if date is None:
        today = datetime.now(timezone.utc).date()
        dates = [
            (today - timedelta(days=offset)).strftime("%Y-%m-%d")
            for offset in reversed(range(0, max(1, days_back) + 1))
        ]
    else:
        dates = [date]

    # Login to Garmin
    print(f"Logging in to Garmin Connect...")
    api = Garmin(email, password)
    api.login()
    print("✓ Logged in successfully")

    # Connect to ActivityWatch
    print(f"Connecting to ActivityWatch on {host}:{port}...")
    awc = ActivityWatchClient("garmin-sync", host=host, port=port)
    awc.connect()

    # Create bucket if it doesn't exist
    try:
        awc.create_bucket("garmin-health", "health")
        print("✓ Created bucket 'garmin-health'")
    except Exception:
        print("✓ Using existing bucket 'garmin-health'")

    # Load last-synced state
    state = load_state(state_path)
    last_sleep = state.get("sleep")
    last_activity = state.get("activity")

    total_inserted = 0
    new_sleep_max: Optional[datetime] = last_sleep
    new_activity_max: Optional[datetime] = last_activity

    print(f"\nSyncing data for dates: {', '.join(dates)}")
    for d in dates:
        s_count, s_max = sync_sleep_data(api, awc, d, last_sleep)
        a_count, a_max = sync_workout_data(api, awc, d, last_activity)
        total_inserted += s_count + a_count
        if s_max and (new_sleep_max is None or s_max > new_sleep_max):
            new_sleep_max = s_max
        if a_max and (new_activity_max is None or a_max > new_activity_max):
            new_activity_max = a_max

    # Save updated state if advanced
    if new_sleep_max != last_sleep or new_activity_max != last_activity:
        save_state(state_path, {"sleep": new_sleep_max, "activity": new_activity_max})
        print(
            f"Updated state file {state_path.name}: "
            f"sleep={_dt_to_iso(new_sleep_max) if new_sleep_max else 'None'}, "
            f"activity={_dt_to_iso(new_activity_max) if new_activity_max else 'None'}"
        )

    print("\n✓ Sync complete!")
    print(f"  Total new events inserted: {total_inserted}")


if __name__ == "__main__":
    # Load environment variables from .env file located next to this script
    # This makes cron/systemd execution independent of the current working directory.
    load_dotenv(dotenv_path=Path(__file__).with_name(".env"))

    email = os.getenv("GARMIN_EMAIL")
    password = os.getenv("GARMIN_PASSWORD")
    host = os.getenv("AW_HOST", "localhost")
    port = int(os.getenv("AW_PORT", "5600"))
    days_back = int(os.getenv("SYNC_DAYS_BACK", "2"))

    if not email or not password:
        raise ValueError("GARMIN_EMAIL and GARMIN_PASSWORD must be set in .env file")

    sync_garmin_data(
        email=email,
        password=password,
        date=None,  # Sliding window
        host=host,
        port=port,
        days_back=days_back,
        state_file=Path(__file__).with_name(".aw-garmin"),
    )
