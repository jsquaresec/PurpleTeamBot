import re

import httpx

from core.config import settings
from core.targets import normalize_target


class AssessmentService:
    def __init__(self) -> None:
        self.timeout = httpx.Timeout(settings.http_timeout_seconds)
        self.headers = {'User-Agent': settings.user_agent}

    async def web_fingerprint(self, target: str) -> dict:
        host = normalize_target(target)
        url = f'https://{host}'
        async with httpx.AsyncClient(timeout=self.timeout, headers=self.headers, follow_redirects=True) as client:
            r = await client.get(url)
            body = r.text[:65536].lower()
            technologies = []
            checks = {
                'WordPress': ('wp-content', 'wp-includes'),
                'Drupal': ('drupal-settings-json', '/sites/default/'),
                'Joomla': ('joomla', '/media/system/js/'),
                'React': ('react', '__next_data__'),
                'Next.js': ('/_next/', '__next_data__'),
                'Cloudflare': ('cf-ray',),
            }
            raw_headers = '\n'.join(f'{k}: {v}' for k, v in r.headers.items()).lower()
            for name, needles in checks.items():
                if any(n in body or n in raw_headers for n in needles):
                    technologies.append(name)
            generator = None
            match = re.search(r'<meta[^>]+name=["\']generator["\'][^>]+content=["\']([^"\']+)', body, re.I)
            if match:
                generator = match.group(1)[:120]
            return {
                'url': str(r.url),
                'status': r.status_code,
                'server': r.headers.get('server', ''),
                'powered_by': r.headers.get('x-powered-by', ''),
                'generator': generator,
                'technologies': technologies,
            }

    async def exposed_paths(self, target: str) -> list[dict]:
        host = normalize_target(target)
        base = f'https://{host}'
        paths = [
            '/.well-known/security.txt',
            '/robots.txt',
            '/sitemap.xml',
            '/.git/HEAD',
            '/.env',
            '/server-status',
            '/phpinfo.php',
            '/actuator/health',
        ]
        results = []
        async with httpx.AsyncClient(timeout=self.timeout, headers=self.headers, follow_redirects=False) as client:
            for path in paths:
                try:
                    r = await client.get(base + path, headers={**self.headers, 'Range': 'bytes=0-255'})
                    results.append({
                        'path': path,
                        'status': r.status_code,
                        'content_type': r.headers.get('content-type', ''),
                        'content_length': r.headers.get('content-length', ''),
                        'interesting': r.status_code in (200, 206),
                    })
                except Exception:
                    results.append({'path': path, 'status': None, 'interesting': False})
        return results

    async def allowed_methods(self, target: str) -> dict:
        host = normalize_target(target)
        async with httpx.AsyncClient(timeout=self.timeout, headers=self.headers, follow_redirects=True) as client:
            r = await client.options(f'https://{host}')
            allow = r.headers.get('allow', '')
            return {'status': r.status_code, 'allow': allow, 'dav': r.headers.get('dav', '')}


assessment_service = AssessmentService()
