#!/usr/bin/env python3
"""
🌳 MCTS Engine — Monte Carlo Tree Search per decision-making complesso in Oracle.

Genera approcci multipli, li valuta, pota rami deboli, e seleziona il percorso
migliore. Ispirato a MCTS classico ma ottimizzato per LLM inference-time.

Architettura:
  BranchGenerator      → 4-5 rami di pensiero via DeepSeek Flash
  CriticEvaluator      → valuta ogni ramo (coerenza/fattibilita/allineamento)
  TreeState            → traccia nodi esplorati, profondita, visite
  PruningPolicy        → pota rami sotto soglia
  RolloutExecutor      → espande rami promettenti con DeepSeek Pro
  BestPathSelector     → seleziona percorso ottimale

Ciclo d'uso:
  1. engine.analyze(task, context) → genera rami, valuta, pota, rollout, seleziona
  2. Risultato: {best_path, all_branches, stats}

CLI:
  python tools/mcts_engine.py analyze --task "Refactor per async" [--context "..."] [--branches 5]
  python tools/mcts_engine.py eval --branch "Usare FastAPI" --task "Build API"
  python tools/mcts_engine.py rollout --branch "Approccio X" --task "..."

Python:
  from tools.mcts_engine import MCTSEngine
  engine = MCTSEngine()
  result = engine.analyze(task="Refactor per async/await", context="modulo legacy 5000 righe")
  print(result['best_path']['content'])
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

# ── DeepSeek API ──────────────────────────────────────────────────────────

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY") or os.getenv("API_KEY")
DEEPSEEK_ENDPOINT = os.getenv("DEEPSEEK_ENDPOINT", "https://api.deepseek.com/v1")
FLASH_MODEL = os.getenv("MODEL_ID", "deepseek-v4-flash")
PRO_MODEL = os.getenv("MODEL_PRO_ID", "deepseek-v4-pro")

try:
    from openai import OpenAI as OpenAIAPI
    HAS_OPENAI = True
except ImportError:
    HAS_OPENAI = False


# ── Dataclasses ───────────────────────────────────────────────────────────

@dataclass
class Branch:
    """Un ramo dell'albero MCTS = un approccio alternativo."""
    id: int
    title: str                              # Nome breve (es. "Sequenziale")
    description: str                        # Descrizione dell'approccio (~100-300 char)
    tags: list[str] = field(default_factory=list)  # {'sequenziale', 'parallelo', 'wrapper', ...}
    
    # Valutazone Critic
    coherence: float = 0.0                  # 0-1: il ragionamento fila?
    feasibility: float = 0.0                # 0-1: e' tecnicamente fattibile?
    alignment: float = 0.0                  # 0-1: risolve il problema giusto?
    critic_reasoning: str = ""              # Spiegazione del Critic
    
    # Rollout (se promosso)
    rollout_depth: int = 0                  # Quanti passi di rollout
    rollout_content: str = ""               # Contenuto espanso dal rollout
    rollout_tokens: int = 0                 # Token consumati nel rollout
    
    # Metadati
    parent_id: Optional[int] = None         # Branch ID genitore (se sub-ramo)
    visits: int = 0
    is_pruned: bool = False

    @property
    def score(self) -> float:
        """Punteggio composito: media pesata delle 3 metriche."""
        return self.coherence * 0.35 + self.feasibility * 0.35 + self.alignment * 0.30

    @property
    def summary(self) -> str:
        status = "[POTATO]" if self.is_pruned else f"score={self.score:.2f}"
        return f"[#{self.id}] {self.title}: {self.description[:80]}... | {status}"

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "tags": self.tags,
            "coherence": self.coherence,
            "feasibility": self.feasibility,
            "alignment": self.alignment,
            "score": self.score,
            "critic_reasoning": self.critic_reasoning,
            "rollout_depth": self.rollout_depth,
            "rollout_content": self.rollout_content[:200] if self.rollout_content else "",
            "is_pruned": self.is_pruned,
        }


@dataclass
class MCTSConfig:
    """Configurazione dell'MCTS Engine."""
    num_branches: int = 4                   # Rami iniziali da generare
    prune_threshold: float = 0.30           # Sotto soglia → potato
    max_rollout_depth: int = 3              # Passi massimi di approfondimento
    max_rollout_tokens: int = 1500          # Token massimi per rollout
    top_k_for_rollout: int = 2              # Quanti rami promuovere a rollout
    critic_temperature: float = 0.2         # Temperatura per il Critic (bassa = preciso)
    branch_temperature: float = 0.7         # Temperatura per branching (alta = varieta')
    use_pro_for_rollout: bool = True        # Usa Pro per rollout? (costoso)
    use_flash_for_branches: bool = True     # Usa Flash per branching? (economico)
    timeout_per_call: int = 30              # Timeout per chiamata API


# ═══════════════════════════════════════════════════════════════════════════
#  API CLIENT WRAPPER
# ═══════════════════════════════════════════════════════════════════════════

class _DeepSeekClient:
    """Wrapper leggero su OpenAI API per DeepSeek."""

    def __init__(self):
        self._client = None
        self._available = False
        self._consecutive_fails = 0
        if HAS_OPENAI and DEEPSEEK_API_KEY:
            try:
                import httpx
                self._http_client = httpx.Client(timeout=5.0, limits=httpx.Limits(max_keepalive_connections=1))
                self._client = OpenAIAPI(
                    api_key=DEEPSEEK_API_KEY,
                    base_url=DEEPSEEK_ENDPOINT,
                    http_client=self._http_client,
                )
                self._available = True
            except Exception:
                self._available = False

    @property
    def available(self) -> bool:
        return self._available and self._client is not None and self._consecutive_fails < 1

    def ask(self, prompt: str, model: str = FLASH_MODEL,
            temperature: float = 0.3, max_tokens: int = 1000,
            caller_tag: str = "mcts_engine.unknown") -> Optional[str]:
        """Chiamata singola al modello. Ritorna None se fallisce."""
        if not self._client or not self.available:
            return None
        try:
            t0 = time.time()
            resp = self._client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=temperature,
                max_tokens=max_tokens,
            )
            duration = time.time() - t0
            _log_openai_call(caller_tag, duration, resp, model)
            return resp.choices[0].message.content.strip()
        except Exception:
            # Dopo un fallimento, disabilita per il resto della sessione
            self._available = False
            return None

    def parse_json(self, prompt: str, model: str = FLASH_MODEL,
                   temperature: float = 0.1,
                   caller_tag: str = "mcts_engine.unknown") -> Optional[dict]:
        """Chiamata con parsing JSON della risposta."""
        raw = self.ask(prompt, model, temperature, max_tokens=1200, caller_tag=caller_tag)
        if not raw:
            return None
        # Pulisce markdown fences
        raw = re.sub(r'^```(?:json)?\s*', '', raw)
        raw = re.sub(r'\s*```$', '', raw)
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None


_client = _DeepSeekClient()


# ═══════════════════════════════════════════════════════════════════════════
#  BRANCH GENERATOR
# ═══════════════════════════════════════════════════════════════════════════

class BranchGenerator:
    """
    Genera approcci alternativi per risolvere un task.
    Usa DeepSeek Flash se disponibile, altrimenti template rule-based.
    """

    BRANCH_PROMPT = """You are a strategic planning AI. Given a task, generate {num_branches} distinct approaches.

Task: {task}

Context: {context}

For each approach, provide:
- title: short name (2-5 words)
- description: how this approach works (30-80 words)
- tags: list of 2-4 keywords describing the approach style

Make the approaches genuinely DIFFERENT from each other.
Each approach must be a complete, viable strategy.
Think about trade-offs: speed vs quality, simplicity vs power, risk vs reward.

Return ONLY valid JSON in this EXACT format:
{{
  "branches": [
    {{
      "title": "...",
      "description": "...",
      "tags": ["tag1", "tag2"]
    }}
  ]
}}

No markdown. No explanation. Only JSON."""

    FALLBACK_BRANCHES = [
        {
            "title": "Approccio Diretto",
            "description": "Esegui il task in modo lineare e sequenziale. Modifica minima, nessuna refactorization preventiva. Rischio basso, velocita alta.",
            "tags": ["sequenziale", "conservativo", "veloce"],
        },
        {
            "title": "Refactor Preventivo",
            "description": "Prima riorganizza il codice esistente per renderlo piu manutenibile, poi applica le modifiche. Piu tempo iniziale, minori errori futuri.",
            "tags": ["refactor", "manutenibilita", "qualita"],
        },
        {
            "title": "Approccio Ibrido",
            "description": "Combina soluzioni esistenti con nuove implementazioni. Usa pattern gia testati dove possibile, innova solo dove necessario.",
            "tags": ["ibrido", "pragmatico", "bilanciato"],
        },
        {
            "title": "Riscrittura Mirata",
            "description": "Identifica e riscrive solo i componenti critici lasciando intatto il resto. Massimo impatto con minimo rischio di regressioni.",
            "tags": ["mirato", "chirurgico", "preciso"],
        },
        {
            "title": "Prototipo + Iterazione",
            "description": "Costruisci un prototipo funzionante velocemente, poi miglioralo iterativamente. Apprendimento rapido, flessibile ma potenzialmente dispersivo.",
            "tags": ["prototipo", "iterativo", "agile"],
        },
    ]

    def __init__(self, config: MCTSConfig):
        self.config = config

    def generate(self, task: str, context: str = "") -> list[dict]:
        """Genera rami. Ritorna lista di dict {title, description, tags}."""
        if _client.available:
            prompt = self.BRANCH_PROMPT.format(
                num_branches=self.config.num_branches,
                task=task[:500],
                context=context[:500] if context else "N/A",
            )
            model = FLASH_MODEL if self.config.use_flash_for_branches else PRO_MODEL
            result = _client.parse_json(prompt, model, temperature=self.config.branch_temperature,
                                         caller_tag="mcts_engine.branch_generation")
            if result and "branches" in result and len(result["branches"]) >= 2:
                return result["branches"][:self.config.num_branches]

        # Fallback: template predefiniti con contesto
        return self._fallback(task, context)

    def _fallback(self, task: str, context: str) -> list[dict]:
        """Fallback rule-based: seleziona template pertinenti in base al task."""
        task_lower = task.lower()
        
        # Priorita' basata su keyword nel task
        keywords_map = [
            (["bug", "error", "fix", "correggi", "ripara"], 0),
            (["refactor", "rewrite", "restructure", "riorganizza", "ridisegna"], 1),
            (["test", "testing", "verifica", "collaudo"], 2),
            (["feature", "add", "aggiungi", "nuova", "implementa", "crea", "create", "build"], 3),
            (["performance", "optimize", "veloce", "speed", "ottimizza", "fast"], 4),
            (["migrate", "migrazion", "upgrade", "aggiorna", "convert"], 2),
        ]

        best_idx = None
        for keywords, idx in keywords_map:
            if any(k in task_lower for k in keywords):
                best_idx = idx
                break
        if best_idx is None:
            best_idx = 3  # default: ibrido

        # Crea descrizione contestualizzata
        result = []
        for i, fb in enumerate(self.FALLBACK_BRANCHES):
            desc = fb["description"]
            if context:
                desc = desc + f" Contesto: {context[:200]}"
            result.append({
                "title": fb["title"],
                "description": desc,
                "tags": fb["tags"],
            })

        # Ruota per avere l'approccio migliore per primo
        if 0 <= best_idx < len(result):
            result.insert(0, result.pop(best_idx))
        return result[:self.config.num_branches]


# ═══════════════════════════════════════════════════════════════════════════
#  CRITIC EVALUATOR
# ═══════════════════════════════════════════════════════════════════════════

class CriticEvaluator:
    """
    Valuta ogni ramo su 3 dimensioni: coerenza, fattibilita', allineamento.
    Usa DeepSeek Flash se disponibile, altrimenti euristica.
    """

    CRITIC_PROMPT = """You are a rigorous code reviewer and strategist. Evaluate the following approach for a given task.

Task: {task}

Approach: {title}
Description: {description}
Tags: {tags}

Score each dimension from 0.0 (worst) to 1.0 (best):

1. COHERENCE: Does the approach logically follow from the task? Is the reasoning sound?
2. FEASIBILITY: Can this be implemented with available tools/technologies? Is it practical?
3. ALIGNMENT: Does this approach actually solve the stated problem? Does it address the core need?

Also provide a brief reasoning (1-2 sentences).

Return ONLY valid JSON:
{{
  "coherence": 0.0-1.0,
  "feasibility": 0.0-1.0,
  "alignment": 0.0-1.0,
  "reasoning": "Brief explanation"
}}

No markdown. No extra text."""

    def evaluate(self, branch_data: dict, task: str) -> tuple[float, float, float, str]:
        """
        Valuta un ramo.
        Returns (coherence, feasibility, alignment, reasoning).
        """
        if _client.available:
            prompt = self.CRITIC_PROMPT.format(
                task=task[:300],
                title=branch_data.get("title", ""),
                description=branch_data.get("description", "")[:300],
                tags=branch_data.get("tags", []),
            )
            result = _client.parse_json(prompt, model=FLASH_MODEL,
                                         temperature=self.config.critic_temperature,
                                         caller_tag="mcts_engine.critic_eval")
            if result:
                c = max(0.0, min(1.0, result.get("coherence", 0.5)))
                f = max(0.0, min(1.0, result.get("feasibility", 0.5)))
                a = max(0.0, min(1.0, result.get("alignment", 0.5)))
                r = result.get("reasoning", "")[:200]
                return (c, f, a, r)

        # Fallback: scoring euristico
        return self._heuristic_score(branch_data, task)

    def _heuristic_score(self, branch: dict, task: str) -> tuple[float, float, float, str]:
        """Scoring rule-based come fallback."""
        title = (branch.get("title", "") + " " + " ".join(branch.get("tags", []))).lower()
        desc = branch.get("description", "").lower()
        task_lower = task.lower()

        # Coerenza: overlap tra descrizione e task
        task_words = set(w for w in task_lower.split() if len(w) > 3)
        desc_words = set(w for w in desc.split() if len(w) > 3)
        if task_words and desc_words:
            overlap = len(task_words & desc_words)
            coherence = min(1.0, overlap / max(len(task_words) * 0.3, 1))
        else:
            coherence = 0.5

        # Fattibilita': approcci pragmatici hanno punteggio piu alto
        pragmatic_tags = {"sequenziale", "pragmatico", "bilanciato", "conservativo", "veloce", "testato"}
        risky_tags = {"sperimentale", "estremo", "totale", "completo", "azzardato"}
        title_tags = set(title.split()) | set(branch.get("tags", []))
        pragmatic_score = len(title_tags & pragmatic_tags) * 0.2
        risky_penalty = len(title_tags & risky_tags) * 0.15
        feasibility = min(1.0, max(0.1, 0.5 + pragmatic_score - risky_penalty))

        # Allineamento: contiene keyword chiave dal task?
        key_verbs = {"refactor", "rewrite", "fix", "add", "implement", "create",
                     "build", "test", "optimize", "migrate", "convert", "update"}
        task_keys = task_words & key_verbs
        desc_keys = desc_words & key_verbs
        if task_keys and desc_keys:
            alignment = len(desc_keys & task_keys) / max(len(task_keys), 1)
        elif not task_keys:
            alignment = 0.6  # task senza verbi chiave
        else:
            alignment = 0.2

        reasoning = f"Coerenza={coherence:.1f}, Fattibilita={feasibility:.1f}, Allineamento={alignment:.1f}"
        return (coherence, feasibility, alignment, reasoning)


# ═══════════════════════════════════════════════════════════════════════════
#  TREE STATE
# ═══════════════════════════════════════════════════════════════════════════

class TreeState:
    """Tiene traccia dello stato dell'albero MCTS."""

    def __init__(self):
        self.branches: list[Branch] = []
        self._next_id: int = 0

    def add_branch(self, branch_data: dict, parent_id: Optional[int] = None) -> Branch:
        """Crea un Branch da dict e lo aggiunge all'albero."""
        b = Branch(
            id=self._next_id,
            title=branch_data.get("title", "Untitled"),
            description=branch_data.get("description", ""),
            tags=branch_data.get("tags", []),
            parent_id=parent_id,
        )
        self._next_id += 1
        self.branches.append(b)
        return b

    def get_branch(self, branch_id: int) -> Optional[Branch]:
        for b in self.branches:
            if b.id == branch_id:
                return b
        return None

    def get_active(self) -> list[Branch]:
        """Rami non potato."""
        return [b for b in self.branches if not b.is_pruned]

    def get_pruned(self) -> list[Branch]:
        return [b for b in self.branches if b.is_pruned]

    def get_best(self) -> Optional[Branch]:
        active = self.get_active()
        if not active:
            return None
        return max(active, key=lambda b: b.score)

    def to_dict(self) -> dict:
        return {
            "total_branches": len(self.branches),
            "active": len(self.get_active()),
            "pruned": len(self.get_pruned()),
            "branches": [b.to_dict() for b in self.branches],
        }


# ═══════════════════════════════════════════════════════════════════════════
#  PRUNING POLICY
# ═══════════════════════════════════════════════════════════════════════════

class PruningPolicy:
    """Pota rami deboli. Soglia configurabile."""

    def __init__(self, threshold: float = 0.30):
        self.threshold = threshold

    def prune(self, tree: TreeState) -> list[Branch]:
        """
        Potatura: segna come is_pruned i rami con score < threshold.
        Mantiene almeno 1 ramo anche se tutti sotto soglia.
        Returns lista dei rami potato.
        """
        pruned = []
        active = tree.get_active()
        if len(active) <= 1:
            return []

        # Trova il migliore per proteggerlo
        best = tree.get_best()

        for b in active:
            if b.id == (best.id if best else -1):
                continue  # non potare mai il migliore
            if b.score < self.threshold:
                b.is_pruned = True
                pruned.append(b)

        return pruned


# ═══════════════════════════════════════════════════════════════════════════
#  ROLLOUT EXECUTOR
# ═══════════════════════════════════════════════════════════════════════════

class RolloutExecutor:
    """
    Espande rami promettenti con ragionamento approfondito.
    Se Pro disponibile, usa quello per rollout di qualita'.
    """

    ROLLOUT_PROMPT = """You are an expert software engineer expanding an approach into a detailed implementation plan.

Task: {task}
Approach: {title}
Description: {description}

Current context: {context}

Expand this approach into a concrete plan:
1. Key steps (numbered, 3-8 steps)
2. Files that need to be created or modified
3. Any dependencies or tools needed
4. Potential pitfalls and how to avoid them
5. Success criteria

Be specific and actionable. Include code structure, function signatures, or architecture details where relevant.

Rollout step {depth}/{max_depth}:"""

    ROLLOUT_CONTINUE_PROMPT = """Continue the rollout from where you left off.

Previous rollout content: {previous_content}

Continue with the next steps. Focus on implementation details, code structure, and edge cases.

Rollout step {depth}/{max_depth}:"""

    def __init__(self, config: MCTSConfig):
        self.config = config

    def rollout(self, branch: Branch, task: str, context: str = "") -> Branch:
        """
        Esegue rollout su un ramo: approfondimento progressivo.
        Modifica e ritorna lo stesso branch.
        """
        if not _client.available:
            # Rollout fallback: genera contenuto strutturato
            branch.rollout_content = self._fallback_rollout(branch, task)
            branch.rollout_depth = 1
            branch.rollout_tokens = len(branch.rollout_content) // 2
            return branch

        model = PRO_MODEL if self.config.use_pro_for_rollout else FLASH_MODEL
        accumulated = []
        max_tokens_per_step = min(
            self.config.max_rollout_tokens // self.config.max_rollout_depth,
            500,
        )

        for depth in range(1, self.config.max_rollout_depth + 1):
            if depth == 1:
                prompt = self.ROLLOUT_PROMPT.format(
                    task=task[:300],
                    title=branch.title,
                    description=branch.description[:200],
                    context=context[:300] if context else "N/A",
                    depth=depth,
                    max_depth=self.config.max_rollout_depth,
                )
            else:
                prompt = self.ROLLOUT_CONTINUE_PROMPT.format(
                    previous_content="\n".join(accumulated[-3:]) if accumulated else "",
                    depth=depth,
                    max_depth=self.config.max_rollout_depth,
                )

            content = _client.ask(
                prompt, model=model,
                temperature=0.3,
                max_tokens=max_tokens_per_step,
                caller_tag="mcts_engine.rollout",
            )
            if content:
                accumulated.append(f"[Step {depth}] {content}")
            else:
                # Se API fallisce, interrompi rollout
                break

        branch.rollout_content = "\n\n".join(accumulated)
        branch.rollout_depth = len(accumulated)
        # Stima token: 4 char ≈ 1 token
        branch.rollout_tokens = len(branch.rollout_content) // 4

        # Se rollout vuoto, fallback
        if not branch.rollout_content:
            branch.rollout_content = self._fallback_rollout(branch, task)
            branch.rollout_depth = 1

        return branch

    def _fallback_rollout(self, branch: Branch, task: str) -> str:
        """Rollout template quando API non disponibile."""
        desc = branch.description[:200]
        title = branch.title
        steps = [
            f"1. Analisi: valutare requisiti del task '{task[:80]}'",
            f"2. Applicare approccio '{title}': {desc[:150]}",
            "3. Implementare soluzione seguendo il piano",
            "4. Testare e verificare i risultati",
            "5. Iterare su eventuali problemi emersi",
        ]
        return "\n".join(steps)


# ═══════════════════════════════════════════════════════════════════════════
#  BEST PATH SELECTOR
# ═══════════════════════════════════════════════════════════════════════════

class BestPathSelector:
    """
    Seleziona il ramo migliore dopo pruning e rollout.
    Usa score composito ma considera anche profondita' rollout come bonus.
    """

    @staticmethod
    def select(tree: TreeState) -> Optional[Branch]:
        """Seleziona il ramo ottimale."""
        active = tree.get_active()
        if not active:
            return None
        if len(active) == 1:
            return active[0]

        # Score composito con bonus rollout
        def composite(b: Branch) -> float:
            base = b.score
            # Bonus per rollout profondo (dimostra esplorazione approfondita)
            rollout_bonus = min(0.15, b.rollout_depth * 0.05)
            return base + rollout_bonus

        return max(active, key=composite)


# ═══════════════════════════════════════════════════════════════════════════
#  MCTS ENGINE (Engine principale)
# ═══════════════════════════════════════════════════════════════════════════

class MCTSEngine:
    """
    Engine MCTS principale.
    
    Ciclo completo:
      1. generate_branches(task, context) → genera N rami
      2. evaluate_branches(task) → Critic valuta ogni ramo
      3. prune() → elimina rami deboli
      4. rollout(task, context) → approfondisce rami promettenti
      5. select_best() → sceglie il migliore
      
    Uso:
        engine = MCTSEngine()
        result = engine.analyze(task="Refactor modulo X per async")
        print(result['best_path']['content'])
    """

    def __init__(self, config: Optional[MCTSConfig] = None):
        self.config = config or MCTSConfig()
        self.generator = BranchGenerator(self.config)
        self.critic = CriticEvaluator()
        # Passa config al critic
        self.critic.config = self.config
        self.tree = TreeState()
        self.pruner = PruningPolicy(threshold=self.config.prune_threshold)
        self.rollout_exec = RolloutExecutor(self.config)
        self.selector = BestPathSelector()

    def analyze(self, task: str, context: str = "") -> dict:
        """
        Ciclo MCTS completo.
        
        Args:
            task: Descrizione del task da risolvere
            context: Contesto aggiuntivo (es. codice corrente, vincoli)
            
        Returns:
            dict con: best_path, all_branches, stats
        """
        t0 = time.time()

        # 1. Generazione rami
        raw_branches = self.generator.generate(task, context)
        for rb in raw_branches:
            self.tree.add_branch(rb)
        num_initial = len(self.tree.branches)

        # 2. Valutazione Critic
        for b in self.tree.branches:
            c, f, a, r = self.critic.evaluate(
                {"title": b.title, "description": b.description, "tags": b.tags},
                task,
            )
            b.coherence = c
            b.feasibility = f
            b.alignment = a
            b.critic_reasoning = r

        # 3. Pruning
        pruned = self.pruner.prune(self.tree)

        # 4. Rollout sui rami migliori
        active = sorted(self.tree.get_active(), key=lambda x: x.score, reverse=True)
        for b in active[:self.config.top_k_for_rollout]:
            self.rollout_exec.rollout(b, task, context)

        # 5. Selezione
        best = self.selector.select(self.tree)

        duration = time.time() - t0

        # Stats
        return {
            "best_path": best.to_dict() if best else None,
            "all_branches": [b.to_dict() for b in self.tree.branches],
            "pruned_branches": [b.to_dict() for b in pruned],
            "stats": {
                "total_initial": num_initial,
                "active": len(self.tree.get_active()),
                "pruned": len(pruned),
                "rollout_executed": sum(1 for b in self.tree.branches if b.rollout_depth > 0),
                "duration_sec": round(duration, 2),
                "api_available": _client.available,
                "config": {
                    "num_branches": self.config.num_branches,
                    "prune_threshold": self.config.prune_threshold,
                    "max_rollout_depth": self.config.max_rollout_depth,
                    "top_k_for_rollout": self.config.top_k_for_rollout,
                },
            },
        }


# ═══════════════════════════════════════════════════════════════════════════
#  CLI
# ═══════════════════════════════════════════════════════════════════════════

def _ascii(s: str) -> str:
    return s.replace('\u2192', '->').replace('\u2713', '[OK]').replace('\u2717', '[X]')


def cmd_analyze(args):
    task = args.task or ''
    if not task and not sys.stdin.isatty():
        task = sys.stdin.read().strip()
    if not task:
        print('ERRORE: fornisci task con --task o via pipe')
        sys.exit(1)

    config = MCTSConfig(
        num_branches=args.branches,
        prune_threshold=args.threshold,
        max_rollout_depth=args.depth,
        top_k_for_rollout=args.rollout_branches,
        use_pro_for_rollout=not args.no_pro,
    )
    engine = MCTSEngine(config)
    result = engine.analyze(task, args.context or '')

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    else:
        print(_ascii(f"\n=== MCTS Analysis: {task[:60]}... ==="))
        print(_ascii(f"API: {'OK' if _client.available else 'FALLBACK (no API)'}"))
        print(_ascii(f"Rami generati: {result['stats']['total_initial']}"))
        print(_ascii(f"Potati: {result['stats']['pruned']}"))
        print(_ascii(f"Rollout eseguiti: {result['stats']['rollout_executed']}"))
        print(_ascii(f"Durata: {result['stats']['duration_sec']}s"))

        print(_ascii(f"\n--- Rami attivi ---"))
        for b in result['all_branches']:
            if not b['is_pruned']:
                print(_ascii(f"  #{b['id']} {b['title']} | score={b['score']:.2f} (C={b['coherence']:.1f} F={b['feasibility']:.1f} A={b['alignment']:.1f})"))
                if b['critic_reasoning']:
                    print(_ascii(f"     Critic: {b['critic_reasoning'][:120]}"))

        if result.get('pruned_branches'):
            print(_ascii(f"\n--- Rami potato ---"))
            for b in result['pruned_branches']:
                print(_ascii(f"  X #{b['id']} {b['title']} | score={b['score']:.2f}"))

        best = result['best_path']
        if best:
            print(_ascii(f"\n=== MIGLIOR APPROCCIO ==="))
            print(_ascii(f"  #{best['id']} {best['title']} (score={best['score']:.2f})"))
            print(_ascii(f"  {best['description'][:200]}"))
            if best.get('rollout_content'):
                print(_ascii(f"\n  Rollout ({best['rollout_depth']} passi):"))
                print(_ascii(f"  {best['rollout_content'][:500]}"))


def cmd_branches(args):
    """Genera solo branch senza evaluazione completa."""
    task = args.task or ''
    if not task:
        print('ERRORE: --task richiesto')
        sys.exit(1)
    config = MCTSConfig(num_branches=args.count)
    gen = BranchGenerator(config)
    branches = gen.generate(task, args.context or '')
    if args.json:
        print(json.dumps(branches, ensure_ascii=False, indent=2))
    else:
        for i, b in enumerate(branches, 1):
            print(_ascii(f"[{i}] {b['title']}"))
            print(_ascii(f"    {b['description'][:120]}"))
            print(_ascii(f"    tags: {', '.join(b['tags'])}"))


def main():
    p = argparse.ArgumentParser(
        description='MCTS Engine - Monte Carlo Tree Search per decision-making',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            'Esempi:\n'
            '  %(prog)s analyze --task "Refactor modulo X per async/await"\n'
            '  %(prog)s analyze --task "Fix memory leak" --context "codice legacy 10k righe"\n'
            '  %(prog)s analyze --task "..." --branches 6 --depth 5\n'
            '  %(prog)s analyze --task "..." --json\n'
            '  cat task.txt | %(prog)s analyze\n'
            '  %(prog)s branches --task "Build REST API" --count 3\n'
        ),
    )
    sp = p.add_subparsers(dest='cmd')

    pa = sp.add_parser('analyze', help='Ciclo MCTS completo')
    pa.add_argument('--task', '-t', default='', help='Task da analizzare')
    pa.add_argument('--context', '-c', default='', help='Contesto aggiuntivo')
    pa.add_argument('--branches', '-b', type=int, default=4, help='Numero rami (default: 4)')
    pa.add_argument('--threshold', '-T', type=float, default=0.30, help='Soglia pruning (default: 0.30)')
    pa.add_argument('--depth', '-d', type=int, default=3, help='Profondita rollout (default: 3)')
    pa.add_argument('--rollout-branches', '-r', type=int, default=2, help='Rami da rolloutare (default: 2)')
    pa.add_argument('--no-pro', action='store_true', help='Usa Flash invece di Pro per rollout')
    pa.add_argument('--json', action='store_true', help='Output JSON')

    pb = sp.add_parser('branches', help='Genera solo rami (senza eval/prune/rollout)')
    pb.add_argument('--task', '-t', default='', help='Task')
    pb.add_argument('--context', '-c', default='')
    pb.add_argument('--count', '-n', type=int, default=4)
    pb.add_argument('--json', action='store_true')

    args = p.parse_args()
    if args.cmd == 'analyze':
        cmd_analyze(args)
    elif args.cmd == 'branches':
        cmd_branches(args)
    else:
        p.print_help()


if __name__ == '__main__':
    main()