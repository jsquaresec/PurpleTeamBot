# PurpleTeamBot

Purple Team is a lightweight Discord security-operations bot designed for authorized OSINT, reconnaissance, vulnerability intelligence, defensive analysis, person intelligence, and scoped network assessment on small infrastructure such as a 1 vCPU / 1 GB RAM VPS.

## What is included

### OSINT and passive recon
- DNS A/AAAA/MX/NS/TXT lookups
- RDAP domain/IP intelligence
- Certificate Transparency subdomain discovery
- SecurityTrails passive subdomain discovery
- GitHub/public username correlation
- HTTP status, server fingerprint and security-header checks
- TLS certificate/protocol/cipher inspection
- Lightweight public person/identifier correlation

### Person intelligence
- EnformionGO Person Search
- EnformionGO reverse phone
- EnformionGO reverse email
- EnformionGO address intelligence
- HIBP breach-exposure lookup

Person-data commands are intentionally restricted to the bot owner or members with **Manage Server**, return ephemerally, and are audit logged. Rich provider records are not dumped into public Discord channels.

### Threat and vulnerability intelligence
- VirusTotal domain/IP/hash reputation
- AbuseIPDB IP reputation
- Shodan host intelligence
- CVE.org CVE records
- FIRST EPSS exploitation probability
- CISA Known Exploited Vulnerabilities correlation
- Have I Been Pwned breach exposure

### Authorized active assessment
- Lightweight Nmap quick scan
- Lightweight Nmap service/version scan
- Explicit per-guild target scope
- One active scan by default
- Curated ports, hard timeouts and low retry counts
- Scan history

### Defensive analysis
- Uploaded-file MD5/SHA-1/SHA-256 hashing
- Optional VirusTotal SHA-256 lookup
- `.eml` header parsing
- Authentication-Results/route-hop summary

### Investigation workflow
`/investigate <target>` combines DNS, Certificate Transparency, HTTP/security-header analysis, and a lightweight Nmap pass when the target has been explicitly authorized in `/scope`.

## Commands

```text
/scope add <target>
/scope remove <target>
/scope list

/scan quick <target>
/scan service <target>

/osint dns <domain>
/osint rdap <target>
/osint subdomains <domain>
/osint username <username>

/person search <first_name> <last_name> [city] [state] [email] [phone]
/person public <query>

/reverse phone <phone>
/reverse email <email>
/reverse address <street> <city_state_zip>

/intel lookup <domain|ip|hash>
/intel breach <account>

/reputation abuseipdb <ip>
/reputation shodan <ip>
/passive subdomains <domain>

/vuln cve <CVE-ID>

/recon web <target>
/recon tls <target> [port]

/analyze file <attachment> [virustotal]
/analyze email <attachment.eml>

/investigate <target>
/history
/status
```

## Resource design

The bot is intentionally designed around a 1c/1g host:

- SQLite rather than PostgreSQL/Redis for the initial deployment.
- Async HTTP/DNS I/O.
- No locally hosted CVE, breach, reputation, or OSINT datasets.
- One Nmap job at a time by default.
- Maximum uploaded analysis file size of 8 MB.
- Maximum uploaded email size of 2 MB.
- systemd `MemoryMax=700M` and `CPUQuota=90%` in the provided unit.

## Requirements

- Ubuntu/Debian-style Linux recommended
- Python 3.11+
- Nmap

## Quick install

```bash
git clone https://github.com/jsquaresec/PurpleTeamBot.git
cd PurpleTeamBot
sudo bash deploy/install.sh
sudo nano /opt/PurpleTeamBot/.env
sudo systemctl restart purpleteambot
sudo systemctl status purpleteambot
```

Manual development setup:

```bash
sudo apt update
sudo apt install -y python3 python3-venv nmap
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

ENFORMION_AP_NAME=
ENFORMION_AP_PASSWORD=
ENFORMION_SEARCH_TYPE=Person
ENFORMION_BASE_URL=https://devapi.enformion.com/PersonSearch

VIRUSTOTAL_API_KEY=
HIBP_API_KEY=
SHODAN_API_KEY=
ABUSEIPDB_API_KEY=
SECURITYTRAILS_API_KEY=
```

Only `DISCORD_TOKEN` is mandatory. Provider-backed commands report that the integration is not configured when its credentials are missing. FIRST EPSS, CISA KEV, RDAP, Certificate Transparency, DNS and basic HTTP/TLS checks do not require private API credentials.

`DISCORD_GUILD_ID` is optional but recommended during development because commands sync directly to your test server instead of waiting for global Discord propagation.

## Authorized-use model

Active network scanning only runs against targets explicitly registered through `/scope add`. Purple Team is intended for systems you own or have permission to assess. Person-intelligence features should be used for legitimate security, fraud-prevention, identity-verification, due-diligence, or other lawful purposes consistent with the data provider's terms.
