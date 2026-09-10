import hashlib
import ipaddress
from urllib.parse import quote

import httpx

from core.config import settings


class IntegrationService:
    def __init__(self) -> None:
        self.timeout = httpx.Timeout(settings.http_timeout_seconds)

    async def digital_footprint_lookup(self, query: str) -> dict:
        if not settings.digital_footprint_api_key:
            raise RuntimeError("Digital Footprint is not configured")
        query = query.strip()
        if not query:
            raise ValueError("A query is required")
        headers = {
            "Authorization": f"Bearer {settings.digital_footprint_api_key}",
            "Content-Type": "application/json",
            "User-Agent": settings.user_agent,
        }
        async with httpx.AsyncClient(timeout=self.timeout, headers=headers) as client:
            response = await client.post(
                settings.digital_footprint_base_url,
                params={"wait": "true"},
                json={"query": query},
            )
            response.raise_for_status()
            return response.json()

    async def usa_caller_lookup(self, phone: str) -> dict:
        phone = phone.strip()
        if not phone:
            raise ValueError("A phone number is required")
        url = f"https://www.usacallerlookup.com/wp-json/ucl/v1/number/{quote(phone, safe='')}"
        async with httpx.AsyncClient(timeout=self.timeout, headers={"User-Agent": settings.user_agent}) as client:
            response = await client.get(url)
            if response.status_code == 400:
                raise ValueError("USACallerLookup requires a valid 10-digit US phone number")
            response.raise_for_status()
            return response.json()

    async def xposed_account(self, account: str) -> list[str]:
        account = account.strip()
        if not account or "@" not in account:
            raise ValueError("XposedOrNot breach lookup requires an email address")
        headers = {"User-Agent": settings.user_agent}
        url = f"https://api.xposedornot.com/v1/check-email/{quote(account, safe='')}"
        async with httpx.AsyncClient(timeout=self.timeout, headers=headers, follow_redirects=True) as client:
            response = await client.get(url, params={"details": "false"})
            if response.status_code == 404:
                return []
            response.raise_for_status()
            data = response.json()
            if data.get("Error"):
                return []
            breaches = data.get("breaches", [])
            if breaches and isinstance(breaches[0], list):
                breaches = breaches[0]
            return [str(item) for item in breaches if item]

    async def virustotal_lookup(self, value: str) -> dict:
        if not settings.virustotal_api_key:
            raise RuntimeError("VirusTotal is not configured")
        value = value.strip()
        endpoint_type = "domains"
        try:
            ipaddress.ip_address(value)
            endpoint_type = "ip_addresses"
        except ValueError:
            if len(value) in (32, 40, 64) and all(c in "0123456789abcdefABCDEF" for c in value):
                endpoint_type = "files"
        headers = {"x-apikey": settings.virustotal_api_key, "User-Agent": settings.user_agent}
        async with httpx.AsyncClient(timeout=self.timeout, headers=headers) as client:
            response = await client.get(f"https://www.virustotal.com/api/v3/{endpoint_type}/{quote(value, safe='')}")
            if response.status_code == 404:
                return {"found": False, "value": value, "type": endpoint_type}
            response.raise_for_status()
            data = response.json().get("data", {})
            attrs = data.get("attributes", {})
            return {
                "found": True,
                "value": value,
                "type": endpoint_type,
                "reputation": attrs.get("reputation"),
                "last_analysis_stats": attrs.get("last_analysis_stats", {}),
                "categories": attrs.get("categories", {}),
            }

    async def epss(self, cve_id: str) -> dict | None:
        cve_id = cve_id.strip().upper()
        async with httpx.AsyncClient(timeout=self.timeout, headers={"User-Agent": settings.user_agent}) as client:
            response = await client.get("https://api.first.org/data/v1/epss", params={"cve": cve_id})
            response.raise_for_status()
            rows = response.json().get("data", [])
            return rows[0] if rows else None

    async def cisa_kev(self, cve_id: str) -> dict | None:
        url = "https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json"
        cve_id = cve_id.strip().upper()
        async with httpx.AsyncClient(timeout=self.timeout, headers={"User-Agent": settings.user_agent}) as client:
            response = await client.get(url)
            response.raise_for_status()
            for vuln in response.json().get("vulnerabilities", []):
                if str(vuln.get("cveID", "")).upper() == cve_id:
                    return vuln
        return None

    async def pwned_password_count(self, password: str) -> int:
        digest = hashlib.sha1(password.encode("utf-8")).hexdigest().upper()
        prefix, suffix = digest[:5], digest[5:]
        async with httpx.AsyncClient(timeout=self.timeout, headers={"User-Agent": settings.user_agent}) as client:
            response = await client.get(f"https://api.pwnedpasswords.com/range/{prefix}")
            response.raise_for_status()
            for line in response.text.splitlines():
                candidate, count = line.split(":", 1)
                if candidate == suffix:
                    return int(count)
        return 0


integrations = IntegrationService()
