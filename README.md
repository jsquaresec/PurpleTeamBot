# PurpleTeamBot

Purple Team is a lightweight Discord security operations bot designed for authorized OSINT, reconnaissance, vulnerability intelligence, defensive analysis, and scoped network assessment on small infrastructure such as a 1 vCPU / 1 GB RAM VPS.

## Design goals

- Runs comfortably on 1c/1g with conservative concurrency.
- Uses SQLite instead of a local database server.
- Uses async HTTP/DNS calls and one active Nmap scan by default.
- Requires explicit guild scope registration before active scans.
- Keeps passive intelligence separate from active assessment.
- Presents concise Discord embeds instead of raw tool output.

## Command families

- `/scope add|remove|list`
- `/scan quick|service`
- `/osint domain|ip|dns|rdap|email|username|person`
- `/intel ip|domain|url|hash`
- `/vuln cve|kev`
- `/recon web|tls|subdomains`
- `/investigate domain`
- `/status`

Person/username OSINT is limited to lawful public-source correlation. The bot does not retrieve private addresses, credentials, financial information, or non-public personal records.

## Active scan safety

Nmap commands only run against targets explicitly registered by a guild administrator through `/scope add`. The wrapper uses curated ports, conservative timing, one scan at a time by default, and hard timeouts.

## Requirements

- Python 3.11+
- Nmap installed on the host

## Install

```bash
sudo apt update
sudo apt install -y python3 python3-venv nmap

git clone https://github.com/jsquaresec/PurpleTeamBot.git
cd PurpleTeamBot
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
nano .env
python main.py
```

## Environment

```env
DISCORD_TOKEN=
DISCORD_GUILD_ID=
BOT_OWNER_ID=
DATABASE_PATH=purple_team.db
MAX_ACTIVE_SCANS=1
SCAN_TIMEOUT_SECONDS=90
HTTP_TIMEOUT_SECONDS=12
USER_AGENT=PurpleTeamBot/0.1
```

`DISCORD_GUILD_ID` is optional but recommended while developing because guild commands sync much faster than global commands.

## Roadmap

The repo starts with a functional lightweight core and leaves room for API-backed additions such as EPSS, CISA KEV correlation, certificate transparency, reputation providers, public breach-notification providers, passive DNS, malware reputation, reporting, cases, and historical comparisons without hosting large local datasets.
