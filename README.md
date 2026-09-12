<div align="center">

# 🟣 Purple Team

### A J2SEC / JSquareSec Security Project

**Systems • Security • Software**

Lightweight Discord-based security operations, OSINT, vulnerability intelligence, defensive analysis, person intelligence, and authorized security assessment.

[![GitHub](https://img.shields.io/badge/GitHub-jsquaresec-111111?style=for-the-badge&logo=github&logoColor=white)](https://github.com/jsquaresec)
[![Certifications](https://img.shields.io/badge/Verified-Credentials-168BFF?style=for-the-badge&logo=googlechrome&logoColor=white)](https://jsquaresec.github.io/jsquaresec/certifications/)
[![J2SEC](https://img.shields.io/badge/Brand-J2SEC-7C3AED?style=for-the-badge)](https://github.com/jsquaresec)
[![Only The Demons](https://img.shields.io/badge/Web-onlythedemons.com-168BFF?style=for-the-badge&logo=googlechrome&logoColor=white)](https://onlythedemons.com)

**Built by Joshua Jones — J2SEC / JSquareSec**

</div>

---

Purple Team is a lightweight security-operations Discord bot combining public-source OSINT, threat intelligence, vulnerability prioritization, defensive analysis, identity intelligence, and authorized reconnaissance in one platform. It is designed to stay efficient through async I/O, conservative scan concurrency, compact Nmap jobs, remote intelligence APIs, and no heavyweight local vulnerability databases.

## OSINT and passive reconnaissance

- DNS A / AAAA / MX / NS / TXT intelligence
- RDAP domain and IP registration data
- reverse DNS / PTR correlation
- Certificate Transparency discovery through crt.sh
- public username correlation across GitHub, GitLab, Reddit, Keybase, and HackerOne
- GitHub public profile intelligence
- email-domain posture checks
- SPF and DMARC discovery
- DNSSEC DNSKEY observation
- HTTP response and redirect intelligence
- server-header and X-Powered-By exposure checks
- web technology fingerprinting
- TLS certificate, protocol, cipher, expiry, issuer, subject, and SAN inspection
- public person / identifier correlation
- urlscan.io web intelligence
- AlienVault OTX IOC enrichment

## Identity and person intelligence

- EnformionGO person search
- EnformionGO reverse phone intelligence
- EnformionGO reverse email intelligence
- EnformionGO reverse address intelligence
- EnformionGO contact-enrichment adapter
- XposedOrNot email breach exposure
- HIBP Pwned Passwords k-anonymity checks
- public username/profile correlation
- email-domain correlation

Person and identifier commands are permission-gated, return ephemerally, and are audit logged. Provider credentials are kept in the server `.env` and are never committed to the repository.

## Threat and vulnerability intelligence

- VirusTotal domain, IP, and file-hash reputation workflows
- AbuseIPDB IP reputation
- Censys host and exposure intelligence
- urlscan.io web intelligence
- AlienVault OTX IOC intelligence
- XposedOrNot breach exposure
- CVE.org CVE records
- FIRST EPSS exploitation probability and percentile
- CISA Known Exploited Vulnerabilities correlation
- software/version review from service fingerprints
- known-exploited prioritization for remediation

## Authorized penetration-testing and assessment features

Active assessment is restricted to targets explicitly registered in the server's authorized scope.

- lightweight Nmap TCP quick scans
- lightweight Nmap service/version detection
- curated high-value port coverage
- service/banner identification
- web technology fingerprinting
- HTTP security-header review
- HTTP method / WebDAV advertisement inspection
- TLS configuration and certificate inspection
- common web exposure checks
- `.git/HEAD` exposure detection
- `.env` exposure detection
- server-status exposure detection
- phpinfo exposure detection
- actuator-health exposure detection
- robots.txt / sitemap / security.txt discovery
- passive subdomain discovery
- DNS security posture
- attack-surface inventory building blocks
- hard scan timeouts and low retry counts
- scan history and audit records

Exposure checks only collect response status and metadata; they do not dump discovered sensitive file contents into Discord.

## Defensive analysis

- uploaded-file MD5 / SHA-1 / SHA-256 hashing
- optional VirusTotal SHA-256 reputation lookup
- `.eml` parsing
- email authentication-header analysis
- mail route / Received-header summary
- IOC lookup for IPs, domains, URLs, and hashes through configured providers
- investigation history and audit logging

## Investigation workflow

`/investigate <target>` combines passive and authorized active intelligence into a single workflow. It includes DNS, Certificate Transparency, HTTP/security-header analysis, and a lightweight Nmap pass when the target is in scope.

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

/osintx email <email-or-domain>
/osintx reverse-dns <ip>
/osintx profiles <username>

/person search <first_name> <last_name> [city] [state] [email] [phone]
/person public <query>

/reverse phone <phone>
/reverse email <email>
/reverse address <address_line1> <city_state_zip>
/reverse username <username>

/intel lookup <domain|ip|hash>
/intel breach <email>

/reputation abuseipdb <ip>
/reputation censys <ip>
/reputation otx <indicator>
/passive urlscan <domain>

/vuln cve <CVE-ID>

/recon web <target>
/recon tls <target> [port]

/assess fingerprint <target>
/assess exposure <target>
/assess methods <target>

/analyze file <attachment> [virustotal]
/analyze email <attachment.eml>

/investigate <target>
/history
/status
```

## Lightweight by design

- SQLite works out of the box.
- PostgreSQL / Neon is supported by setting `DATABASE_URL`.
- Async HTTP and DNS operations.
- Remote CVE, breach, reputation, and OSINT intelligence instead of large local datasets.
- Conservative Nmap concurrency and scan timeouts.
- Curated scan profiles instead of aggressive all-port defaults.
- Small bounded file-analysis jobs.
- Modular providers so optional integrations do not increase the base footprint when unused.

## Requirements

- Linux recommended
- Python 3.11+
- Nmap

## Quick install

```bash
git clone https://github.com/jsquaresec/PurpleTeamBot.git
cd PurpleTeamBot
sudo bash deploy/install.sh
sudo micro /opt/PurpleTeamBot/.env
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
DATABASE_URL=

MAX_ACTIVE_SCANS=1
SCAN_TIMEOUT_SECONDS=90
HTTP_TIMEOUT_SECONDS=12
USER_AGENT=PurpleTeamBot/0.1

ENFORMION_AP_NAME=
ENFORMION_AP_PASSWORD=
ENFORMION_SEARCH_TYPE=Person
ENFORMION_BASE_URL=https://devapi.enformion.com/PersonSearch

VIRUSTOTAL_API_KEY=
ABUSEIPDB_API_KEY=
CENSYS_PAT=
URLSCAN_API_KEY=
OTX_API_KEY=
```

Only `DISCORD_TOKEN` is mandatory for the base bot. EnformionGO credentials enable the richer person/reverse intelligence layer. XposedOrNot, HIBP Pwned Passwords, FIRST EPSS, CISA KEV, RDAP, Certificate Transparency, DNS, reverse DNS, email-domain posture, and basic HTTP/TLS checks do not require paid API credentials.

If `DATABASE_URL` is blank, Purple Team uses SQLite. If a PostgreSQL/Neon connection string is supplied, the bot automatically initializes and uses PostgreSQL instead.

`DISCORD_GUILD_ID` is optional but useful during development because commands can sync directly to a test server.

## Authorized-use model

Active network and web assessment only runs against targets explicitly registered through `/scope add`. Purple Team is intended for systems you own or have permission to assess. Person-intelligence features should be used for legitimate security, fraud-prevention, identity-verification, due-diligence, or other lawful purposes consistent with applicable provider terms.

---

<div align="center">

### J2SEC / JSquareSec

**Joshua Jones**  
**Systems • Security • Software**

[GitHub](https://github.com/jsquaresec) • [Verified Credentials](https://jsquaresec.github.io/jsquaresec/certifications/) • [Only The Demons](https://onlythedemons.com)

**Build with purpose. Secure what matters. Keep learning.**

</div>
