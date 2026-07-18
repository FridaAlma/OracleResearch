"""
┌──────────────────────────────────────────────────────────────────┐
│  EnvironmentProbe — Feasibility Reasoning Pre-Flight Checker     │
├──────────────────────────────────────────────────────────────────┤
│  Oracle chiama questo tool PRIMA di costruire tool per task      │
│  complessi. Verifica:                                            │
│                                                                  │
│  ⬡ Port & Host connectivity (TCP socket, DNS, HTTP reachability) │
│  ⬡ Dipendenze installate (pip list, import test, version check)  │
│  ⬡ Permessi filesystem (read/write/execute su path)              │
│  ⬡ Variabili d'ambiente (presenza e valore)                      │
│  ⬡ Report strutturato: available / missing / feasible            │
│                                                                  │
│  Output: EnvironmentReport con feasibility assessment e          │
│  recommendations per pivotare se necessario.                     │
└──────────────────────────────────────────────────────────────────┘
"""

import argparse
import json
import os
import re
import socket
import subprocess
import sys
import textwrap
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Optional, Union


# ═══════════════════════════════════════════════════════════════════
#  DATA MODELS
# ═══════════════════════════════════════════════════════════════════

@dataclass
class PortProbe:
    """Risultato di una verifica di connettività porta."""
    host: str = ""
    port: int = 0
    protocol: str = "tcp"
    reachable: bool = False
    latency_ms: Optional[float] = None
    error: Optional[str] = None
    dns_resolved: Optional[bool] = None
    resolved_ips: list[str] = field(default_factory=list)


@dataclass
class DepProbe:
    """Risultato di una verifica di dipendenza."""
    package: str
    import_name: Optional[str] = None  # nome import (diverso da package pip)
    installed: bool = False
    version: Optional[str] = None
    required_version: Optional[str] = None
    version_ok: bool = True
    error: Optional[str] = None


@dataclass
class FSPermProbe:
    """Risultato di una verifica permessi filesystem."""
    path: str = ""
    exists: bool = False
    readable: bool = False
    writable: bool = False
    executable: bool = False
    is_dir: Optional[bool] = None
    error: Optional[str] = None


@dataclass
class EnvVarProbe:
    """Risultato di una verifica variabile d'ambiente."""
    name: str
    exists: bool = False
    value_set: bool = False       # True se ha valore non-vuoto
    masked_value: str = ""        # versione censurata (es. "sk-...abc")
    error: Optional[str] = None


@dataclass
class ProbeSection:
    """Sezione del report (una categoria di probe)."""
    category: str                 # "connectivity", "dependencies", "filesystem", "environment"
    label: str                    # etichetta human-readable
    passed: int = 0
    failed: int = 0
    warnings: int = 0
    items: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass
class EnvironmentReport:
    """Report strutturato completo del probe."""
    # Metadati
    timestamp: str = ""
    probe_duration_ms: float = 0.0
    hostname: str = ""
    platform: str = ""
    python_version: str = ""

    # Sezioni
    connectivity: ProbeSection = field(default_factory=lambda: ProbeSection("connectivity", "🌐 Connettività"))
    dependencies: ProbeSection = field(default_factory=lambda: ProbeSection("dependencies", "📦 Dipendenze"))
    filesystem: ProbeSection = field(default_factory=lambda: ProbeSection("filesystem", "💾 Filesystem"))
    environment: ProbeSection = field(default_factory=lambda: ProbeSection("environment", "🔧 Variabili d'Ambiente"))

    # Assessment finale
    all_checks_passed: bool = True
    blocker_count: int = 0
    feasibility: str = "UNKNOWN"  # "FEASIBLE" | "FEASIBLE_WITH_WARNINGS" | "BLOCKED" | "UNKNOWN"
    recommendations: list[str] = field(default_factory=list)
    pivot_options: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Serializza in dizionario per output JSON."""
        def _section_dict(s: ProbeSection) -> dict:
            return {
                "category": s.category,
                "label": s.label,
                "passed": s.passed,
                "failed": s.failed,
                "warnings": s.warnings,
                "items": s.items,
                "notes": s.notes,
            }

        return {
            "timestamp": self.timestamp,
            "probe_duration_ms": round(self.probe_duration_ms, 1),
            "hostname": self.hostname,
            "platform": self.platform,
            "python_version": self.python_version,
            "sections": {
                "connectivity": _section_dict(self.connectivity),
                "dependencies": _section_dict(self.dependencies),
                "filesystem": _section_dict(self.filesystem),
                "environment": _section_dict(self.environment),
            },
            "assessment": {
                "all_checks_passed": self.all_checks_passed,
                "blocker_count": self.blocker_count,
                "feasibility": self.feasibility,
                "recommendations": self.recommendations,
                "pivot_options": self.pivot_options,
            },
        }

    def to_text(self, use_ascii: bool = False) -> str:
        """Renderizza il report in testo formattato con box-drawing.

        Args:
            use_ascii: Se True, usa caratteri ASCII al posto di Unicode box-drawing
                       (per compatibilità con terminali Windows cp1252).
        """
        d = self.to_dict()
        lines = []
        w = 64

        if use_ascii:
            h_line = "="
            v_line = "|"
            tl, tr, bl, br = "+", "+", "+", "+"
            cross = "+"
        else:
            h_line = "─"
            v_line = "│"
            tl, tr, bl, br = "┌", "┐", "└", "┘"
            cross = "├"

        lines.append(tl + h_line * w + tr)

        # Header
        title = "ENVIRONMENT PROBE REPORT"
        lines.append(f"{v_line} {title:^{w}} {v_line}")
        lines.append(cross + h_line * w + cross if cross == "├" else cross + h_line * w + cross)
        lines.append(f"{v_line} {'Host:':10s} {d['hostname']:<30s} {'Durata:':8s} {d['probe_duration_ms']}ms  {v_line}")
        lines.append(f"{v_line} {'OS:':10s} {d['platform'][:30]:<30s} {'Python:':8s} {d['python_version']:<13s} {v_line}")
        lines.append(cross + h_line * w + cross if cross == "├" else cross + h_line * w + cross)

        # Sezioni
        for sec_key, sec_data in d["sections"].items():
            label = sec_data["label"]
            # Strip emoji in ASCII mode
            if use_ascii:
                label = re.sub(r'[^\w\s\[\]\(\)\-\.,;:!?]', '', label).strip()
            passed = sec_data["passed"]
            failed = sec_data["failed"]
            warn = sec_data["warnings"]
            if use_ascii:
                status = "[OK]" if failed == 0 else ("[WARN]" if failed == 0 and warn > 0 else "[FAIL]")
            else:
                status = "✅" if failed == 0 else ("⚠️" if failed == 0 and warn > 0 else "❌")
            lines.append(f"{v_line} {status} {label:<30s} OK:{passed} KO:{failed} W:{warn}    {v_line}")

            for item in sec_data["items"]:
                item_status = item.get("status", "?")
                item_label = item.get("label", "")
                item_detail = item.get("detail", "")
                if use_ascii:
                    icon = "[OK]" if item_status == "ok" else ("[WARN]" if item_status == "warn" else "[FAIL]")
                else:
                    icon = "✅" if item_status == "ok" else ("⚠️" if item_status == "warn" else "❌")
                line = f"{v_line}    {icon} {item_label}"
                if item_detail:
                    detail_short = item_detail[:w - len(line) + 6]
                    line += f": {detail_short}"
                lines.append(line.ljust(w + 6) + f"{v_line}")

            for note in sec_data.get("notes", []):
                bulb = "[i]" if use_ascii else "💡"
                note_short = note[:w - 7]
                lines.append(f"{v_line}    {bulb} {note_short:<{w-7}} {v_line}")

        # Assessment
        lines.append(cross + h_line * w + cross if cross == "├" else cross + h_line * w + cross)
        feas = d["assessment"]["feasibility"]
        if use_ascii:
            feas_icon = {"FEASIBLE": "[OK]", "FEASIBLE_WITH_WARNINGS": "[WARN]", "BLOCKED": "[FAIL]", "UNKNOWN": "[???]"}.get(feas, "[???]")
        else:
            feas_icon = {"FEASIBLE": "✅", "FEASIBLE_WITH_WARNINGS": "⚠️", "BLOCKED": "❌", "UNKNOWN": "❓"}.get(feas, "❓")
        lines.append(f"{v_line} {feas_icon} FEASIBILITY: {feas:<{w-18}} {v_line}")
        lines.append(f"{v_line}    Blocker totali: {d['assessment']['blocker_count']:<{w-23}} {v_line}")

        if d["assessment"]["recommendations"]:
            lines.append(f"{v_line}    Raccomandazioni:                                    {v_line}")
            for rec in d["assessment"]["recommendations"]:
                rec_short = rec[:w - 8]
                lines.append(f"{v_line}      * {rec_short:<{w-8}} {v_line}")

        if d["assessment"]["pivot_options"]:
            lines.append(f"{v_line}    Opzioni di pivot:                                   {v_line}")
            for pv in d["assessment"]["pivot_options"]:
                pv_short = pv[:w - 8]
                arrow = "->" if use_ascii else "↪"
                lines.append(f"{v_line}      {arrow} {pv_short:<{w-8}} {v_line}")

        lines.append(bl + h_line * w + br)

        result = "\n".join(lines)
        if use_ascii:
            # Strip emoji e caratteri non-ASCII
            result = result.encode('ascii', errors='replace').decode('ascii')
        return result


# ═══════════════════════════════════════════════════════════════════
#  PROBE ENGINE
# ═══════════════════════════════════════════════════════════════════

class EnvironmentProbe:
    """
    Motore di probe ambientale.

    Usage:
        probe = EnvironmentProbe()
        probe.add_port_check("smtp.gmail.com", 587)
        probe.add_dep_check("requests", min_version="2.28")
        probe.add_fs_check("./workspace", need_write=True)
        probe.add_env_check("OPENAI_API_KEY", required=True)

        report = probe.run()
        print(report.to_text())

        if report.feasibility == "BLOCKED":
            print("Task non fattibile nell'ambiente corrente.")
            for pv in report.pivot_options:
                print(f"  → {pv}")
    """

    def __init__(self):
        self._port_checks: list[dict] = []
        self._dep_checks: list[dict] = []
        self._fs_checks: list[dict] = []
        self._env_checks: list[dict] = []

        # Info di sistema
        self._hostname = socket.gethostname()
        self._platform = sys.platform
        self._py_version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"

    # ── Registrazione check ────────────────────────────────────

    def add_port_check(self, host: str, port: int, protocol: str = "tcp",
                       label: str = "", required: bool = True):
        """
        Aggiunge una verifica di connettività TCP/UDP.

        Args:
            host: Hostname o IP
            port: Numero porta
            protocol: "tcp" (default) o "udp"
            label: Descrizione human-readable (es. "SMTP Gmail")
            required: Se True, il fallimento è un blocker
        """
        self._port_checks.append({
            "host": host, "port": port, "protocol": protocol,
            "label": label or f"{host}:{port}/{protocol}",
            "required": required,
        })

    def add_dep_check(self, package: str, import_name: Optional[str] = None,
                      min_version: Optional[str] = None, max_version: Optional[str] = None,
                      label: str = "", required: bool = True):
        """
        Aggiunge una verifica di dipendenza Python.

        Args:
            package: Nome pacchetto pip (es. "requests")
            import_name: Nome modulo da importare (default: package)
            min_version: Versione minima richiesta (es. "2.28.0")
            max_version: Versione massima consentita
            label: Descrizione human-readable
            required: Se True, il fallimento è un blocker
        """
        self._dep_checks.append({
            "package": package,
            "import_name": import_name or package,
            "min_version": min_version,
            "max_version": max_version,
            "label": label or package,
            "required": required,
        })

    def add_fs_check(self, path: str, need_read: bool = True, need_write: bool = False,
                     need_exec: bool = False, must_exist: bool = False,
                     label: str = "", required: bool = True):
        """
        Aggiunge una verifica di permessi filesystem.

        Args:
            path: Percorso da verificare
            need_read: Richiede permesso lettura
            need_write: Richiede permesso scrittura
            need_exec: Richiede permesso esecuzione
            must_exist: Il path deve esistere
            label: Descrizione human-readable
            required: Se True, il fallimento è un blocker
        """
        self._fs_checks.append({
            "path": path,
            "need_read": need_read,
            "need_write": need_write,
            "need_exec": need_exec,
            "must_exist": must_exist,
            "label": label or path,
            "required": required,
        })

    def add_env_check(self, name: str, required: bool = True,
                      label: str = ""):
        """
        Aggiunge una verifica di variabile d'ambiente.

        Args:
            name: Nome della variabile (es. "OPENAI_API_KEY")
            required: Se True, il fallimento è un blocker
            label: Descrizione human-readable
        """
        self._env_checks.append({
            "name": name,
            "label": label or name,
            "required": required,
        })

    # ── API bulk ───────────────────────────────────────────────

    def add_requirements(self, requirements: dict):
        """
        Registra tutte le verifiche in un colpo solo da un dizionario.

        Esempio:
            probe.add_requirements({
                "ports": [
                    {"host": "smtp.gmail.com", "port": 587, "label": "SMTP Gmail"},
                ],
                "deps": [
                    {"package": "requests", "min_version": "2.28"},
                ],
                "filesystem": [
                    {"path": "./workspace", "need_write": True},
                ],
                "environment": [
                    {"name": "OPENAI_API_KEY", "required": True},
                ],
            })
        """
        for p in requirements.get("ports", []):
            self.add_port_check(**p)
        for d in requirements.get("deps", []):
            self.add_dep_check(**d)
        for f in requirements.get("filesystem", []):
            self.add_fs_check(**f)
        for e in requirements.get("environment", []):
            self.add_env_check(**e)

    # ── Esecuzione probe ───────────────────────────────────────

    def run(self) -> EnvironmentReport:
        """Esegue tutti i probe registrati e restituisce il report."""
        start = time.time()
        report = EnvironmentReport(
            timestamp=time.strftime("%Y-%m-%dT%H:%M:%S"),
            hostname=self._hostname,
            platform=self._platform,
            python_version=self._py_version,
        )

        # 1. Connettività
        for pc in self._port_checks:
            result = self._probe_port(pc["host"], pc["port"], pc["protocol"])
            item = {
                "status": "ok" if result.reachable else "error",
                "label": pc["label"],
                "detail": f"{'✅' if result.reachable else '❌'} {pc['host']}:{pc['port']}/{pc['protocol']}",
                "host": result.host,
                "port": result.port,
                "reachable": result.reachable,
                "latency_ms": result.latency_ms,
                "error": result.error,
                "resolved_ips": result.resolved_ips,
                "required": pc.get("required", True),
            }
            if result.reachable:
                report.connectivity.passed += 1
            else:
                report.connectivity.failed += 1
            report.connectivity.items.append(item)

        # 2. Dipendenze
        for dc in self._dep_checks:
            result = self._probe_dep(
                dc["package"], dc["import_name"],
                dc.get("min_version"), dc.get("max_version"),
            )
            is_blocker = dc.get("required", True)
            if result.installed and result.version_ok:
                status = "ok"
                report.dependencies.passed += 1
            elif result.installed and not result.version_ok:
                status = "warn"
                report.dependencies.warnings += 1
            else:
                status = "error" if is_blocker else "warn"
                if is_blocker:
                    report.dependencies.failed += 1
                else:
                    report.dependencies.warnings += 1

            ver_info = f"v{result.version}" if result.version else "non installato"
            if result.required_version:
                ver_info += f" (richiesta: {result.required_version})"

            item = {
                "status": status,
                "label": dc["label"],
                "detail": ver_info,
                "package": result.package,
                "installed": result.installed,
                "version": result.version,
                "version_ok": result.version_ok,
                "required_version": result.required_version,
                "error": result.error,
                "required": is_blocker,
            }
            report.dependencies.items.append(item)

        # 3. Filesystem
        for fc in self._fs_checks:
            result = self._probe_fs(
                fc["path"], fc["need_read"], fc["need_write"],
                fc["need_exec"], fc["must_exist"],
            )
            is_blocker = fc.get("required", True)
            all_ok = True
            issues = []

            if fc["must_exist"] and not result.exists:
                all_ok = False
                issues.append("path non esiste")
            if fc["need_read"] and not result.readable:
                all_ok = False
                issues.append("no read")
            if fc["need_write"] and not result.writable:
                all_ok = False
                issues.append("no write")
            if fc["need_exec"] and not result.executable:
                all_ok = False
                issues.append("no exec")

            status = "ok" if all_ok else ("error" if is_blocker else "warn")
            if all_ok:
                report.filesystem.passed += 1
            elif is_blocker:
                report.filesystem.failed += 1
            else:
                report.filesystem.warnings += 1

            perms = []
            if result.readable:
                perms.append("r")
            if result.writable:
                perms.append("w")
            if result.executable:
                perms.append("x")
            perms_str = "".join(perms) if perms else "---"

            detail = f"{'esiste' if result.exists else 'manca'} | perm: {perms_str}"
            if issues:
                detail += f" | issues: {', '.join(issues)}"

            item = {
                "status": status,
                "label": fc["label"],
                "detail": detail,
                "path": result.path,
                "exists": result.exists,
                "readable": result.readable,
                "writable": result.writable,
                "executable": result.executable,
                "is_dir": result.is_dir,
                "error": result.error,
                "required": is_blocker,
            }
            report.filesystem.items.append(item)

        # 4. Variabili ambiente
        for ec in self._env_checks:
            result = self._probe_env(ec["name"])
            is_blocker = ec.get("required", True)

            if result.exists and result.value_set:
                status = "ok"
                report.environment.passed += 1
            elif result.exists and not result.value_set:
                status = "warn"
                report.environment.warnings += 1
            else:
                status = "error" if is_blocker else "warn"
                if is_blocker:
                    report.environment.failed += 1
                else:
                    report.environment.warnings += 1

            detail = f"presente: {result.exists}, valorizzata: {result.value_set}"
            if result.masked_value:
                detail += f" → {result.masked_value}"

            item = {
                "status": status,
                "label": ec["label"],
                "detail": detail,
                "name": result.name,
                "exists": result.exists,
                "value_set": result.value_set,
                "masked_value": result.masked_value,
                "required": is_blocker,
            }
            report.environment.items.append(item)

        # ── Assessment finale ──
        total_blockers = (
            report.connectivity.failed +
            report.dependencies.failed +
            report.filesystem.failed +
            report.environment.failed
        )
        report.blocker_count = total_blockers

        if total_blockers == 0:
            total_warnings = (
                report.connectivity.warnings +
                report.dependencies.warnings +
                report.filesystem.warnings +
                report.environment.warnings
            )
            report.feasibility = "FEASIBLE" if total_warnings == 0 else "FEASIBLE_WITH_WARNINGS"
            report.all_checks_passed = (total_warnings == 0)
        else:
            report.feasibility = "BLOCKED"
            report.all_checks_passed = False

        # Genera raccomandazioni e pivot
        report.recommendations = self._make_recommendations(report)
        report.pivot_options = self._make_pivot_options(report)

        report.probe_duration_ms = (time.time() - start) * 1000
        return report

    # ── Probe individuali ─────────────────────────────────────

    def _probe_port(self, host: str, port: int, protocol: str) -> PortProbe:
        """Verifica connettività TCP a host:port."""
        result = PortProbe(host=host, port=port, protocol=protocol)
        start = time.time()

        # DNS resolution
        try:
            addrs = socket.getaddrinfo(host, port, socket.AF_UNSPEC,
                                       socket.SOCK_STREAM if protocol == "tcp" else socket.SOCK_DGRAM)
            result.dns_resolved = True
            result.resolved_ips = list(set(a[4][0] for a in addrs))
        except socket.gaierror as e:
            result.dns_resolved = False
            result.error = f"DNS: {e}"
            return result

        if protocol != "tcp":
            result.reachable = True  # UDP: non possiamo verificare reachability in modo semplice
            result.error = "UDP probe non supporta verifica reachability"
            return result

        # TCP connect
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(5.0)
            sock.connect((host, port))
            result.latency_ms = round((time.time() - start) * 1000, 1)
            result.reachable = True
            sock.close()
        except (socket.timeout, ConnectionRefusedError, OSError) as e:
            result.reachable = False
            result.error = str(e)
            result.latency_ms = round((time.time() - start) * 1000, 1)

        return result

    def _probe_dep(self, package: str, import_name: str,
                   min_version: Optional[str], max_version: Optional[str]) -> DepProbe:
        """Verifica installazione e versione di un pacchetto Python."""
        result = DepProbe(
            package=package,
            import_name=import_name,
            required_version=min_version,
        )

        # Tenta import
        try:
            mod = __import__(import_name)
            result.installed = True
        except ImportError:
            result.installed = False
            result.error = f"ImportError: nessun modulo '{import_name}'"
            return result

        # Estrai versione
        version = None
        for attr in ("__version__", "VERSION", "version"):
            if hasattr(mod, attr):
                v = getattr(mod, attr)
                if isinstance(v, str):
                    version = v
                elif isinstance(v, tuple):
                    version = ".".join(str(x) for x in v)
                break

        if version is None:
            # Fallback: pip show
            try:
                out = subprocess.run(
                    [sys.executable, "-m", "pip", "show", package],
                    capture_output=True, text=True, timeout=10,
                )
                if out.returncode == 0:
                    for line in out.stdout.splitlines():
                        if line.startswith("Version:"):
                            version = line.split(":", 1)[1].strip()
                            break
            except Exception:
                pass

        result.version = version

        # Version check
        if min_version and version:
            result.version_ok = self._version_ge(version, min_version)
            if not result.version_ok:
                result.error = f"Versione {version} < richiesta {min_version}"
        if max_version and version and result.version_ok:
            result.version_ok = self._version_le(version, max_version)
            if not result.version_ok:
                result.error = f"Versione {version} > massima {max_version}"

        return result

    def _probe_fs(self, path: str, need_read: bool, need_write: bool,
                  need_exec: bool, must_exist: bool) -> FSPermProbe:
        """Verifica permessi filesystem su un path."""
        result = FSPermProbe(path=path)
        p = Path(path).expanduser().resolve()

        result.path = str(p)
        result.exists = p.exists()
        result.is_dir = p.is_dir() if p.exists() else None

        if must_exist and not result.exists:
            result.error = f"Path non esiste: {p}"
            return result

        if result.exists:
            result.readable = os.access(p, os.R_OK)
            result.writable = os.access(p, os.W_OK)
            result.executable = os.access(p, os.X_OK)
        else:
            # Verifica permessi sulla directory padre
            parent = p.parent
            if parent.exists():
                result.writable = os.access(parent, os.W_OK)
                result.readable = os.access(parent, os.R_OK)
            result.readable = result.readable or False
            result.writable = result.writable or False
            result.executable = False

        return result

    def _probe_env(self, name: str) -> EnvVarProbe:
        """Verifica presenza e valore di una variabile d'ambiente."""
        result = EnvVarProbe(name=name)
        value = os.environ.get(name)

        if value is not None:
            result.exists = True
            result.value_set = bool(value.strip())
            # Maschera il valore
            if len(value) > 8:
                result.masked_value = value[:4] + "..." + value[-4:]
            elif value:
                result.masked_value = value[:2] + "***"
        else:
            result.exists = False
            result.value_set = False

        return result

    # ── Utility ────────────────────────────────────────────────

    @staticmethod
    def _version_ge(v1: str, v2: str) -> bool:
        """Confronta versioni: v1 >= v2."""
        def _norm(v: str) -> tuple:
            return tuple(int(x) for x in re.findall(r'\d+', v))
        try:
            return _norm(v1) >= _norm(v2)
        except Exception:
            return True  # non blocchiamo se il parsing fallisce

    @staticmethod
    def _version_le(v1: str, v2: str) -> bool:
        try:
            return EnvironmentProbe._version_ge(v2, v1)
        except Exception:
            return True

    def _make_recommendations(self, report: EnvironmentReport) -> list[str]:
        """Genera raccomandazioni basate sui fallimenti."""
        recs = []

        # Connettività
        failed_ports = [i for i in report.connectivity.items if i["status"] == "error"]
        if failed_ports:
            hosts = ", ".join(f"{i['host']}:{i['port']}" for i in failed_ports)
            recs.append(f"Porte non raggiungibili: {hosts}. Verifica firewall/VPN.")

        # Dipendenze
        failed_deps = [i for i in report.dependencies.items if i["status"] == "error"]
        if failed_deps:
            pkgs = " ".join(i["package"] for i in failed_deps)
            recs.append(f"Dipendenze mancanti: {pkgs}. Esegui: pip install {pkgs}")

        warn_deps = [i for i in report.dependencies.items if i["status"] == "warn"]
        if warn_deps:
            pkgs = ", ".join(f"{i['package']} (v{i.get('version','?')}, richiesta {i.get('required_version','?')})"
                           for i in warn_deps)
            recs.append(f"Versioni non compatibili: {pkgs}")

        # Filesystem
        failed_fs = [i for i in report.filesystem.items if i["status"] == "error"]
        if failed_fs:
            paths = ", ".join(i["path"] for i in failed_fs)
            recs.append(f"Permessi filesystem insufficienti: {paths}")

        # Env vars
        failed_env = [i for i in report.environment.items if i["status"] == "error"]
        if failed_env:
            vars_ = ", ".join(i["name"] for i in failed_env)
            recs.append(f"Variabili d'ambiente mancanti: {vars_}. Impostale in .env")

        return recs

    def _make_pivot_options(self, report: EnvironmentReport) -> list[str]:
        """Suggerisce approcci alternativi se ci sono blocker."""
        pivots = []

        # Porte bloccate → suggerisci alternative
        failed_ports = [i for i in report.connectivity.items if i["status"] == "error"]
        if failed_ports:
            for fp in failed_ports:
                if fp["port"] == 587:  # SMTP submission
                    pivots.append(f"Porta {fp['port']} bloccata → usa API REST email (SendGrid/Mailgun) invece di SMTP diretto")
                elif fp["port"] == 25:
                    pivots.append(f"Porta 25 bloccata → usa porta 587 (submission) o 465 (SMTPS) o API email provider")
                elif fp["port"] == 443:
                    pivots.append(f"HTTPS bloccato su {fp['host']} → verifica proxy/firewall, o usa mirror alternativo")
                else:
                    pivots.append(f"Porta {fp['host']}:{fp['port']} non raggiungibile → cerca API HTTP alternative su porta 80/443")

        # Dipendenze mancanti → suggerisci alternative built-in
        failed_deps = [i for i in report.dependencies.items if i["status"] == "error"]
        if failed_deps:
            for fd in failed_deps:
                pkg = fd["package"]
                alt = self._suggest_builtin_alternative(pkg)
                if alt:
                    pivots.append(f"{pkg} non installato → puoi usare {alt} (modulo built-in)")

        # Variabili mancanti
        failed_env = [i for i in report.environment.items if i["status"] == "error"]
        if failed_env:
            for fe in failed_env:
                pivots.append(f"Variabile {fe['name']} non impostata → chiedi all'utente o cerca in .env / .env.example")

        return pivots

    @staticmethod
    def _suggest_builtin_alternative(package: str) -> Optional[str]:
        """Mappa pacchetti esterni ad alternative built-in Python."""
        mapping = {
            "requests": "urllib.request (stdlib)",
            "httpx": "urllib.request (stdlib)",
            "aiohttp": "asyncio + urllib (stdlib)",
            "beautifulsoup4": "html.parser (stdlib, modulo html)",
            "lxml": "xml.etree.ElementTree (stdlib)",
            "pillow": "tkinter o generazione con array numpy",
            "numpy": "array + math (stdlib)",
            "pandas": "csv.DictReader (stdlib)",
            "rich": "print + ANSI escape codes",
            "click": "argparse (stdlib)",
            "tqdm": "print periodico su stderr",
            "sqlalchemy": "sqlite3 (stdlib)",
            "flask": "http.server (stdlib)",
            "fastapi": "http.server (stdlib)",
            "uvicorn": "http.server (stdlib)",
            "pydantic": "dataclasses (stdlib)",
            "celery": "threading + queue (stdlib)",
            "redis": "sqlite3 come cache (stdlib)",
        }
        return mapping.get(package.lower())


# ═══════════════════════════════════════════════════════════════════
#  HIGH-LEVEL API
# ═══════════════════════════════════════════════════════════════════

def quick_probe(requirements: dict) -> EnvironmentReport:
    """
    API rapida: passa un dizionario di requisiti e ottieni il report.

    Esempio:
        report = quick_probe({
            "ports": [{"host": "smtp.gmail.com", "port": 587}],
            "deps": [{"package": "requests", "min_version": "2.28"}],
            "filesystem": [{"path": "./workspace", "need_write": True}],
            "environment": [{"name": "OPENAI_API_KEY"}],
        })
        if report.feasibility == "BLOCKED":
            print(report.to_text())
    """
    probe = EnvironmentProbe()
    probe.add_requirements(requirements)
    return probe.run()


def is_feasible(requirements: dict) -> tuple[bool, EnvironmentReport]:
    """
    API booleana: True se il task è fattibile, False altrimenti.

    Returns: (feasible, report)
    """
    report = quick_probe(requirements)
    return report.feasibility != "BLOCKED", report


# ═══════════════════════════════════════════════════════════════════
#  CLI
# ═══════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        prog="environment_probe",
        description="[ENV] EnvironmentProbe — Feasibility Pre-Flight Checker per Oracle",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            Esempi:
              # Verifica connettività porta
              python tools/environment_probe.py port --host smtp.gmail.com --port 587

              # Verifica dipendenza
              python tools/environment_probe.py dep --package requests --min-version 2.28

              # Verifica permessi filesystem
              python tools/environment_probe.py fs --path ./workspace --need-write

              # Verifica variabile d'ambiente
              python tools/environment_probe.py env --name OPENAI_API_KEY

              # Verifica multipla da file JSON
              python tools/environment_probe.py check --from-json requirements.json

              # Verifica multipla inline (pipe)
              echo '{"deps":[{"package":"requests","min_version":"2.28"}],"ports":[{"host":"google.com","port":443}]}' | python tools/environment_probe.py check --stdin

              # Output JSON
              python tools/environment_probe.py check --from-json reqs.json --json
        """),
    )

    sub = parser.add_subparsers(dest="command", help="Comando")

    # ── port ──
    p_port = sub.add_parser("port", help="Verifica connettività porta")
    p_port.add_argument("--host", "-H", required=True, help="Hostname o IP")
    p_port.add_argument("--port", "-p", type=int, required=True, help="Numero porta")
    p_port.add_argument("--protocol", default="tcp", choices=["tcp", "udp"], help="Protocollo (default: tcp)")
    p_port.add_argument("--label", help="Etichetta human-readable")
    p_port.add_argument("--json", action="store_true", help="Output JSON")
    p_port.add_argument("--ascii", action="store_true", help="Usa ASCII al posto di Unicode (auto su Windows)")

    # ── dep ──
    p_dep = sub.add_parser("dep", help="Verifica dipendenza Python")
    p_dep.add_argument("--package", "-p", required=True, help="Nome pacchetto pip")
    p_dep.add_argument("--import-name", help="Nome modulo import (se diverso da package)")
    p_dep.add_argument("--min-version", help="Versione minima richiesta")
    p_dep.add_argument("--max-version", help="Versione massima consentita")
    p_dep.add_argument("--label", help="Etichetta human-readable")
    p_dep.add_argument("--json", action="store_true", help="Output JSON")
    p_dep.add_argument("--ascii", action="store_true", help="Usa ASCII al posto di Unicode (auto su Windows)")

    # ── fs ──
    p_fs = sub.add_parser("fs", help="Verifica permessi filesystem")
    p_fs.add_argument("--path", required=True, help="Percorso da verificare")
    p_fs.add_argument("--need-read", action="store_true", help="Richiede permesso lettura")
    p_fs.add_argument("--need-write", action="store_true", help="Richiede permesso scrittura")
    p_fs.add_argument("--need-exec", action="store_true", help="Richiede permesso esecuzione")
    p_fs.add_argument("--must-exist", action="store_true", help="Il path deve esistere")
    p_fs.add_argument("--label", help="Etichetta human-readable")
    p_fs.add_argument("--json", action="store_true", help="Output JSON")
    p_fs.add_argument("--ascii", action="store_true", help="Usa ASCII al posto di Unicode (auto su Windows)")

    # ── env ──
    p_env = sub.add_parser("env", help="Verifica variabile d'ambiente")
    p_env.add_argument("--name", "-n", required=True, help="Nome variabile")
    p_env.add_argument("--label", help="Etichetta human-readable")
    p_env.add_argument("--json", action="store_true", help="Output JSON")
    p_env.add_argument("--ascii", action="store_true", help="Usa ASCII al posto di Unicode (auto su Windows)")

    # ── check (multiplo da file JSON o stdin) ──
    p_check = sub.add_parser("check", help="Verifica multipla da file JSON o stdin")
    p_check.add_argument("--from-json", "-f", help="File JSON con requisiti")
    p_check.add_argument("--stdin", action="store_true", help="Leggi requisiti da stdin")
    p_check.add_argument("--json", "-j", action="store_true", help="Output JSON")
    p_check.add_argument("--quiet", "-q", action="store_true", help="Solo feasibility status")
    p_check.add_argument("--text", "-t", action="store_true", help="Output testo formattato (default)")
    p_check.add_argument("--ascii", action="store_true", help="Usa ASCII al posto di Unicode (auto su Windows)")

    args = parser.parse_args()
    probe = EnvironmentProbe()

    # Auto-detect Windows → ASCII mode per evitare errori di encoding
    use_ascii = getattr(args, 'ascii', False) or sys.platform == "win32"

    try:
        if args.command == "port":
            probe.add_port_check(
                host=args.host, port=args.port, protocol=args.protocol,
                label=args.label or f"{args.host}:{args.port}",
            )
            report = probe.run()
            if args.json:
                print(json.dumps(report.to_dict(), indent=2, ensure_ascii=True))
            else:
                print(report.to_text(use_ascii=use_ascii))

        elif args.command == "dep":
            probe.add_dep_check(
                package=args.package,
                import_name=args.import_name,
                min_version=args.min_version,
                max_version=args.max_version,
                label=args.label or args.package,
            )
            report = probe.run()
            if args.json:
                print(json.dumps(report.to_dict(), indent=2, ensure_ascii=True))
            else:
                print(report.to_text(use_ascii=use_ascii))

        elif args.command == "fs":
            probe.add_fs_check(
                path=args.path,
                need_read=args.need_read,
                need_write=args.need_write,
                need_exec=args.need_exec,
                must_exist=args.must_exist,
                label=args.label or args.path,
            )
            report = probe.run()
            if args.json:
                print(json.dumps(report.to_dict(), indent=2, ensure_ascii=True))
            else:
                print(report.to_text(use_ascii=use_ascii))

        elif args.command == "env":
            probe.add_env_check(
                name=args.name,
                label=args.label or args.name,
            )
            report = probe.run()
            if args.json:
                print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
            else:
                print(report.to_text(use_ascii=use_ascii))

        elif args.command == "check":
            import io

            if args.from_json:
                with open(args.from_json, "r", encoding="utf-8") as f:
                    reqs = json.load(f)
            elif args.stdin:
                raw = sys.stdin.read()
                reqs = json.loads(raw)
            else:
                print("ERRORE: specifica --from-json o --stdin per check multiplo", file=sys.stderr)
                sys.exit(1)

            probe.add_requirements(reqs)
            report = probe.run()

            if args.quiet:
                print(report.feasibility)
                sys.exit(0 if report.feasibility != "BLOCKED" else 1)
            elif args.json:
                print(json.dumps(report.to_dict(), indent=2, ensure_ascii=True))
            else:
                print(report.to_text(use_ascii=use_ascii))

            sys.exit(0 if report.feasibility != "BLOCKED" else 1)

        else:
            parser.print_help()

    except Exception as e:
        print(f"[ERR] {e}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()