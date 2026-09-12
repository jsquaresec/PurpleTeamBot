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
        return await self._tcp_scan(target, ports)

    async def service_scan(self, target: str) -> ScanResult:
        ports = "21,22,25,53,80,443,445,3306,3389,5432,8080,8443"
        return await self._tcp_scan(target, ports, service=True)

    async def web_scan(self, target: str) -> ScanResult:
        ports = "80,81,443,444,8000,8008,8080,8081,8088,8181,8443,8888,9000,9090,9443"
        return await self._tcp_scan(target, ports, service=True)

    async def infrastructure_scan(self, target: str) -> ScanResult:
        ports = "22,23,25,53,67,68,69,110,111,123,135,137,138,139,143,161,162,389,445,465,514,587,636,873,993,995,2049,3389,5900"
        return await self._tcp_scan(target, ports, service=True)

    async def database_scan(self, target: str) -> ScanResult:
        ports = "1433,1521,1830,2181,2379,2380,27017,28017,3306,5432,5984,6379,7474,7687,9042,9200,9300,11211"
        return await self._tcp_scan(target, ports, service=True)

    async def extended_scan(self, target: str) -> ScanResult:
        # Broader but still bounded to a curated TCP set so it remains lightweight.
        ports = (
            "20,21,22,23,25,53,69,80,81,88,110,111,119,123,135,137,139,143,161,389,443,445,"
            "465,514,515,587,631,636,873,993,995,1080,1433,1521,1723,1883,2049,2181,2379,2380,"
            "3000,3306,3389,4369,5000,5432,5672,5900,5984,6379,6443,7001,7077,7474,7687,8000,"
            "8008,8080,8081,8088,8181,8443,8888,9000,9042,9090,9200,9300,9443,11211,15672,27017"
        )
        return await self._tcp_scan(target, ports, service=True)

    async def _tcp_scan(self, target: str, ports: str, service: bool = False) -> ScanResult:
        args = [
            "nmap", "-sT", "-n", "-Pn", "--open", "-T3",
            "--max-retries", "1", "--host-timeout", f"{settings.scan_timeout_seconds}s",
        ]
        if service:
            args.extend(["-sV", "--version-light"])
        args.extend(["-p", ports, target])
        return await self._run(target, args)

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
