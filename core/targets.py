import ipaddress
import re
from urllib.parse import urlparse

DOMAIN_RE = re.compile(r"^(?=.{1,253}$)(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,63}$")


def normalize_target(value: str) -> str:
    value = value.strip().lower().rstrip(".")
    if "://" in value:
        parsed = urlparse(value)
        value = (parsed.hostname or "").lower().rstrip(".")
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        pass
    if DOMAIN_RE.fullmatch(value):
        return value
    raise ValueError("Target must be a valid IP address or domain name.")


def normalize_scope(value: str) -> str:
    value = value.strip().lower().rstrip(".")
    try:
        return str(ipaddress.ip_network(value, strict=False))
    except ValueError:
        return normalize_target(value)


def target_matches_scope(target: str, scope: str) -> bool:
    target = normalize_target(target)
    try:
        network = ipaddress.ip_network(scope, strict=False)
        try:
            return ipaddress.ip_address(target) in network
        except ValueError:
            return False
    except ValueError:
        scoped_domain = normalize_target(scope)
        try:
            ipaddress.ip_address(scoped_domain)
            return target == scoped_domain
        except ValueError:
            return target == scoped_domain or target.endswith("." + scoped_domain)


def safe_url(value: str) -> str:
    value = value.strip()
    if not value.startswith(("http://", "https://")):
        value = "https://" + value
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("URL must use http or https and include a hostname.")
    normalize_target(parsed.hostname)
    return value
