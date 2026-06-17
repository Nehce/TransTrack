# Kingtrans Tracker

A small Python tool to query shipment tracking information from **xj56.kingtrans.net**, parse the XML response, persist state, compute diffs, and run in CLI mode. Designed for extension with schedulers and push notifications (e.g. Telegram).

## Features
- Robust client with HTTP/HTTPS fallback and retries (`kingtrans_client.py`)
- Data models: `TrackSummary`, `TrackItem`, `TrackResult`
- Persistent JSON state store with atomic writes (`storage.py`)
- Diff utilities (both persistent and pure in-memory: `storage.py`, `diff.py`)
- CLI interface (`cli.py`):
  - Single or multiple tracking numbers
  - Pretty or JSON output
  - CSV export of newly added events
  - Loop mode (simple scheduler)
- Ready for extension with push integrations (Telegram, email, etc.)

## Requirements
- Python 3.8+
- `requests`

Install dependencies:
```bash
pip install -r requirements.txt
```

## Usage

### Single run
```bash
python cli.py --tracking 1ZW1008Y6816279460
```

### Multiple numbers + JSON output
```bash
python cli.py --tracking 1ZW... 1ZB... --json
```

### From file
```bash
python cli.py --batch tracklist.txt --csv added.csv
```
Where `tracklist.txt` contains one tracking number per line.

### Test Telegram notification
```bash
python send_test_telegram.py
```
Requires `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` environment variables.

### Loop mode
```bash
python cli.py --batch tracklist.txt --loop --interval 120
```
Runs every 120 minutes until stopped.

## Project structure
```
kingtrans_client.py   # Client: HTTP, parsing, data models
storage.py            # JSON state store + diffing
cli.py                # Command-line interface
diff.py               # Pure diff functions (dict/list based)
README.md             # This file
requirements.txt      # Dependencies
state/                # Default folder for JSON snapshots
```

## Extending
- **Push notifications**: Use `DiffResult` (from `storage.py`) or `DiffLite` (from `diff.py`) to detect changes, then send messages.
- **Scheduling**: Use external cron/systemd, or extend `cli.py` with APScheduler.
- **Tests**: Save sample XML responses and run unit tests against the parsers.

## License
MIT (for personal use).