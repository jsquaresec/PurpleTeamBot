import asyncio
import socket

import dns.asyncresolver
import httpx

from core.config import settings
from core.targets import normalize_target


class AdvancedOSINTService:
    def __init__(self) -> None:
        self.timeout = httpx.Timeout(settings.http_timeout_seconds)

    async def email_posture(self, email_or_domain: str) -> dict:
        value = email_or_domain.strip().lower()
        domain = value.split('@', 1)[1] if '@' in value else value
        domain = normalize_target(domain)
        resolver = dns.asyncresolver.Resolver()

        async def query(name: str, record_type: str) -> list[str]:
            try:
                answer = await resolver.resolve(name, record_type, lifetime=settings.http_timeout_seconds)
                return [str(r).strip('"')[:500] for r in answer]
            except Exception:
                return []

        mx, txt, dmarc, dnskey = await asyncio.gather(
            query(domain, 'MX'),
            query(domain, 'TXT'),
            query(f'_dmarc.{domain}', 'TXT'),
            query(domain, 'DNSKEY'),
        )
        spf = [x for x in txt if x.lower().startswith('v=spf1')]
        return {
            'domain': domain,
            'mx': mx[:10],
            'spf': spf[:5],
            'dmarc': dmarc[:5],
            'dnssec_dnskey': bool(dnskey),
        }

    async def reverse_dns(self, ip: str) -> dict:
        ip = normalize_target(ip)
        try:
            socket.inet_pton(socket.AF_INET, ip)
        except OSError:
            try:
                socket.inet_pton(socket.AF_INET6, ip)
            except OSError as exc:
                raise ValueError('Reverse DNS requires an IP address.') from exc
        try:
            host, aliases, addresses = await asyncio.to_thread(socket.gethostbyaddr, ip)
            return {'ip': ip, 'hostname': host, 'aliases': aliases[:10], 'addresses': addresses[:10]}
        except socket.herror:
            return {'ip': ip, 'hostname': None, 'aliases': [], 'addresses': []}

    async def username_profiles(self, username: str) -> list[dict]:
        username = username.strip()
        if not username or len(username) > 64 or any(c.isspace() for c in username):
            raise ValueError('Invalid username.')
        templates = {
            'GitHub': f'https://github.com/{username}',
            'GitLab': f'https://gitlab.com/{username}',
            'Reddit': f'https://www.reddit.com/user/{username}',
            'Keybase': f'https://keybase.io/{username}',
            'HackerOne': f'https://hackerone.com/{username}',
        }
        headers = {'User-Agent': settings.user_agent}
        results = []
        async with httpx.AsyncClient(timeout=self.timeout, headers=headers, follow_redirects=True) as client:
            for site, url in templates.items():
                try:
                    r = await client.get(url)
                    present = r.status_code == 200
                    results.append({'site': site, 'present': present, 'status': r.status_code, 'url': url})
                except Exception:
                    results.append({'site': site, 'present': False, 'status': None, 'url': url})
        return results


advanced_osint = AdvancedOSINTService()
