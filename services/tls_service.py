import asyncio
import ssl
from datetime import datetime, timezone

from core.config import settings
from core.targets import normalize_target


async def inspect_tls(target: str, port: int = 443) -> dict:
    host = normalize_target(target)
    context = ssl.create_default_context()
    reader = writer = None
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port, ssl=context, server_hostname=host),
            timeout=settings.http_timeout_seconds,
        )
        ssl_obj = writer.get_extra_info("ssl_object")
        cert = ssl_obj.getpeercert() if ssl_obj else {}
        cipher = ssl_obj.cipher() if ssl_obj else None
        not_after = cert.get("notAfter", "")
        days_remaining = None
        if not_after:
            expires = datetime.strptime(not_after, "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc)
            days_remaining = (expires - datetime.now(timezone.utc)).days
        return {
            "host": host,
            "port": port,
            "protocol": ssl_obj.version() if ssl_obj else "unknown",
            "cipher": cipher[0] if cipher else "unknown",
            "issuer": cert.get("issuer", []),
            "subject": cert.get("subject", []),
            "not_after": not_after,
            "days_remaining": days_remaining,
            "san_count": len(cert.get("subjectAltName", [])),
        }
    finally:
        if writer is not None:
            writer.close()
            await writer.wait_closed()
