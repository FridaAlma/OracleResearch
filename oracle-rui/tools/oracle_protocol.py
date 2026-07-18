#!/usr/bin/env python3
"""
🌀 Oracle Protocol — Orchestratore Integrato per Decision-Making Multi-Livello.

Integra i 3 pilastri dell'Oracle Protocol:
  1. MCTS Engine        → esplorazione albero decisionale
  2. Interleaved Sandbox → esecuzione codice sicura
  3. Semantic Context Filter → contesto compresso + anti-drift

Architettura:
  ComplexityDetector  → classifica task: simple | standard | complex
  ProtocolLoop        → ciclo: MCTS → Sandbox → SCF → ripeti
  ProtocolMonitor     → metriche: token, rami, esecuzioni, drift, durata
  OracleProtocol      → engine principale (punto d'ingresso unico)

Flusso:
  1. analyze(task, context) → ComplexityDetector → tier
  2. Se simple:  risposta diretta Flash
  3. Se standard: Flash + Sandbox (verifica codice)
  4. Se complex:  MCTS → Approccio migliore → Sandbox → SCF → iterazione

CLI:
  python tools/oracle_protocol.py analyze --task "Refactor per async" [--context "..."]
  python tools/oracle_protocol.py simple --task "Cosa e' una variabile?"
  python tools/oracle_protocol.py status

Python:
  from tools.oracle_protocol import OracleProtocol
  op = OracleProtocol()
  result = op.analyze(task="Refactor modulo CRUD per async/await")
  print(result['response'][:500])
"""

import argparse
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from dotenv import load_dotenv
load_dotenv()

# ── LLM Logging ─────────────────────────────────────────────────
try:
    from llm_logger import log_llm_call as _log_llm
except ImportError:
    def _log_llm(*args, **kwargs):
        pass

def _log_openai_call(caller_tag: str, duration: float, resp, model_id: str):
    """Log a direct openai call with token counts."""
    try:
        usage = resp.usage
        inp = usage.prompt_tokens if usage else 0
        out = usage.completion_tokens if usage else 0
    except Exception:
        inp, out = 0, 0
    _log_llm(
        caller_tag=caller_tag,
        duration_sec=duration,
        input_tokens=inp,
        output_tokens=out,
        model_id=model_id or getattr(resp, "model", ""),
        metadata={"provider": "openai_direct"},
    )

# ── Paths ─────────────────────────────────────────────────────────────────

BASE_DIR = Path(__file__).parent.parent.resolve()

# ── Import dei 4 tool (LAZY - solo quando necessario) ──────────────────────

_HAS_MCTS = None
_HAS_SANDBOX = None  
_HAS_SCF = None
_HAS_IDENTITY = None

def _check_module(name: str) -> bool:
    """Verifica rapidamente se un modulo esiste via filesystem (senza import)."""
    return (BASE_DIR / "tools" / f"{name}.py").exists()

def _get_mcts():
    global _HAS_MCTS
    if _HAS_MCTS is None:
        if not _check_module("mcts_engine"):
            _HAS_MCTS = False
        else:
            try:
                sys.path.insert(0, str(BASE_DIR))
                from tools.mcts_engine import MCTSEngine, MCTSConfig
                _HAS_MCTS = True
                globals()['_MCTSEngine'] = MCTSEngine
                globals()['_MCTSConfig'] = MCTSConfig
            except ImportError:
                _HAS_MCTS = False
    return _HAS_MCTS

def _get_sandbox():
    global _HAS_SANDBOX
    if _HAS_SANDBOX is None:
        if not _check_module("interleaved_sandbox"):
            _HAS_SANDBOX = False
        else:
            try:
                sys.path.insert(0, str(BASE_DIR))
                from tools.interleaved_sandbox import InterleavedSandbox, SandboxConfig
                _HAS_SANDBOX = True
                globals()['_InterleavedSandbox'] = InterleavedSandbox
                globals()['_SandboxConfig'] = SandboxConfig
            except ImportError:
                _HAS_SANDBOX = False
    return _HAS_SANDBOX

def _get_scf():
    global _HAS_SCF
    if _HAS_SCF is None:
        if not _check_module("semantic_context_filter"):
            _HAS_SCF = False
        else:
            try:
                sys.path.insert(0, str(BASE_DIR))
                from tools.semantic_context_filter import SemanticContextFilter
                _HAS_SCF = True
                globals()['_SemanticContextFilter'] = SemanticContextFilter
            except ImportError:
                _HAS_SCF = False
    return _HAS_SCF

def _get_identity():
    global _HAS_IDENTITY
    if _HAS_IDENTITY is None:
        if not (BASE_DIR / "identity_protocol" / "identity_vector.py").exists():
            _HAS_IDENTITY = False
        else:
            try:
                sys.path.insert(0, str(BASE_DIR))
                from identity_protocol.identity_vector import IdentityVector
                _HAS_IDENTITY = True
                globals()['_IdentityVector'] = IdentityVector
            except ImportError:
                _HAS_IDENTITY = False
    return _HAS_IDENTITY

# ── DeepSeek API (diretta per risposte semplici) ──────────────────────────

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY") or os.getenv("API_KEY")
DEEPSEEK_ENDPOINT = os.getenv("DEEPSEEK_ENDPOINT", "https://api.deepseek.com/v1")
FLASH_MODEL = os.getenv("MODEL_ID", "deepseek-v4-flash")
PRO_MODEL = os.getenv("MODEL_PRO_ID", "deepseek-v4-pro")

try:
    from openai import OpenAI as OpenAIAPI
    HAS_API = True
except ImportError:
    HAS_API = False


# ── Dataclasses ───────────────────────────────────────────────────────────

@dataclass
class ProtocolResult:
    """Risultato completo del protocollo."""
    tier: str                               # simple | standard | complex
    response: str                           # Risposta finale
    mcts_best_path: Optional[dict] = None   # Miglior approccio MCTS (se complex)
    sandbox_results: list = field(default_factory=list)  # Risultati esecuzioni
    drift_checks: list = field(default_factory=list)     # Check allineamento
    refinements: int = 0                    # Numero iterazioni refinement
    stats: dict = field(default_factory=dict)  # Metriche

    def to_dict(self) -> dict:
        return {
            "tier": self.tier,
            "response": self.response,
            "mcts_best_path": self.mcts_best_path,
            "sandbox_results": [
                {"status": r.get("status"), "summary": r.get("summary", "")[:100]}
                if isinstance(r, dict) else str(r)[:100]
                for r in (self.sandbox_results or [])
            ],
            "drift_checks": self.drift_checks,
            "refinements": self.refinements,
            "stats": self.stats,
        }


@dataclass
class ProtocolConfig:
    """Configurazione dell'orchestratore."""
    # Thresholds ComplexityDetector
    simple_max_chars: int = 50
    simple_keywords: set = field(default_factory=lambda: {
        "hello", "hi", "ciao", "grazie", "grazia", "help", "aiuto",
        "chi sei", "what is", "who are",
        "typo", "formatta", "format",
    })
    standard_keywords: set = field(default_factory=lambda: {
        "scrivi", "write", "crea", "create", "implementa", "implement",
        "codice", "code", "funzione", "function", "metodo", "method",
        "classe", "class", "script", "programma", "program",
        "spiega", "explain", "descrivi", "describe", "come funziona",
        "how to", "how do", "che cos", "che cosa", "cosa sono",
        "cosa", "what is", "definisci", "significa",
    })
    complex_keywords: set = field(default_factory=lambda: {
        "refactor", "rewrite", "restructure", "riscrivi", "riorganizza", "ridisegna",
        "architettura", "architecture",
        "multi-file", "codebase", "dependency", "migration", "redesign",
        "ottimizza", "optimize", "performance", "deep analysis",
        "investigation", "indagine", "report", "research", "ricerca",
        "design pattern", "scalabilita", "scalability", "sicurezza",
        "security audit", "vulnerability", "threat modeling",
        "audit", "analisi completa", "full analysis",
        # Identity Protocol keywords
        "identita", "identity", "chi sono", "chi sei", "self-concept",
        "costituzione", "constitution", "valori", "values",
        "milestone", "evoluzione", "growth", "commitment",
        "marcia", "erikson", "narrativa", "narrative",
    })

    # Thresholds MCTS
    mcts_num_branches: int = 4
    mcts_prune_threshold: float = 0.30
    mcts_rollout_depth: int = 2
    mcts_top_k: int = 2

    # Thresholds Sandbox
    sandbox_timeout: int = 30

    # Thresholds SCF
    scf_session_id: str = "protocol_default"
    scf_max_facts: int = 5

    # Loop
    max_refinements: int = 2
    refinement_threshold: float = 0.5  # sotto questo score → refinamento


# ═══════════════════════════════════════════════════════════════════════════
#  COMPLEXITY DETECTOR
# ═══════════════════════════════════════════════════════════════════════════

class ComplexityDetector:
    """
    Classifica il task in 3 livelli di complessita':
    - simple: domanda breve, nessuna keyword complessa → risposta diretta Flash
    - standard: task normale con codice → Flash + Sandbox verification
    - complex: refactoring, architettura, multi-file → MCTS + Sandbox + SCF
    """

    TIER_SIMPLE = "simple"
    TIER_STANDARD = "standard"
    TIER_COMPLEX = "complex"

    def __init__(self, config: ProtocolConfig):
        self.config = config

    def detect(self, task: str, context: str = "") -> str:
        """
        Determina il tier di elaborazione.
        Returns: 'simple' | 'standard' | 'complex'
        """
        task_lower = task.lower()
        context_lower = context.lower() if context else ""
        combined = task_lower + " " + context_lower

        # Passo 1: Keyword complesse dominano sempre
        if any(k in combined for k in self.config.complex_keywords):
            return self.TIER_COMPLEX

        # Passo 2: Keyword standard presenti (senza keyword complesse) → standard
        if any(k in task_lower for k in self.config.standard_keywords):
            return self.TIER_STANDARD

        # Passo 3: Task molto breve senza contesto → simple
        if len(task.strip()) <= self.config.simple_max_chars and not context:
            return self.TIER_SIMPLE

        # Passo 4: Keyword semplici dominanti → simple
        if any(k in task_lower for k in self.config.simple_keywords):
            if len(task) < 120:
                return self.TIER_SIMPLE

        # Passo 5: Task lungo (>300 char) → complex
        if len(task) > 300:
            return self.TIER_COMPLEX

        # Passo 6: Contesto ricco → almeno standard
        if context and len(context) > 150:
            return self.TIER_STANDARD

        # Default: standard
        return self.TIER_STANDARD


# ═══════════════════════════════════════════════════════════════════════════
#  PROTOCOL MONITOR
# ═══════════════════════════════════════════════════════════════════════════

class ProtocolMonitor:
    """Tracciamento metriche per ogni esecuzione del protocollo."""

    def __init__(self):
        self.start_time: float = 0.0
        self.metrics: dict = {}

    def start(self):
        """Avvia monitoraggio."""
        self.start_time = time.time()
        self.metrics = {
            "tokens_estimated": 0,
            "branches_explored": 0,
            "sandbox_executions": 0,
            "sandbox_passed": 0,
            "sandbox_failed": 0,
            "drift_checks": 0,
            "drift_warnings": 0,
            "refinements": 0,
            "api_calls": 0,
            "total_duration_sec": 0.0,
            "phases": [],
            "identity_check": False,        # Identity Protocol
            "identity_alignment": None,      # aligned | warning | breached
            "identity_score": None,          # Score attuale
        }

    def log_phase(self, name: str, detail: str = ""):
        """Registra una fase del protocollo."""
        self.metrics["phases"].append({
            "name": name,
            "detail": detail,
            "time": round(time.time() - self.start_time, 2),
        })

    def log_identity(self, identity_vector) -> dict:
        """
        Registra lo stato identitario corrente.
        Salva snapshot in Vector Memory se disponibile.
        """
        try:
            result = identity_vector.save_state()
            self.metrics["identity_check"] = True
            if result.get("status") == "saved":
                self.metrics["identity_alignment"] = "aligned"
                self.metrics["identity_score"] = result.get("total_score")
            elif result.get("status") == "warning":
                self.metrics["identity_alignment"] = "warning"
            self.log_phase("identity", f"status={result.get('identity_status','?')} score={result.get('total_score','?')}")
            return result
        except Exception as e:
            self.metrics["identity_check"] = False
            self.metrics["identity_alignment"] = "warning"
            self.log_phase("identity", f"error={str(e)[:50]}")
            return {"status": "error", "message": str(e)}

    def end(self) -> dict:
        """Finalizza e ritorna metriche."""
        self.metrics["total_duration_sec"] = round(time.time() - self.start_time, 2)
        return dict(self.metrics)


# ═══════════════════════════════════════════════════════════════════════════
#  PROTOCOL LOOP (integrazione dei 3 tool)
# ═══════════════════════════════════════════════════════════════════════════

class ProtocolLoop:
    """
    Ciclo di esecuzione del protocollo.
    Integra MCTS → Sandbox → SCF in modo iterativo.
    """

    def __init__(self, config: ProtocolConfig, monitor: ProtocolMonitor):
        self.config = config
        self.monitor = monitor

        # Inizializza tool (LAZY - solo al primo uso)
        self._mcts = None
        self._sandbox = None
        self._scf = None
        self._mcts_inited = False
        self._sandbox_inited = False
        self._scf_inited = False

    def _ensure_mcts(self):
        """Inizializza MCTS Engine al primo uso."""
        if not self._mcts_inited and _get_mcts():
            mcts_cfg = _MCTSConfig(
                num_branches=self.config.mcts_num_branches,
                prune_threshold=self.config.mcts_prune_threshold,
                max_rollout_depth=self.config.mcts_rollout_depth,
                top_k_for_rollout=self.config.mcts_top_k,
            )
            self._mcts = _MCTSEngine(mcts_cfg)
            self._mcts_inited = True

    def _ensure_sandbox(self):
        """Inizializza Sandbox al primo uso."""
        if not self._sandbox_inited and _get_sandbox():
            sb_cfg = _SandboxConfig(timeout=self.config.sandbox_timeout)
            self._sandbox = _InterleavedSandbox(sb_cfg)
            self._sandbox_inited = True

    def _ensure_scf(self):
        """Inizializza SCF al primo uso."""
        if not self._scf_inited and _get_scf():
            self._scf = _SemanticContextFilter(
                session_id=self.config.scf_session_id,
            )
            self._scf_inited = True

    def run_simple(self, task: str) -> str:
        """Tier simple: risposta diretta via DeepSeek Flash."""
        self.monitor.log_phase("simple", "risposta diretta Flash")
        client = self._get_api_client()
        if client:
            resp = client.ask(
                f"Answer concisely and informatively:\n\n{task}",
                temperature=0.3,
                max_tokens=500,
                caller_tag="oracle_protocol.simple",
            )
            if resp:
                self.monitor.metrics["api_calls"] += 1
                return resp
        return self._fallback_response(task)

    def run_standard(self, task: str, context: str = "") -> ProtocolResult:
        """Tier standard: risposta Flash + eventuale verifica codice."""
        self.monitor.log_phase("standard", "Flash + Sandbox verification")
        client = self._get_api_client()

        # Ottieni risposta
        if client:
            context_prefix = f"{context}\n" if context else ""
            prompt = context_prefix + f"""Task: {task}

Provide a concise solution. If code is required, use markdown code blocks with proper language tags."""
            response = client.ask(prompt, temperature=0.3, max_tokens=1500,
                                  caller_tag="oracle_protocol.standard")
            if response:
                self.monitor.metrics["api_calls"] += 1
            else:
                response = self._fallback_response(task)
        else:
            response = self._fallback_response(task)

        # Verifica codice con Sandbox
        self._ensure_sandbox()
        sandbox_results = []
        if self._sandbox and ('```' in response):
            self.monitor.log_phase("sandbox", "verifica blocchi codice")
            results = self._sandbox.analyze_response(response)
            for r in results:
                sandbox_results.append({
                    "language": r.language,
                    "status": r.status,
                    "exit_code": r.exit_code,
                    "summary": r.summary,
                })
                self.monitor.metrics["sandbox_executions"] += 1
                if r.status == "success":
                    self.monitor.metrics["sandbox_passed"] += 1
                else:
                    self.monitor.metrics["sandbox_failed"] += 1

            # Se ci sono errori nel codice, tenta correzione
            has_errors = any(r.status != "success" for r in results)
            if has_errors and client:
                self.monitor.log_phase("refinement", "correzione codice")
                error_report = "\n".join([
                    f"- {r.language}: {r.summary}" for r in results if r.status != "success"
                ])
                refine_prompt = f"""The code I provided has errors:

{error_report}

Please fix the code and provide the corrected version only.

Original task: {task}

Return ONLY the corrected code with proper markdown."""
                fixed = client.ask(refine_prompt, temperature=0.2, max_tokens=1000,
                                   caller_tag="oracle_protocol.standard_refinement")
                if fixed:
                    self.monitor.metrics["api_calls"] += 1
                    self.monitor.metrics["refinements"] += 1
                    response = response + "\n\n---\n\n**Correzione:**\n\n" + fixed

        result = ProtocolResult(
            tier="standard",
            response=response,
            sandbox_results=sandbox_results,
            stats=self.monitor.end(),
        )
        return result

    def run_complex(self, task: str, context: str = "") -> ProtocolResult:
        """Tier complex: MCTS → esecuzione → SCF → iterazione."""
        self.monitor.log_phase("complex", "MCTS + Sandbox + SCF")

        # 1. MCTS: genera e valuta approcci
        self._ensure_mcts()
        self.monitor.log_phase("mcts", "generazione albero decisionale")
        mcts_result = None
        if self._mcts:
            try:
                mcts_result = self._mcts.analyze(task, context)
                self.monitor.metrics["branches_explored"] = mcts_result["stats"]["total_initial"]
                self.monitor.log_phase(
                    "mcts_complete",
                    f"miglior approccio: {mcts_result['best_path']['title'] if mcts_result.get('best_path') else 'N/A'}"
                )
            except Exception as e:
                self.monitor.log_phase("mcts_error", str(e)[:100])

        # Prepara risposta base con approccio MCTS
        response_parts = []
        if mcts_result and mcts_result.get("best_path"):
            bp = mcts_result["best_path"]
            response_parts.append(f"## Piano: {bp['title']}")
            response_parts.append("")
            response_parts.append(bp['description'])
            if bp.get('critic_reasoning'):
                response_parts.append(f"\n*Valutazione: {bp['critic_reasoning']}*")
            if bp.get('rollout_content'):
                response_parts.append(f"\n### Implementazione\n")
                response_parts.append(bp['rollout_content'][:1500])
        else:
            # Fallback: nessun MCTS disponibile
            response_parts.append(f"## Analisi del problema\n\n{task}")

        response = "\n".join(response_parts)

        # 2. Esegui/Mostra codice con Sandbox
        self._ensure_sandbox()
        sandbox_results = []
        if self._sandbox and ('```' in response):
            self.monitor.log_phase("sandbox", "esecuzione blocchi codice")
            results = self._sandbox.analyze_response(response)
            for r in results:
                sandbox_results.append({
                    "language": r.language,
                    "status": r.status,
                    "exit_code": r.exit_code,
                    "summary": r.summary,
                })
                self.monitor.metrics["sandbox_executions"] += 1
                if r.status == "success":
                    self.monitor.metrics["sandbox_passed"] += 1
                else:
                    self.monitor.metrics["sandbox_failed"] += 1

            # Refinement se necessario
            has_errors = any(r.status != "success" for r in results)
            refinement_count = 0
            while has_errors and refinement_count < self.config.max_refinements:
                self.monitor.log_phase("refinement", f"tentativo {refinement_count + 1}")
                client = self._get_api_client()
                if not client:
                    break
                error_report = "\n".join([
                    f"- {r.language}: {r.summary}" for r in results if r.status != "success"
                ])
                refine_prompt = f"""The following approach has errors:

Approach: {response_parts[1] if len(response_parts) > 1 else task}

Errors:
{error_report}

Please provide a corrected implementation plan. Focus on fixing the issues.

Task: {task}"""
                fixed = client.ask(refine_prompt, temperature=0.2, max_tokens=1500,
                                   caller_tag="oracle_protocol.complex_refinement")
                if fixed:
                    self.monitor.metrics["api_calls"] += 1
                    self.monitor.metrics["refinements"] += 1
                    refinement_count += 1
                    response = response + "\n\n---\n\n### Correzione " + str(refinement_count) + "\n\n" + fixed
                    # Riverifica
                    if '```' in fixed:
                        results = self._sandbox.analyze_response(fixed)
                        sandbox_results.extend([
                            {
                                "language": r.language, "status": r.status,
                                "exit_code": r.exit_code, "summary": r.summary,
                            } for r in results
                        ])
                        has_errors = any(r.status != "success" for r in results)
                    else:
                        has_errors = False

        # 3. SCF: estrai e salva contesto
        self._ensure_scf()
        drift_checks = []
        if self._scf:
            self.monitor.log_phase("scf", "estrazione fatti + salvataggio")
            try:
                scf_result = self._scf.extract_and_store(f"Task: {task}\nContext: {context}\nResponse: {response}")
                self.monitor.metrics["scf_facts_stored"] = len(scf_result.get("stored", []))

                # Drift check
                drift = self._scf.check_drift(task, response[:200])
                drift_checks.append(drift)
                self.monitor.metrics["drift_checks"] += 1
                if not drift.get("aligned", True):
                    self.monitor.metrics["drift_warnings"] += 1
                    self.monitor.log_phase("drift_warning", drift.get("reason", ""))
            except Exception as e:
                self.monitor.log_phase("scf_error", str(e)[:100])

        result = ProtocolResult(
            tier="complex",
            response=response,
            mcts_best_path=mcts_result["best_path"] if mcts_result else None,
            sandbox_results=sandbox_results,
            drift_checks=drift_checks,
            refinements=self.monitor.metrics.get("refinements", 0),
            stats=self.monitor.end(),
        )
        return result

    def _get_api_client(self):
        """Restituisce client API DeepSeek se disponibile.
        Controlla l'environ a runtime per permettere override nei test."""
        api_key = os.environ.get("DEEPSEEK_API_KEY", "") or os.environ.get("API_KEY", "")
        if not HAS_API or not api_key:
            return None
        try:
            import httpx
            hc = httpx.Client(timeout=5.0)
            client = OpenAIAPI(api_key=api_key, base_url=DEEPSEEK_ENDPOINT, http_client=hc)
            return _APIClientWrapper(client)
        except Exception:
            return None

    @staticmethod
    def _fallback_response(task: str) -> str:
        """Risposta fallback quando API non disponibile."""
        return (f"**Analisi richiesta:** {task}\n\n"
                f"_Nota: API DeepSeek non disponibile. "
                f"Verifica la connessione e la chiave API nel file .env._")


class _APIClientWrapper:
    """Wrapper minimale per chiamate API."""

    def __init__(self, client):
        self._client = client

    def ask(self, prompt: str, temperature: float = 0.3, max_tokens: int = 1000,
            caller_tag: str = "oracle_protocol.unknown") -> Optional[str]:
        try:
            t0 = time.time()
            resp = self._client.chat.completions.create(
                model=FLASH_MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=temperature,
                max_tokens=max_tokens,
            )
            duration = time.time() - t0
            _log_openai_call(caller_tag, duration, resp, FLASH_MODEL)
            return resp.choices[0].message.content.strip()
        except Exception:
            return None


# ═══════════════════════════════════════════════════════════════════════════
#  ORACLE PROTOCOL (Engine principale)
# ═══════════════════════════════════════════════════════════════════════════

class OracleProtocol:
    """
    Engine principale dell'Oracle Protocol.
    
    Uso:
        op = OracleProtocol()
        
        # Analisi completa con auto-detection del tier
        result = op.analyze(task="Refactor modulo CRUD per async/await")
        print(result.response)
        
        # Forza un tier specifico
        result = op.analyze(task="Ciao!", tier="simple")
        
        # Con contesto
        result = op.analyze(
            task="Refactor per async/await",
            context="codice esistente 2000 righe, FastAPI + SQLAlchemy",
        )
    """

    def __init__(self, config: Optional[ProtocolConfig] = None):
        self.config = config or ProtocolConfig()
        self.detector = ComplexityDetector(self.config)
        self._identity = None

        # Stats globali
        self.total_runs = 0
        self.tier_counts = {"simple": 0, "standard": 0, "complex": 0}
        self.total_duration = 0.0

    def _ensure_identity(self):
        """Inizializza IdentityVector al primo uso (lazy)."""
        if self._identity is None and _get_identity():
            self._identity = _IdentityVector()
        return self._identity is not None

    def analyze(self, task: str, context: str = "", tier: str = "auto") -> ProtocolResult:
        """
        Punto d'ingresso principale.
        
        Args:
            task: Descrizione del task/richiesta
            context: Contesto aggiuntivo (facoltativo)
            tier: Forza un tier ('simple', 'standard', 'complex', 'auto')
            
        Returns:
            ProtocolResult con response, stats, e dettagli
        """
        self.total_runs += 1
        monitor = ProtocolMonitor()
        monitor.start()
        loop = ProtocolLoop(self.config, monitor)

        # Detection tier
        effective_tier = tier.lower() if tier != "auto" else self.detector.detect(task, context)
        self.tier_counts[effective_tier] = self.tier_counts.get(effective_tier, 0) + 1

        monitor.log_phase("detection", f"tier={effective_tier}")

        # ── Identity Check (per task complex o identity-relevant) ──
        identity_context = ""
        is_identity_task = any(kw in task.lower() for kw in [
            "identita", "identity", "chi sono", "chi sei", "self-concept",
            "milestone", "marcia", "erikson", "costituzione",
        ])
        if effective_tier == "complex" or is_identity_task:
            if self._ensure_identity():
                identity_result = monitor.log_identity(self._identity)
                if identity_result.get("status") == "saved":
                    identity_context = (
                        f"[Identity State: {identity_result.get('identity_status','?')} "
                        f"score={identity_result.get('total_score','?')}]"
                    )
                    if is_identity_task:
                        # Per task identitari, arricchisci il contesto
                        latest = self._identity.get_latest_state()
                        if latest:
                            meta = latest.get("metadata", {})
                            identity_context += (
                                f"\n[Identity History: status={meta.get('identity_status','?')}, "
                                f"score={meta.get('total_score','?')}, "
                                f"values={meta.get('value_alignment','?')}]"
                            )
                monitor.log_phase("identity_check", identity_context[:100] if identity_context else "done")
            else:
                monitor.log_phase("identity_check", "identity_vector_non_disponibile")

        # Arricchisci contesto con identity_context
        full_context = context
        if identity_context:
            full_context = (context + "\n" + identity_context) if context else identity_context

        # Esecuzione per tier
        if effective_tier == "simple":
            response = loop.run_simple(task)
            result = ProtocolResult(
                tier="simple",
                response=response,
                stats=monitor.end(),
            )
        elif effective_tier == "standard":
            result = loop.run_standard(task, full_context)
        elif effective_tier == "complex":
            result = loop.run_complex(task, full_context)
        else:
            # Fallback safe
            result = ProtocolResult(
                tier="simple",
                response=f"Tier '{effective_tier}' non riconosciuto. Uso modalita semplice.\n\n{task}",
                stats=monitor.end(),
            )

        self.total_duration += result.stats.get("total_duration_sec", 0)
        return result

    def get_stats(self) -> dict:
        """Statistiche globali dell'orchestratore."""
        return {
            "total_runs": self.total_runs,
            "tier_distribution": dict(self.tier_counts),
            "total_duration_sec": round(self.total_duration, 2),
            "available_modules": {
                "mcts": _check_module("mcts_engine"),
                "sandbox": _check_module("interleaved_sandbox"),
                "scf": _check_module("semantic_context_filter"),
                "identity": (BASE_DIR / "identity_protocol" / "identity_vector.py").exists(),
                "api": HAS_API and bool(os.environ.get("DEEPSEEK_API_KEY", "") or os.environ.get("API_KEY", "")),
            },
        }


# ═══════════════════════════════════════════════════════════════════════════
#  CLI
# ═══════════════════════════════════════════════════════════════════════════

def _ascii(s: str) -> str:
    return s.replace('\u2500', '-').replace('\u2502', '|').replace('\u2514', '+').replace('\u251c', '+')


def cmd_analyze(args):
    task = args.task or ''
    if not task and not sys.stdin.isatty():
        task = sys.stdin.read().strip()
    if not task:
        print('ERRORE: fornisci task con --task o via pipe')
        sys.exit(1)

    config = ProtocolConfig(
        mcts_num_branches=args.branches,
        mcts_rollout_depth=args.depth,
        max_refinements=args.refinements,
    )
    op = OracleProtocol(config)
    result = op.analyze(task, args.context or '', tier=args.tier)

    if args.json:
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2, default=str))
    else:
        print(_ascii(f"\n=== Oracle Protocol - Tier: {result.tier.upper()} ==="))
        print(_ascii(f"Durata: {result.stats.get('total_duration_sec', 0):.2f}s"))
        print(_ascii(f"Refinements: {result.refinements}"))

        if result.mcts_best_path:
            print(_ascii(f"\n--- MCTS: {result.mcts_best_path['title']} ---"))
            print(_ascii(f"Score: {result.mcts_best_path.get('score', 0):.2f}"))

        if result.sandbox_results:
            ok = sum(1 for r in result.sandbox_results if r.get('status') == 'success')
            total = len(result.sandbox_results)
            print(_ascii(f"\n--- Sandbox: {ok}/{total} esecuzioni OK ---"))

        if result.drift_checks:
            for dc in result.drift_checks:
                icon = '[OK]' if dc.get('aligned') else '[X]'
                print(_ascii(f"\n{icon} Drift: {dc.get('reason', '')[:80]}"))

        print(_ascii(f"\n=== RISPOSTA ==="))
        print(result.response[:2000])
        if len(result.response) > 2000:
            print(_ascii("\n... [risposta troncata, usa --json per completo]"))


def cmd_simple(args):
    """Forza modalita simple."""
    task = args.task or ''
    if not task:
        print('ERRORE: --task richiesto')
        sys.exit(1)
    op = OracleProtocol()
    result = op.analyze(task, tier="simple")
    if args.json:
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2, default=str))
    else:
        print(result.response)


def cmd_status(args):
    """Mostra stato dell'orchestratore."""
    op = OracleProtocol()
    stats = op.get_stats()
    if args.json:
        print(json.dumps(stats, ensure_ascii=False, indent=2))
    else:
        print(_ascii(f"\n=== Oracle Protocol Status ==="))
        print(_ascii(f"Total runs: {stats['total_runs']}"))
        print(_ascii(f"Tier distribution:"))
        for t, c in stats['tier_distribution'].items():
            print(_ascii(f"  {t}: {c}"))
        print(_ascii(f"Total duration: {stats['total_duration_sec']}s"))
        print(_ascii(f"\nModules:"))
        for mod, ok in stats['available_modules'].items():
            icon = '[OK]' if ok else '[X]'
            print(_ascii(f"  {icon} {mod}"))


def main():
    p = argparse.ArgumentParser(
        description='Oracle Protocol Orchestrator - decision-making multi-livello',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            'Esempi:\n'
            '  %(prog)s analyze --task "Refactor modulo X per async/await" --context "codice 2k righe"\n'
            '  %(prog)s analyze --task "Cosa e una variabile?" --tier simple\n'
            '  %(prog)s analyze --task "..." --json\n'
            '  %(prog)s simple --task "Chi ha vinto i mondiali 2022?"\n'
            '  cat task.txt | %(prog)s analyze\n'
            '  %(prog)s status\n'
        ),
    )
    sp = p.add_subparsers(dest='cmd')

    pa = sp.add_parser('analyze', help='Analisi completa con auto-detection tier')
    pa.add_argument('--task', '-t', default='', help='Task/richiesta')
    pa.add_argument('--context', '-c', default='', help='Contesto aggiuntivo')
    pa.add_argument('--tier', '-T', default='auto', choices=['auto', 'simple', 'standard', 'complex'],
                    help='Forza tier (default: auto)')
    pa.add_argument('--branches', '-b', type=int, default=4, help='Rami MCTS (default: 4)')
    pa.add_argument('--depth', '-d', type=int, default=2, help='Profondita rollout (default: 2)')
    pa.add_argument('--refinements', '-r', type=int, default=2, help='Max refinement loop (default: 2)')
    pa.add_argument('--json', action='store_true', help='Output JSON')

    ps = sp.add_parser('simple', help='Forza modalita simple (risposta diretta)')
    ps.add_argument('--task', '-t', default='', help='Domanda semplice')
    ps.add_argument('--json', action='store_true')

    pst = sp.add_parser('status', help='Stato orchestratore')
    pst.add_argument('--json', action='store_true')

    args = p.parse_args()
    if args.cmd == 'analyze':
        cmd_analyze(args)
    elif args.cmd == 'simple':
        cmd_simple(args)
    elif args.cmd == 'status':
        cmd_status(args)
    else:
        p.print_help()


if __name__ == '__main__':
    main()