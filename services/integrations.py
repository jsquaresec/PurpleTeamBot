import hashlib
import ipaddress
from urllib.parse import quote

import httpx

from core.config import settings


class IntegrationService:
    def __init__(self) -> None:
        self.timeout = httpx.Timeout(settings.http_timeout_seconds)

    async def _enformion(self, endpoint: str, search_type: str, payload: dict) -> dict:
        if not settings.enformion_ap_name or not settings.enformion_ap_password:
            raise RuntimeError("EnformionGO is not configured")
        headers = {
            "galaxy-ap-name": settings.enformion_ap_name,
            "galaxy-ap-password": settings.enformion_ap_password,
            "galaxy-search-type": search_type,
            "User-Agent": settings.user_agent,
        }
        async with httpx.AsyncClient(timeout=self.timeout, headers=headers) as client:
            response = await client.post(endpoint, json=payload)
            response.raise_for_status()
            return response.json()

    async def enformion_person_search(self, payload: dict) -> dict:
        return await self._enformion(
            settings.enformion_base_url,
            settings.enformion_search_type,
            payload,
        )

    async def enformion_phone(self, phone: str) -> dict:
        phone = phone.strip()
        if not phone:
            raise ValueError("A phone number is required")
        return await self._enformion(
            "https://devapi.enformion.com/Phone/Enrich",
            "DevAPICallerID",
            {"Phone": phone},
        )

    async def enformion_email(self, email: str) -> dict:
        email = email.strip()
        if not email:
            raise ValueError("An email address is required")
        return await self._enformion(
            "https://devapi.enformion.com/Email/Id",
            "DevAPIEmailID",
            {"Email": email},
        )

    async def enformion_address(self, address_line1: str, address_line2: str, exact_match: str = "") -> dict:
        if not address_line1.strip() or not address_line2.strip():
            raise ValueError("Both address lines are required")
        payload = {
            "addressline1": address_line1.strip(),
            "addressline2": address_line2.strip(),
        }
        if exact_match:
            payload["ExactMatch"] = exact_match
        return await self._enformion(
            "https://devapi.enformion.com/Address/Id",
            "DevAPIAddressID",
            payload,
        )

    async def enformion_contact_enrich(self, payload: dict) -> dict:
        return await self._enformion(
            "https://devapi.enformion.com/Contact/Enrich",
            "DevAPIContactEnrich",
            payload,
        )

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
