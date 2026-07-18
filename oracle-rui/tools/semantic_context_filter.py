#!/usr/bin/env python3
"""
🧠 Semantic Context Filter — Memoria semantica a due livelli per Oracle.

Previene il context drift durante sessioni lunghe comprimendo il contesto
e mantenendo solo le informazioni rilevanti.

Architettura:
  SessionState          — tracciamento stato sessione (goal, decisioni, files)
  BackgroundExtractor   — DeepSeek Flash per estrarre fatti salienti
  VectorStore           — wrapper su VectorMemoryEngine (ChromaDB)
  RelevanceRanker       — query semantica per fatti pertinenti
  ContextComposer       — assemblea contesto compresso (3 msg + top facts + stato)
  DriftDetector         — rileva deviazioni dall'obiettivo

Ciclo d'uso:
  1. Dopo ogni turno: extract_and_store(conversation_history)
  2. Prima del turno successivo: get_compressed_context(current_query, goal)
  3. Opzionale: check_drift(current_goal, current_action) → warn se devia

CLI:
  python tools/semantic_context_filter.py extract --messages "..." [--session-id sess_01]
  python tools/semantic_context_filter.py context --query "..." [--goal "..."] [--session-id sess_01]
  python tools/semantic_context_filter.py drift --goal "..." --action "..."
  python tools/semantic_context_filter.py state --show [--session-id sess_01]
  python tools/semantic_context_filter.py clear --session-id sess_01

Python:
  from tools.semantic_context_filter import SemanticContextFilter
  scf = SemanticContextFilter(session_id="sess_01")
  compressed = scf.get_compressed_context("what next?", goal="build api")
  scf.extract_and_store(conversation_history)
"""

import argparse
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
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

# ── Paths ─────────────────────────────────────────────────────────────────

BASE_DIR = Path(__file__).parent.parent.resolve()
DATA_DIR = BASE_DIR / "data"

# ── DeepSeek API ──────────────────────────────────────────────────────────

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY") or os.getenv("API_KEY")
DEEPSEEK_ENDPOINT = os.getenv("DEEPSEEK_ENDPOINT", "https://api.deepseek.com/v1")
FLASH_MODEL = os.getenv("MODEL_ID", "deepseek-v4-flash")

try:
    from openai import OpenAI as OpenAIAPI
    HAS_OPENAI = True
except ImportError:
    HAS_OPENAI = False

# ── Vector Memory ─────────────────────────────────────────────────────────

try:
    sys.path.insert(0, str(BASE_DIR))
    from tools.vector_memory import VectorMemoryEngine
    HAS_VECTOR_MEMORY = True
except ImportError:
    HAS_VECTOR_MEMORY = False


# ── Dataclasses ───────────────────────────────────────────────────────────

@dataclass
class Fact:
    """Un fatto estratto dalla conversazione."""
    content: str
    category: str          # goal | decision | state | error | discovery | context
    timestamp: float
    importance: float = 0.5   # 0.0 - 1.0

@dataclass
class SessionState:
    """Stato compresso della sessione."""
    goal: str = ""
    sub_goal: str = ""
    decisions: list[str] = field(default_factory=list)
    files_modified: list[str] = field(default_factory=list)
    current_variables: dict = field(default_factory=dict)
    errors_encountered: list[str] = field(default_factory=list)
    discoveries: list[str] = field(default_factory=list)
    turn_count: int = 0
    tokens_consumed: int = 0

    def to_dict(self) -> dict:
        return {
            "goal": self.goal,
            "sub_goal": self.sub_goal,
            "decisions": self.decisions[-5:],        # ultime 5
            "files_modified": self.files_modified[-10:],
            "current_variables": self.current_variables,
            "errors_encountered": self.errors_encountered[-5:],
            "discoveries": self.discoveries[-5:],
            "turn_count": self.turn_count,
        }

    def to_compressed(self) -> str:
        parts = []
        if self.goal:
            parts.append(f"GOAL: {self.goal}")
        if self.sub_goal:
            parts.append(f"NOW: {self.sub_goal}")
        if self.files_modified:
            parts.append(f"FILES: {', '.join(self.files_modified[-5:])}")
        if self.decisions:
            parts.append(f"DECISIONS: {'; '.join(self.decisions[-3:])}")
        if self.errors_encountered:
            parts.append(f"ERRORS: {'; '.join(self.errors_encountered[-3:])}")
        if self.discoveries:
            parts.append(f"DISCOVERED: {'; '.join(self.discoveries[-3:])}")
        parts.append(f"TURNS: {self.turn_count}")
        return " | ".join(parts)


# ═══════════════════════════════════════════════════════════════════════════
#  BACKGROUND EXTRACTOR (DeepSeek Flash)
# ═══════════════════════════════════════════════════════════════════════════

class BackgroundExtractor:
    """
    Usa DeepSeek Flash (13B, economico) per estrarre fatti salienti
    dalla conversazione. Processa la cronologia in background.
    """

    EXTRACTION_PROMPT = """You are a fact extraction AI. Analyze the conversation below and extract key information as JSON.

Extract:
1. "goal": the main objective the user wants to achieve
2. "sub_goal": what is being worked on right now
3. "decisions": list of important decisions made (max 3)
4. "files_modified": list of file paths that were created or modified
5. "discoveries": list of important findings or insights (max 3)
6. "errors": list of errors encountered (max 3)
7. "state_vars": key variables or state (dict of name: value)

Rules:
- Be concise but precise. Keep each item under 100 chars.
- Only extract information explicitly present or directly implied.
- If nothing to extract, return empty lists.
- Return ONLY valid JSON. No markdown. No explanation.

Conversation:
{conversation}

Output JSON:"""

    def __init__(self, model: str = FLASH_MODEL):
        self.model = model
        self._client = None
        if HAS_OPENAI and DEEPSEEK_API_KEY:
            self._client = OpenAIAPI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_ENDPOINT)
        self._last_raw = ""

    @property
    def available(self) -> bool:
        return self._client is not None

    def extract(self, conversation: str, caller_tag: str = "semantic_context_filter.extract") -> dict:
        """Estrae fatti dalla conversazione. Ritorna dict strutturato."""
        if not self.available:
            return self._fallback_extract(conversation)

        prompt = self.EXTRACTION_PROMPT.format(conversation=conversation[-3000:])  # ultimi 3k char
        try:
            t0 = time.time()
            resp = self._client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=800,
            )
            duration = time.time() - t0
            # Log token usage
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
                model_id=self.model,
                metadata={"provider": "openai_direct"},
            )
            raw = resp.choices[0].message.content.strip()
            self._last_raw = raw
            # Pulisce markdown
            raw = re.sub(r'^```(?:json)?\s*', '', raw)
            raw = re.sub(r'\s*```$', '', raw)
            return json.loads(raw)
        except (json.JSONDecodeError, Exception) as e:
            return self._fallback_extract(conversation)

    def _fallback_extract(self, text: str) -> dict:
        """Fallback basato su regole quando API non disponibile."""
        result = {
            "goal": "", "sub_goal": "", "decisions": [],
            "files_modified": [], "discoveries": [], "errors": [], "state_vars": {},
        }
        # Cerca percorsi file
        files = re.findall(r'[\w/\\-]+\.\w{1,4}', text)
        result["files_modified"] = list(set(f for f in files if '/' in f or '\\' in f))[:3]
        # Cerca errori
        errs = re.findall(r'(?:Error|error|Traceback|ERR|FAILED)[:\s]+([^\n]+)', text)
        result["errors"] = [e.strip()[:80] for e in errs][:3]
        return result


# ═══════════════════════════════════════════════════════════════════════════
#  VECTOR STORE (wrapper su ChromaDB)
# ═══════════════════════════════════════════════════════════════════════════

class VectorStore:
    """
    Wrapper su VectorMemoryEngine (ChromaDB).
    Gestisce le collezioni per sessione.
    """

    SESSION_COLLECTION = "sessions"   # collezione principale per fatti di sessione

    def __init__(self):
        if not HAS_VECTOR_MEMORY:
            raise ImportError("VectorMemoryEngine non disponibile. pip install chromadb")
        self.engine = VectorMemoryEngine()

    def store_fact(self, session_id: str, fact: Fact) -> dict:
        """Salva un fatto nella memoria vettoriale."""
        doc_id = f"{session_id}_{int(fact.timestamp)}"
        metadata = {
            "session_id": session_id,
            "category": fact.category,
            "importance": fact.importance,
            "timestamp": fact.timestamp,
        }
        return self.engine.add_text(
            collection=self.SESSION_COLLECTION,
            document_id=doc_id,
            text=f"[{fact.category}] {fact.content}",
            metadata=metadata,
        )

    def store_state(self, session_id: str, state: SessionState) -> dict:
        """Salva lo stato corrente della sessione."""
        doc_id = f"{session_id}_state"
        text = state.to_compressed()
        metadata = {
            "session_id": session_id,
            "category": "state",
            "importance": 1.0,
            "timestamp": time.time(),
        }
        return self.engine.add_text(
            collection=self.SESSION_COLLECTION,
            document_id=doc_id,
            text=text,
            metadata=metadata,
        )

    def search_relevant(self, session_id: str, query: str, top_k: int = 5) -> list[dict]:
        """Cerca fatti semanticamente rilevanti per la sessione."""
        results = self.engine.search(
            collection=self.SESSION_COLLECTION,
            query=query,
            top_k=top_k * 2,  # prende di piu' per filtrare
            include_metadata=True,
            include_text=True,
        )
        # Filtra per session_id se specificato
        filtered = []
        for r in results:
            meta = r.get("metadata", {}) or {}
            if meta.get("session_id") == session_id:
                filtered.append(r)
            elif not session_id:
                filtered.append(r)
        return filtered[:top_k]

    def get_session_facts(self, session_id: str) -> list[dict]:
        """Recupera tutti i fatti di una sessione."""
        # ChromaDB non supporta query per metadati via collection.get facilmente
        # Cerchiamo con una query generica
        results = self.engine.search(
            collection=self.SESSION_COLLECTION,
            query=f"session {session_id}",
            top_k=50,
            include_metadata=True,
            include_text=True,
        )
        return [r for r in results
                if r.get("metadata", {}).get("session_id") == session_id]

    def clear_session(self, session_id: str) -> int:
        """Elimina tutti i fatti di una sessione."""
        facts = self.get_session_facts(session_id)
        count = 0
        for f in facts:
            try:
                self.engine.delete_document(self.SESSION_COLLECTION, f["id"])
                count += 1
            except Exception:
                pass
        # Elimina anche lo stato
        try:
            self.engine.delete_document(self.SESSION_COLLECTION, f"{session_id}_state")
        except Exception:
            pass
        return count

    def get_all_sessions(self) -> list[str]:
        """Lista tutte le sessioni con fatti memorizzati."""
        # Cerchiamo documenti con pattern session_XXX_state
        results = self.engine.search(
            collection=self.SESSION_COLLECTION,
            query="session state",
            top_k=100,
            include_metadata=True,
        )
        sessions = set()
        for r in results:
            sid = r.get("metadata", {}).get("session_id")
            if sid:
                sessions.add(sid)
        return sorted(sessions)


# ═══════════════════════════════════════════════════════════════════════════
#  RELEVANCE RANKER
# ═══════════════════════════════════════════════════════════════════════════

class RelevanceRanker:
    """Ordina e filtra fatti per rilevanza rispetto al contesto corrente."""

    @staticmethod
    def rank(facts: list[dict], query: str) -> list[dict]:
        """Ordina per similarità (già fatto da ChromaDB) + importanza."""
        # ChromaDB restituisce già ordinati per similarità
        # Aggiungiamo peso all'importanza dal metadata
        for f in facts:
            meta = f.get("metadata", {}) or {}
            importance = meta.get("importance", 0.5)
            sim = f.get("similarity", 0.5)
            f["_combined_score"] = sim * 0.7 + importance * 0.3
        return sorted(facts, key=lambda x: x.get("_combined_score", 0), reverse=True)

    @staticmethod
    def filter_by_category(facts: list[dict], categories: list[str]) -> list[dict]:
        """Filtra fatti per categoria."""
        return [f for f in facts
                if f.get("metadata", {}).get("category") in categories]


# ═══════════════════════════════════════════════════════════════════════════
#  DRIFT DETECTOR
# ═══════════════════════════════════════════════════════════════════════════

class DriftDetector:
    """
    Rileva se l'agente sta deviando dall'obiettivo originale.
    Usa similarità semantica e pattern matching.
    """

    def __init__(self):
        self._history: list[tuple[str, str, float]] = []  # (goal, action, score)

    @staticmethod
    def _stem(word: str) -> str:
        """Stemming minimal: rimuove suffissi comuni."""
        w = word.lower()
        # Catena di stripping ordinata per specificita'
        if w.endswith('izing') or w.endswith('yzing'):
            w = w[:-4]
        elif w.endswith('ation') or w.endswith('ition'):
            w = w[:-4]
        elif w.endswith('ating') or w.endswith('iting') or w.endswith('uting') or w.endswith('eting') or w.endswith('oting'):
            w = w[:-3]
        elif w.endswith('ising') or w.endswith('ysing'):
            w = w[:-4]
        elif w.endswith('ing'):
            w = w[:-3]
        elif w.endswith('ment'):
            w = w[:-4]
        elif w.endswith('able') or w.endswith('ible'):
            w = w[:-4]
        elif w.endswith('tion') or w.endswith('sion'):
            w = w[:-4]
        elif w.endswith('ness'):
            w = w[:-4]
        elif w.endswith('less'):
            w = w[:-4]
        elif w.endswith('ful'):
            w = w[:-3]
        elif w.endswith('ly'):
            w = w[:-2]
        elif w.endswith('ies'):
            w = w[:-3] + 'y'
        elif w.endswith('ves'):
            w = w[:-3] + 'f'
        elif w.endswith('es'):
            w = w[:-2]
        elif w.endswith('ed'):
            w = w[:-2]
        elif w.endswith('s') and not w.endswith('ss') and not w.endswith('us'):
            w = w[:-1]
        # Rimuovi 'e' finale se lunga
        if w.endswith('e') and len(w) > 3 and not w.endswith('ie'):
            w = w[:-1]
        return w

    def check(self, goal: str, action: str) -> dict:
        """
        Verifica se 'action' e' allineata con 'goal'.
        Ritorna {aligned: bool, score: float, reason: str}
        """
        if not goal or not action:
            return {"aligned": True, "score": 1.0, "reason": "nessun goal/action"}

        # Crea parole stemmate per matching piu' flessibile
        g_stems = {self._stem(w) for w in goal.lower().split() if len(w) > 2}
        a_stems = {self._stem(w) for w in action.lower().split() if len(w) > 2}
        stopwords = {'the', 'and', 'for', 'with', 'from', 'this', 'that', 'what', 'when', 'where'}
        g_stems -= stopwords
        a_stems -= stopwords

        overlap = g_stems & a_stems

        # Match fuzzy: parole che condividono prefisso >= 4 caratteri
        fuzzy = set()
        for gs in g_stems:
            for a in a_stems:
                if len(gs) >= 4 and len(a) >= 4:
                    # Condividono almeno 4 caratteri iniziali
                    min_len = min(len(gs), len(a))
                    if min_len >= 4 and gs[:4] == a[:4]:
                        fuzzy.add(gs)
                        fuzzy.add(a)
                    # Una e' contenuta nell'altra
                    elif gs in a or a in gs:
                        fuzzy.add(gs)
                        fuzzy.add(a)

        all_matched = overlap | fuzzy

        # Scoring basato su overlap stemmato + fuzzy
        if len(g_stems) == 0:
            score = 0.5
        elif len(g_stems) <= 3:
            score = len(all_matched) / max(len(g_stems), 1)
        else:
            score = len(all_matched) / max(len(g_stems | a_stems), 1)

        # Bonus se azione contiene verbi chiave semanticamente simili
        key_verbs = {
            'build', 'create', 'fix', 'repair', 'refactor', 'add', 'implement',
            'write', 'remove', 'delete', 'update', 'change', 'modify',
            'analyze', 'test', 'check', 'debug', 'investigate',
            'configure', 'setup', 'install', 'deploy', 'migrate',
            'optimize', 'improve', 'enhance',
        }
        g_verbs = g_stems & key_verbs
        a_verbs = a_stems & key_verbs
        if g_verbs and a_verbs & g_verbs:
            score = min(1.0, score + 0.3)
        elif g_verbs and a_verbs:
            # Verbi diversi ma entrambi d'azione
            score = min(1.0, score + 0.15)

        aligned = score >= 0.15
        reason = "allineato" if aligned else f"possibile drift: overlap={score:.2f}"

        self._history.append((goal, action, score))
        return {"aligned": aligned, "score": round(score, 2), "reason": reason}

    def get_trend(self) -> str:
        """Analisi trend: l'agente sta migliorando o peggiorando l'allineamento."""
        if len(self._history) < 3:
            return "dati insufficienti"
        recent = [s for _, _, s in self._history[-5:]]
        if len(recent) >= 3:
            trend = recent[-1] - recent[0]
            if trend > 0.1:
                return "miglioramento"
            elif trend < -0.1:
                return "peggioramento"
        return "stabile"


# ═══════════════════════════════════════════════════════════════════════════
#  CONTEXT COMPOSER
# ═══════════════════════════════════════════════════════════════════════════

class ContextComposer:
    """
    Assemblea contesto compresso per il modello.
    Formato:
    
    ## COMPRESSED CONTEXT ##
    GOAL: ...
    NOW: ...
    FILES: ...
    DECISIONS: ...
    ERRORS: ...
    KEY_FACTS: ...
    ## END CONTEXT ##
    """

    @staticmethod
    def compose(
        state: SessionState,
        relevant_facts: list[dict],
        last_messages: list[str],
        max_facts: int = 5,
    ) -> str:
        """Crea contesto compresso."""
        parts = []
        parts.append("## COMPRESSED CONTEXT ##")

        # Stato corrente
        state_str = state.to_compressed()
        if state_str:
            parts.append(state_str)

        # Fatti rilevanti dalla memoria semantica
        if relevant_facts:
            parts.append("RELEVANT FACTS:")
            for i, f in enumerate(relevant_facts[:max_facts], 1):
                text = f.get("text", "")
                meta = f.get("metadata", {}) or {}
                cat = meta.get("category", "general")
                sim = f.get("similarity", 0)
                parts.append(f"  [{i}] ({cat}, sim={sim:.2f}) {text[:150]}")

        # Ultimi messaggi (contesto immediato)
        if last_messages:
            parts.append("LAST MESSAGES:")
            # Solo i contenuti, compressi
            for msg in last_messages[-3:]:
                # Prendi solo prime 200 char per messaggio
                truncated = msg[:200].replace('\n', ' ')
                parts.append(f"  {truncated}")

        parts.append("## END CONTEXT ##")
        return '\n'.join(parts)

    @staticmethod
    def compress_for_prompt(conversation: list[dict], state: SessionState,
                            max_turns: int = 3) -> list[dict]:
        """
        Comprime la cronologia della conversazione.
        Mantiene solo gli ultimi max_turns messaggi + stato compresso.
        """
        if not conversation:
            return []

        # Prendi ultimi max_turns turni
        compressed = conversation[-max_turns * 2:]  # *2 per user+assistant

        # Aggiungi contesto come messaggio system
        state_str = state.to_compressed()
        context_msg = {
            "role": "system",
            "content": f"[CONTEXT] {state_str}",
        }

        return [context_msg] + compressed


# ═══════════════════════════════════════════════════════════════════════════
#  SEMANTIC CONTEXT FILTER (Engine principale)
# ═══════════════════════════════════════════════════════════════════════════

class SemanticContextFilter:
    """
    Engine principale del filtro contestuale semantico.
    
    Usage:
        scf = SemanticContextFilter(session_id="sess_01")
        # Dopo ogni turno
        scf.extract_and_store(conversation_history)
        # Prima del prossimo turno
        ctx = scf.get_compressed_context(current_query, goal)
        # Verifica drift
        drift = scf.check_drift(goal, action_description)
    """

    def __init__(self, session_id: str = "default", goal: str = "",
                 working_dir: Optional[str] = None):
        self.session_id = session_id
        self.state = SessionState(goal=goal)
        self.extractor = BackgroundExtractor()
        self.drift = DriftDetector()
        self.composer = ContextComposer()
        self._last_messages: list[str] = []

        # Vector store (opzionale)
        self._vector_store: Optional[VectorStore] = None
        try:
            self._vector_store = VectorStore()
        except Exception:
            pass

    def extract_and_store(self, conversation: str) -> dict:
        """
        Estrae fatti dalla conversazione e li salva in memoria vettoriale.
        Da chiamare DOPO ogni turno di conversazione.
        """
        # Estrai con DeepSeek Flash
        extracted = self.extractor.extract(conversation)

        # Aggiorna stato sessione
        if extracted.get("goal") and not self.state.goal:
            self.state.goal = extracted["goal"]
        if extracted.get("sub_goal"):
            self.state.sub_goal = extracted["sub_goal"]

        decisions = extracted.get("decisions", [])
        for d in decisions:
            if d not in self.state.decisions:
                self.state.decisions.append(d)

        files = extracted.get("files_modified", [])
        for f in files:
            if f not in self.state.files_modified:
                self.state.files_modified.append(f)

        discoveries = extracted.get("discoveries", [])
        for disc in discoveries:
            if disc not in self.state.discoveries:
                self.state.discoveries.append(disc)

        errors = extracted.get("errors", [])
        for e in errors:
            if e not in self.state.errors_encountered:
                self.state.errors_encountered.append(e)

        if extracted.get("state_vars"):
            self.state.current_variables.update(extracted["state_vars"])

        self.state.turn_count += 1

        # Salva in ChromaDB
        stored = []
        if self._vector_store:
            # Salva goal come fatto
            if extracted.get("goal"):
                f = Fact(content=extracted["goal"], category="goal",
                         timestamp=time.time(), importance=1.0)
                self._vector_store.store_fact(self.session_id, f)
                stored.append("goal")

            # Salva decisioni
            for d in decisions:
                f = Fact(content=d, category="decision",
                         timestamp=time.time(), importance=0.8)
                self._vector_store.store_fact(self.session_id, f)
                stored.append("decision")

            # Salva scoperte
            for disc in discoveries:
                f = Fact(content=disc, category="discovery",
                         timestamp=time.time(), importance=0.7)
                self._vector_store.store_fact(self.session_id, f)
                stored.append("discovery")

            # Salva errori
            for e in errors:
                f = Fact(content=e, category="error",
                         timestamp=time.time(), importance=0.9)
                self._vector_store.store_fact(self.session_id, f)
                stored.append("error")

            # Salva stato
            self._vector_store.store_state(self.session_id, self.state)

        return {
            "extracted": extracted,
            "stored": stored,
            "state": self.state.to_dict(),
        }

    def get_compressed_context(self, query: str = "", goal: str = "",
                               max_facts: int = 5,
                               include_last_messages: bool = True) -> str:
        """
        Genera contesto compresso per il prossimo turno.
        Da chiamare PRIMA di ogni nuova richiesta al modello.
        """
        if goal:
            self.state.goal = goal
        if query:
            self.state.sub_goal = query[:120]

        # Cerca fatti rilevanti in ChromaDB
        relevant = []
        if self._vector_store:
            search_query = f"{goal} {query}" if goal else query
            if search_query:
                relevant = self._vector_store.search_relevant(
                    self.session_id, search_query, top_k=max_facts
                )
                # Rank per rilevanza + importanza
                relevant = RelevanceRanker.rank(relevant, search_query)

        # Componi contesto
        context = self.composer.compose(
            state=self.state,
            relevant_facts=relevant,
            last_messages=self._last_messages if include_last_messages else [],
            max_facts=max_facts,
        )

        return context

    def check_drift(self, goal: str, action: str) -> dict:
        """Verifica se l'azione corrente e' allineata al goal."""
        g = goal or self.state.goal
        result = self.drift.check(g, action)
        trend = self.drift.get_trend()
        result["trend"] = trend
        return result

    def update_last_messages(self, messages: list[str]):
        """Aggiorna la cronologia degli ultimi messaggi."""
        self._last_messages = messages
        # Mantieni solo ultimi 5 messaggi
        if len(self._last_messages) > 5:
            self._last_messages = self._last_messages[-5:]

    def clear(self):
        """Resetta la sessione."""
        if self._vector_store:
            self._vector_store.clear_session(self.session_id)
        self.state = SessionState()
        self._last_messages = []
        self.drift = DriftDetector()

    def get_info(self) -> dict:
        """Info sulla sessione corrente."""
        return {
            "session_id": self.session_id,
            "goal": self.state.goal,
            "turn_count": self.state.turn_count,
            "decisions": len(self.state.decisions),
            "files": len(self.state.files_modified),
            "vector_store": self._vector_store is not None,
            "extractor": self.extractor.available,
        }


# ═══════════════════════════════════════════════════════════════════════════
#  CLI
# ═══════════════════════════════════════════════════════════════════════════

def _ascii(s: str) -> str:
    return s.replace('\u2192', '->').replace('\u2713', '[OK]').replace('\u2717', '[X]').replace('\u2500', '-')


def cmd_extract(args):
    text = args.text or ''
    if not text and not sys.stdin.isatty():
        text = sys.stdin.read().strip()
    if not text:
        print('ERRORE: fornisci testo con --text o via pipe')
        sys.exit(1)

    scf = SemanticContextFilter(session_id=args.session_id)
    result = scf.extract_and_store(text)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    else:
        ex = result['extracted']
        print(_ascii(f"Goal: {ex.get('goal', '-')}"))
        print(_ascii(f"Sub-goal: {ex.get('sub_goal', '-')}"))
        print(_ascii(f"Decisioni: {len(ex.get('decisions', []))}"))
        print(_ascii(f"Files: {len(ex.get('files_modified', []))}"))
        print(_ascii(f"Scoperte: {len(ex.get('discoveries', []))}"))
        print(_ascii(f"Errori: {len(ex.get('errors', []))}"))
        print(_ascii(f"Fatti salvati in vettoriale: {len(result['stored'])}"))
        print(_ascii(f"Stato: {result['state']}"))


def cmd_context(args):
    scf = SemanticContextFilter(session_id=args.session_id, goal=args.goal or '')
    ctx = scf.get_compressed_context(query=args.query or '', goal=args.goal or '',
                                     max_facts=args.top_k)
    if args.json:
        print(json.dumps({"context": ctx, "session_id": args.session_id}, ensure_ascii=False, indent=2))
    else:
        print(_ascii(ctx))


def cmd_drift(args):
    scf = SemanticContextFilter(session_id=args.session_id, goal=args.goal or '')
    result = scf.check_drift(args.goal or '', args.action or '')
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        icon = '[OK]' if result['aligned'] else '[X]'
        print(_ascii(f"{icon} Allineato: {result['aligned']} | Score: {result['score']} | {result['reason']}"))
        if result.get('trend'):
            print(_ascii(f"Trend: {result['trend']}"))


def cmd_state(args):
    scf = SemanticContextFilter(session_id=args.session_id)
    if args.show:
        info = scf.get_info()
        if args.json:
            print(json.dumps(info, ensure_ascii=False, indent=2))
        else:
            for k, v in info.items():
                print(_ascii(f"  {k}: {v}"))
    if args.clear:
        scf.clear()
        print(_ascii(f"Sessione '{args.session_id}' resettata."))


def cmd_sessions(args):
    try:
        vs = VectorStore()
        sessions = vs.get_all_sessions()
        if args.json:
            print(json.dumps(sessions, ensure_ascii=False, indent=2))
        else:
            print(_ascii(f"Sessioni attive ({len(sessions)}):"))
            for s in sessions:
                print(_ascii(f"  - {s}"))
    except Exception as e:
        print(_ascii(f"ERRORE: {e}"))


def main():
    p = argparse.ArgumentParser(
        description='Semantic Context Filter - previene context drift in sessioni lunghe',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='Esempi:\n'
               '  %(prog)s extract --text "User says: crea una API. Assistant: fatto."\n'
               '  %(prog)s context --query "cosa fare dopo?" --goal "build API" --top-k 5\n'
               '  %(prog)s drift --goal "refactor modulo" --action "aggiungo test"\n'
               '  %(prog)s state --show --session-id sess_01\n'
               '  %(prog)s state --clear --session-id sess_01\n'
               '  echo "testo lungo" | %(prog)s extract\n',
    )
    p.add_argument('--session-id', '-s', default='default', help='ID sessione')
    p.add_argument('--json', action='store_true', help='Output JSON')

    sp = p.add_subparsers(dest='cmd')

    pe = sp.add_parser('extract', help='Estrai fatti da conversazione')
    pe.add_argument('--text', '-t', default='', help='Testo conversazione')
    pe.add_argument('--session-id', '-s', default='default')

    pc = sp.add_parser('context', help='Genera contesto compresso')
    pc.add_argument('--query', '-q', default='', help='Query corrente')
    pc.add_argument('--goal', '-g', default='', help='Obiettivo')
    pc.add_argument('--top-k', '-k', type=int, default=5)
    pc.add_argument('--session-id', '-s', default='default')

    pd = sp.add_parser('drift', help='Verifica allineamento goal/azione')
    pd.add_argument('--goal', '-g', default='', help='Obiettivo')
    pd.add_argument('--action', '-a', default='', help='Azione corrente')
    pd.add_argument('--session-id', '-s', default='default')

    ps = sp.add_parser('state', help='Mostra/resetta stato sessione')
    ps.add_argument('--show', action='store_true', help='Mostra stato')
    ps.add_argument('--clear', action='store_true', help='Resetta sessione')
    ps.add_argument('--session-id', '-s', default='default')

    pl = sp.add_parser('sessions', help='Lista sessioni attive')

    args = p.parse_args()

    # Propaga --json e --session-id ai subparser
    for sp_name in ['extract', 'context', 'drift', 'state']:
        if hasattr(args, sp_name + '_subparser'):
            pass  # already handled

    if args.cmd == 'extract':
        cmd_extract(args)
    elif args.cmd == 'context':
        cmd_context(args)
    elif args.cmd == 'drift':
        cmd_drift(args)
    elif args.cmd == 'state':
        cmd_state(args)
    elif args.cmd == 'sessions':
        cmd_sessions(args)
    else:
        p.print_help()


if __name__ == '__main__':
    main()