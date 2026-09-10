import ipaddress
from urllib.parse import urlparse

import httpx

from core.config import settings


class ReputationService:
    def __init__(self) -> None:
        self.timeout = httpx.Timeout(settings.http_timeout_seconds)

    async def abuseipdb(self, ip: str) -> dict:
        if not settings.abuseipdb_api_key:
            raise RuntimeError("AbuseIPDB is not configured")
        headers = {"Key": settings.abuseipdb_api_key, "Accept": "application/json"}
        async with httpx.AsyncClient(timeout=self.timeout, headers=headers) as client:
            r = await client.get(
                "https://api.abuseipdb.com/api/v2/check",
                params={"ipAddress": ip, "maxAgeInDays": 90},
            )
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

    async def censys_host(self, ip: str) -> dict:
        if not settings.censys_pat:
            raise RuntimeError("Censys is not configured")
        try:
            ipaddress.ip_address(ip)
        except ValueError as exc:
            raise ValueError("Censys host lookup requires a valid IP address") from exc

        headers = {
            "Authorization": f"Bearer {settings.censys_pat}",
            "Accept": "application/vnd.censys.api.v3.host.v1+json",
            "User-Agent": settings.user_agent,
        }
        async with httpx.AsyncClient(timeout=self.timeout, headers=headers) as client:
            r = await client.get(f"https://api.platform.censys.io/v3/global/asset/host/{ip}")
            if r.status_code == 404:
                return {"found": False, "ip": ip}
            r.raise_for_status()
            data = r.json()
            return {"found": True, "ip": ip, "data": data}

    async def urlscan_search(self, value: str) -> dict:
        value = value.strip()
        headers = {"User-Agent": settings.user_agent}
        if settings.urlscan_api_key:
            headers["API-Key"] = settings.urlscan_api_key

        query = value
        try:
            ipaddress.ip_address(value)
            query = f"ip:{value}"
        except ValueError:
            parsed = urlparse(value if "://" in value else f"https://{value}")
            host = parsed.hostname or value
            query = f"domain:{host}"

        async with httpx.AsyncClient(timeout=self.timeout, headers=headers, follow_redirects=True) as client:
            r = await client.get("https://urlscan.io/api/v1/search/", params={"q": query, "size": 10})
            r.raise_for_status()
            data = r.json()
            rows = []
            for item in data.get("results", [])[:10]:
                page = item.get("page", {})
                task = item.get("task", {})
                rows.append(
                    {
                        "url": page.get("url") or task.get("url"),
                        "domain": page.get("domain"),
                        "ip": page.get("ip"),
                        "asn": page.get("asn"),
                        "country": page.get("country"),
                        "scan_id": item.get("_id") or task.get("uuid"),
                    }
                )
            return {"query": query, "total": data.get("total", 0), "results": rows}

    async def otx_indicator(self, value: str) -> dict:
        if not settings.otx_api_key:
            raise RuntimeError("AlienVault OTX is not configured")

        value = value.strip()
        indicator_type = "domain"
        try:
            ip = ipaddress.ip_address(value)
            indicator_type = "IPv6" if ip.version == 6 else "IPv4"
        except ValueError:
            if value.startswith(("http://", "https://")):
                indicator_type = "url"
            elif len(value) == 32 and all(c in "0123456789abcdefABCDEF" for c in value):
                indicator_type = "file"
            elif len(value) == 40 and all(c in "0123456789abcdefABCDEF" for c in value):
                indicator_type = "file"
            elif len(value) == 64 and all(c in "0123456789abcdefABCDEF" for c in value):
                indicator_type = "file"

        headers = {"X-OTX-API-KEY": settings.otx_api_key, "User-Agent": settings.user_agent}
        async with httpx.AsyncClient(timeout=self.timeout, headers=headers, follow_redirects=True) as client:
            r = await client.get(f"https://otx.alienvault.com/api/v1/indicators/{indicator_type}/{value}/general")
            if r.status_code == 404:
                return {"found": False, "type": indicator_type, "value": value}
            r.raise_for_status()
            data = r.json()
            pulse_info = data.get("pulse_info", {})
            return {
                "found": True,
                "type": indicator_type,
                "value": value,
                "pulse_count": pulse_info.get("count", 0),
                "reputation": data.get("reputation"),
                "validation": data.get("validation", []),
                "asn": data.get("asn"),
                "country_code": data.get("country_code"),
            }


reputation_service = ReputationService()
