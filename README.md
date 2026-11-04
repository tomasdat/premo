# Premo

Premo is a League of Legends guild app for tracking players and accounts, syncing match history, and exploring player and squad stats.

## Requirements

- Python 3.10 or newer
- A Riot Games API key for account lookup and match syncing

## Setup

In PowerShell, from the project directory:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Set `RIOT_API_KEY` in `.env`. Adjust `PLATFORM`, `SYNC_QUEUES`, or `DB_PATH` there if needed.

Start the app:

```powershell
uvicorn app.main:app --reload
```

Open [http://localhost:8000](http://localhost:8000). On first startup, Premo creates a setup code at `data/setup_code.txt`; use it to register the first account, which becomes the admin. Admins can invite additional members.

## Tests

```powershell
python -m pytest
```

The `.env` file and `data/` directory are intentionally excluded from Git. They contain local configuration, the database, and setup data.
