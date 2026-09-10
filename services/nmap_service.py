import asyncio
import re
from dataclasses import dataclass
from core.config import settings


@dataclass
class PortFinding:
    port: int
    protocol: str
    state: str
    service: str
    version: str = ""


@dataclass
class ScanResult:
    target: str
    ports: list[PortFinding]
    raw: str


class NmapService:
    def __init__(self) -> None:
        self._sem = asyncio.Semaphore(settings.max_active_scans)

    async def quick_scan(self, target: str) -> ScanResult:
        ports = "21,22,25,53,80,110,143,443,445,465,587,993,995,1433,1521,3306,3389,5432,5900,6379,8080,8443,9200,27017"
        return await self._run(target, [
            "nmap", "-sT", "-n", "-Pn", "--open", "-T3",
            "--max-retries", "1", "--host-timeout", f"{settings.scan_timeout_seconds}s",
            "-p", ports, target,
        ])

    async def service_scan(self, target: str) -> ScanResult:
        ports = "21,22,25,53,80,443,445,3306,3389,5432,8080,8443"
        return await self._run(target, [
            "nmap", "-sT", "-n", "-Pn", "--open", "-T3", "-sV", "--version-light",
            "--max-retries", "1", "--host-timeout", f"{settings.scan_timeout_seconds}s",
            "-p", ports, target,
        ])

    async def _run(self, target: str, args: list[str]) -> ScanResult:
        async with self._sem:
            try:
                proc = await asyncio.create_subprocess_exec(
                    *args,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
            except FileNotFoundError as exc:
                raise RuntimeError("nmap is not installed on this host") from exc

            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(), timeout=settings.scan_timeout_seconds + 10
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.communicate()
                raise RuntimeError("scan timed out")

            text = stdout.decode(errors="replace")
            if proc.returncode != 0:
                raise RuntimeError(stderr.decode(errors="replace").strip() or "nmap failed")

            findings: list[PortFinding] = []
            for line in text.splitlines():
                m = re.match(r"^(\d+)/(tcp|udp)\s+(open)\s+([^\s]+)(?:\s+(.*))?$", line.strip())
                if m:
                    findings.append(PortFinding(
                        port=int(m.group(1)), protocol=m.group(2), state=m.group(3),
                        service=m.group(4), version=(m.group(5) or "")[:100],
                    ))
            return ScanResult(target=target, ports=findings, raw=text)


nmap_service = NmapService()
