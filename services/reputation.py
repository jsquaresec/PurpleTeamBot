import httpx

from core.config import settings
from core.targets import normalize_target


class ReputationService:
    def __init__(self) -> None:
        self.timeout = httpx.Timeout(settings.http_timeout_seconds)

    async def abuseipdb(self, ip: str) -> dict:
        if not settings.abuseipdb_api_key:
            raise RuntimeError("AbuseIPDB is not configured")
        headers = {"Key": settings.abuseipdb_api_key, "Accept": "application/json"}
        async with httpx.AsyncClient(timeout=self.timeout, headers=headers) as client:
            r = await client.get("https://api.abuseipdb.com/api/v2/check", params={"ipAddress": ip, "maxAgeInDays": 90})
            r.raise_for_status()
            d = r.json().get("data", {})
            return {
                "ip": d.get("ipAddress"),
                "abuse_confidence_score": d.get("abuseConfidenceScore"),
                "country": d.get("countryCode"),
                "usage_type": d.get("usageType"),
                "isp": d.get("isp"),
                "domain": d.get("domain"),
                "total_reports": d.get("totalReports"),
                "last_reported_at": d.get("lastReportedAt"),
            }

    async def shodan_host(self, ip: str) -> dict:
        if not settings.shodan_api_key:
            raise RuntimeError("Shodan is not configured")
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            r = await client.get(f"https://api.shodan.io/shodan/host/{ip}", params={"key": settings.shodan_api_key, "minify": "true"})
            r.raise_for_status()
            d = r.json()
            return {
                "ip": d.get("ip_str", ip),
                "org": d.get("org"),
                "isp": d.get("isp"),
                "asn": d.get("asn"),
                "country": d.get("country_code"),
                "hostnames": d.get("hostnames", [])[:10],
                "ports": d.get("ports", [])[:50],
                "vulns": list((d.get("vulns") or {}).keys())[:25] if isinstance(d.get("vulns"), dict) else (d.get("vulns") or [])[:25],
            }

    async def securitytrails_subdomains(self, domain: str) -> list[str]:
        if not settings.securitytrails_api_key:
            raise RuntimeError("SecurityTrails is not configured")
        domain = normalize_target(domain)
        headers = {"APIKEY": settings.securitytrails_api_key, "Accept": "application/json"}
        async with httpx.AsyncClient(timeout=self.timeout, headers=headers) as client:
            r = await client.get(f"https://api.securitytrails.com/v1/domain/{domain}/subdomains")
            r.raise_for_status()
            subs = r.json().get("subdomains", [])
            return [f"{x}.{domain}" for x in subs[:200]]


reputation_service = ReputationService()
