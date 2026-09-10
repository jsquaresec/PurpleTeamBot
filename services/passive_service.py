import ipaddress
from urllib.parse import urlparse

import dns.asyncresolver
import httpx

from core.config import settings
from core.targets import normalize_target, safe_url


class PassiveService:
    def __init__(self) -> None:
        self._timeout = httpx.Timeout(settings.http_timeout_seconds)

    @staticmethod
    def _is_ip(value: str) -> bool:
        try:
            ipaddress.ip_address(value)
            return True
        except ValueError:
            return False

    async def dns_records(self, domain: str) -> dict[str, list[str]]:
        domain = normalize_target(domain)
        resolver = dns.asyncresolver.Resolver()
        result: dict[str, list[str]] = {}
        for record_type in ("A", "AAAA", "MX", "NS", "TXT"):
            try:
                answer = await resolver.resolve(domain, record_type, lifetime=settings.http_timeout_seconds)
                result[record_type] = [str(r).strip('"')[:300] for r in answer]
            except Exception:
                result[record_type] = []
        return result

    async def rdap(self, target: str) -> dict:
        target = normalize_target(target)
        endpoint = f"https://rdap.org/ip/{target}" if self._is_ip(target) else f"https://rdap.org/domain/{target}"
        async with httpx.AsyncClient(timeout=self._timeout, headers={"User-Agent": settings.user_agent}) as client:
            response = await client.get(endpoint, follow_redirects=True)
            response.raise_for_status()
            return response.json()

    async def certificate_names(self, domain: str) -> list[str]:
        domain = normalize_target(domain)
        if self._is_ip(domain):
            return []
        async with httpx.AsyncClient(timeout=self._timeout, headers={"User-Agent": settings.user_agent}) as client:
            response = await client.get("https://crt.sh/", params={"q": f"%.{domain}", "output": "json"})
            response.raise_for_status()
            names: set[str] = set()
            for item in response.json()[:300]:
                for name in str(item.get("name_value", "")).splitlines():
                    name = name.strip().lower().lstrip("*.")
                    if name == domain or name.endswith("." + domain):
                        names.add(name)
            return sorted(names)[:100]

    async def github_username(self, username: str) -> dict | None:
        username = username.strip()
        if not username or len(username) > 39:
            raise ValueError("Invalid username.")
        async with httpx.AsyncClient(timeout=self._timeout, headers={"User-Agent": settings.user_agent}) as client:
            response = await client.get(f"https://api.github.com/users/{username}")
            if response.status_code == 404:
                return None
            response.raise_for_status()
            data = response.json()
            return {
                "login": data.get("login"),
                "name": data.get("name"),
                "bio": data.get("bio"),
                "company": data.get("company"),
                "location": data.get("location"),
                "blog": data.get("blog"),
                "public_repos": data.get("public_repos"),
                "followers": data.get("followers"),
                "html_url": data.get("html_url"),
            }

    async def person_search(self, query: str) -> dict:
        query = query.strip()
        if not query or len(query) > 120:
            raise ValueError("Person search query must be between 1 and 120 characters.")

        result = {"query": query, "github": None, "notes": []}

        # Lightweight public-source correlation. Username-like queries are checked
        # against GitHub. Additional providers can be plugged in later without
        # increasing the base VPS footprint.
        if " " not in query and "@" not in query:
            try:
                result["github"] = await self.github_username(query)
            except Exception:
                result["github"] = None

        if "@" in query:
            local, _, domain = query.partition("@")
            if local and domain:
                result["email_domain"] = normalize_target(domain)
                try:
                    result["dns"] = await self.dns_records(domain)
                except Exception:
                    result["dns"] = {}

        result["notes"].append(
            "Public-source OSINT only; no private records, credentials, financial data, or hidden personal data sources are queried."
        )
        return result

    async def http_probe(self, value: str) -> dict:
        url = safe_url(value)
        async with httpx.AsyncClient(timeout=self._timeout, headers={"User-Agent": settings.user_agent}, verify=True) as client:
            response = await client.get(url, follow_redirects=True)
            return {
                "url": str(response.url),
                "status": response.status_code,
                "server": response.headers.get("server", ""),
                "content_type": response.headers.get("content-type", ""),
                "security_headers": {
                    name: response.headers.get(name, "")
                    for name in (
                        "strict-transport-security",
                        "content-security-policy",
                        "x-content-type-options",
                        "x-frame-options",
                        "referrer-policy",
                        "permissions-policy",
                    )
                },
            }

    async def cve_lookup(self, cve_id: str) -> dict:
        cve_id = cve_id.strip().upper()
        if not cve_id.startswith("CVE-"):
            raise ValueError("Use a CVE identifier such as CVE-2024-1234.")
        async with httpx.AsyncClient(timeout=self._timeout, headers={"User-Agent": settings.user_agent}) as client:
            response = await client.get(f"https://cveawg.mitre.org/api/cve/{cve_id}")
            if response.status_code == 404:
                raise ValueError("CVE not found.")
            response.raise_for_status()
            return response.json()


passive_service = PassiveService()
