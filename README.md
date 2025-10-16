# aw-garmin

Sync Garmin Connect health data (sleep stages and activities) to
[ActivityWatch](https://github.com/ActivityWatch/activitywatch).

This will:

- Sync today's sleep data (sleep stages: DEEP, LIGHT, REM, AWAKE)
- Sync today's activities (walking, cycling, running, etc.)
- Store everything in the `garmin-health` bucket in ActivityWatch

All events will appear in the ActivityWatch timeline with color-coded titles:

- Sleep: `Sleep: DEEP`, `Sleep: REM`, etc.
- Activities: `Activity: Walking`, `Activity: Cycling`, etc.

## Setup

1. Copy `.env.example` to `.env`:
   ```bash
   cp .env.example .env
   ```

2. Edit `.env` and add your Garmin credentials:
   ```
   GARMIN_EMAIL=your-email@example.com
   GARMIN_PASSWORD=your-password
   AW_HOST=localhost
   AW_PORT=5600
   ```

3. Sync one-time:
   ```bash
   uv run sync.py
   ```

4. Create a daily cron job to run the sync script. E.g. with `crontab -e`:
   ```
   0 6 * * * /path/to/your/uv run /path/to/your/sync.py
   ```

   This example runs the sync script every day at 6 AM.
