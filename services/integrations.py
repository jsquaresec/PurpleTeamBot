import hashlib
import ipaddress
from urllib.parse import quote

import httpx

from core.config import settings


class IntegrationService:
    def __init__(self) -> None:
        self.timeout = httpx.Timeout(settings.http_timeout_seconds)

    async def pdl_person_enrich(self, **params) -> dict:
        if not settings.pdl_api_key:
            raise RuntimeError("People Data Labs is not configured")

        payload = {k: v for k, v in params.items() if v not in (None, "", [], {})}
        headers = {
            "X-Api-Key": settings.pdl_api_key,
            "Content-Type": "application/json",
            "User-Agent": settings.user_agent,
        }
        async with httpx.AsyncClient(timeout=self.timeout, headers=headers) as client:
            response = await client.post(settings.pdl_base_url, json=payload)
            if response.status_code == 404:
                return {"found": False}
            response.raise_for_status()
            data = response.json()
            return {"found": True, "data": data.get("data", data), "status": data.get("status")}

    # Compatibility wrappers for existing command code while the UI labels are migrated.
    # These now use People Data Labs; Enformion is not contacted.
    async def enformion_person_search(self, payload: dict) -> dict:
        params = {
            "first_name": payload.get("FirstName"),
            "last_name": payload.get("LastName"),
            "email": payload.get("Email"),
            "phone": payload.get("Phone"),
        }
        addresses = payload.get("Addresses") or []
        if addresses and isinstance(addresses[0], dict):
            params["location"] = addresses[0].get("AddressLine2")
        return await self.pdl_person_enrich(**params)

    async def enformion_phone(self, phone: str) -> dict:
        return await self.pdl_person_enrich(phone=phone)

    async def enformion_email(self, email: str) -> dict:
        return await self.pdl_person_enrich(email=email)

    async def enformion_address(self, address_line1: str, address_line2: str, exact_match: str = "") -> dict:
        return await self.pdl_person_enrich(street_address=address_line1, location=address_line2)

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
