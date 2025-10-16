import os
from pathlib import Path
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from aw_client import ActivityWatchClient
from aw_core.models import Event
from dotenv import load_dotenv
from garminconnect import Garmin  # type: ignore[reportMissingTypeStubs]
from typing import TypedDict, List, Dict


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


def sync_sleep_data(api: Garmin, awc: ActivityWatchClient, date: str) -> list[Event]:
    """Sync sleep data from Garmin to ActivityWatch"""
    sleep = api.get_sleep_data(date)

    events: list[Event] = []
    for level in sleep["sleepLevels"]:
        sleep_level = SleepLevel(**level)
        duration_seconds = (sleep_level.end - sleep_level.start).total_seconds()

        event = Event(
            timestamp=sleep_level.start,
            duration=duration_seconds,
            data={"title": f"Sleep: {sleep_level.level}"},
        )
        events.append(event)

    # Insert events
    for event in events:
        awc.insert_event("garmin-health", event)

    print(f"Synced {len(events)} sleep events for {date}")
    return events


class AllDayEvent(TypedDict, total=False):
    startTimestampGMT: str
    endTimestampGMT: str
    duration: int  # minutes
    activityType: str


def sync_workout_data(api: Garmin, awc: ActivityWatchClient, date: str) -> list[Event]:
    """Sync workout/activity data from Garmin to ActivityWatch"""
    all_day_events: List[AllDayEvent] = api.get_all_day_events(date)  # type: ignore[assignment]

    events: list[Event] = []
    for activity in all_day_events:
        # Parse the start time
        start_str = str(activity.get("startTimestampGMT", "1970-01-01T00:00:00.0"))
        start_time = datetime.strptime(start_str, "%Y-%m-%dT%H:%M:%S.0")
        # Duration is in minutes; convert to seconds
        duration_minutes = int(activity.get("duration", 0) or 0)
        duration_seconds: int = duration_minutes * 60

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
        events.append(event)

    # Insert events
    for event in events:
        awc.insert_event("garmin-health", event)

    print(f"Synced {len(events)} activity events for {date}")
    return events


def sync_garmin_data(
    email: str,
    password: str,
    date: str | None = None,
    host: str = "localhost",
    port: int = 5600,
) -> None:
    """
    Sync Garmin data to ActivityWatch

    Args:
        email: Garmin account email
        password: Garmin account password
        date: Date to sync (YYYY-MM-DD format). Defaults to today.
        host: ActivityWatch server host (default: localhost)
        port: ActivityWatch server port (default: 5600)
    """
    # Use today if no date provided
    if date is None:
        date = datetime.now().date().strftime("%Y-%m-%d")

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

    # Sync data
    print(f"\nSyncing data for {date}...")
    sleep_events = sync_sleep_data(api, awc, date)
    workout_events = sync_workout_data(api, awc, date)

    print(f"\n✓ Sync complete!")
    print(f"  Total events: {len(sleep_events) + len(workout_events)}")


if __name__ == "__main__":
    # Load environment variables from .env file located next to this script
    # This makes cron/systemd execution independent of the current working directory.
    load_dotenv(dotenv_path=Path(__file__).with_name(".env"))

    email = os.getenv("GARMIN_EMAIL")
    password = os.getenv("GARMIN_PASSWORD")
    host = os.getenv("AW_HOST", "localhost")
    port = int(os.getenv("AW_PORT", "5600"))

    if not email or not password:
        raise ValueError("GARMIN_EMAIL and GARMIN_PASSWORD must be set in .env file")

    sync_garmin_data(
        email=email,
        password=password,
        date=None,  # Use today
        host=host,
        port=port,
    )
