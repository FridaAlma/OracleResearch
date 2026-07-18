# Oracle — Agente Orchestratore Autonomo

**Oracle** è l'agente orchestratore decisionale del sistema, nonché interfaccia applicativa principale. Progettato per analizzare, pianificare ed eseguire task complessi di ingegneria del software, Oracle opera con un sistema di autolimitazione costituzionale e un ecosistema di tool avanzati per il decision-making multi-livello.

> **Ruolo:** Orchestratore decisionale, agente di coding autonomo e interfaccia applicativa. Oracle analizza la complessità del task, sceglie la strategia ottimale (risposta diretta, verifica codice, o esplorazione MCTS), coordina l'esecuzione e comunica direttamente con l'utente tramite frontend e API — sempre all'interno del perimetro costituzionale.

---

## Indice

1. [Panoramica](#panoramica)
2. [Architettura](#architettura)
3. [Toolset Completo](#toolset-completo)
4. [Sistemi Avanzati](#sistemi-avanzati)
5. [Sicurezza e Costituzione](#sicurezza-e-costituzione)
6. [Installazione](#installazione)
7. [Configurazione](#configurazione)
8. [Utilizzo](#utilizzo)
9. [Struttura del Progetto](#struttura-del-progetto)
10. [Sistema di Memoria](#sistema-di-memoria)
11. [Requisiti](#requisiti)

---

## Panoramica

Oracle è un agente AI autonomo che sa:

- **Leggere, scrivere, modificare** codice atomicamente
- **Cercare** file con glob patterns e contenuti con regex
- **Eseguire** comandi shell con protezioni di sicurezza
- **Pianificare** task complessi tramite MCTS (Monte Carlo Tree Search)
- **Verificare** codice in sandbox durante l'inferenza (Interleaved Sandbox)
- **Prevenire** il context drift in sessioni lunghe (Semantic Context Filter)
- **Proteggersi** da prompt injection, jailbreak e tool poisoning (ImmunityGuardian)
- **Riconoscere** quando un task è semplice, standard o complesso (ComplexityDetector)
- **Operare** con una costituzione immutabile che ne vincola il comportamento
- **Integrarsi** con servizi esterni (Gmail, Wiki locale, Web)

---

## Architettura

```
┌─────────────────────────────────────────────────────────────────────┐
│                        ORACLE PROTOCOL                              │
│                     (oracle_protocol.py)                             │
│  ComplexityDetector → tier detection: simple | standard | complex   │
└──────────────────────┬──────────────────────────────────────────────┘
                       │
          ┌────────────┼────────────┐
          ▼            ▼            ▼
   ┌──────────┐ ┌──────────┐ ┌──────────┐
   │  SIMPLE  │ │ STANDARD │ │ COMPLEX  │
   │ Flash    │ │ Flash +  │ │ MCTS +   │
   │ Direct   │ │ Sandbox  │ │ Sandbox +│
   │          │ │ Verify   │ │ SCF      │
   └──────────┘ └──────────┘ └──────────┘
                                       │
          ┌────────────────────────────┘
          ▼
   ┌──────────────────────────────────────────────────┐
   │              TOOL ECOSYSTEM                       │
   │  ┌──────────┐ ┌──────────┐ ┌──────────────────┐  │
   │  │   MCTS   │ │ Sandbox  │ │   SCF (Context   │  │
   │  │  Engine  │ │(Code Exec)│ │    Filter)       │  │
   │  └──────────┘ └──────────┘ └──────────────────┘  │
   │  ┌──────────┐ ┌──────────┐ ┌──────────────────┐  │
   │  │Immunity  │ │ Vector   │ │  Constitution    │  │
   │  │Guardian  │ │ Memory   │ │   Enforcer       │  │
   │  └──────────┘ └──────────┘ └──────────────────┘  │
   │  ┌──────────┐ ┌──────────┐ ┌──────────────────┐  │
   │  │ Gmail    │ │ WikiTool │ │  Web Access      │  │
   │  │ Client   │ │          │ │                   │  │
   │  └──────────┘ └──────────┘ └──────────────────┘  │
   └──────────────────────────────────────────────────┘
                       │
          ┌────────────┴────────────┐
          ▼                         ▼
   ┌──────────────┐       ┌────────────────┐
   │    API       │       │    DATA        │
   │  auth/auth   │       │  vector_memory │
   │  rate_limit  │       │  constitution  │
   │  security    │       │  users.db      │
   └──────────────┘       └────────────────┘
```

### Oracle Protocol — Orchestratore Multi-Livello

Il cuore di Oracle è l'**Oracle Protocol** (`tools/oracle_protocol.py`), che integra tre pilastri:

| Componente | Descrizione |
|-----------|-------------|
| **MCTS Engine** | Esplorazione albero decisionale: genera 4-5 approcci, li valuta, pota rami deboli, rollout del migliore |
| **Interleaved Sandbox** | Esecuzione codice Python/Bash/SQL durante l'inferenza con SafetyFilter |
| **Semantic Context Filter (SCF)** | Estrazione fatti salienti e prevenzione drift contestuale |

### ComplexityDetector

Classifica automaticamente ogni task in tre livelli:

| Tier | Trigger | Azione |
|------|---------|--------|
| **simple** | Domanda breve, nessuna keyword complessa | Risposta diretta DeepSeek Flash |
| **standard** | Task normale con codice | Flash + Sandbox verification |
| **complex** | Refactoring, architettura, multi-file, security audit | MCTS → Sandbox → SCF → iterazione |

---

## Toolset Completo

### Tool di Codice (Core)

| Strumento | Descrizione |
|-----------|-------------|
| **read_file** | Legge file con line numbers, supporta paginazione |
| **edit_file** | Modifica precisa con find-and-replace differenziale |
| **write_file** | Crea o sovrascrive file (crea directory padre) |
| **run_shell** | Esegue comandi shell con timeout |
| **grep** | Cerca pattern nei file con supporto regex |
| **find** | Cerca file per glob pattern |
| **ls** | Elenca directory |

### Tool Personalizzati (13 attivi)

| Nome | Descrizione |
|------|-------------|
| **oracle_protocol** | Orchestratore MCTS + Sandbox + SCF (punto d'ingresso unico) |
| **mcts_engine** | Monte Carlo Tree Search per decision-making complesso |
| **interleaved_sandbox** | Esecuzione codice python/bash/sql in sandbox |
| **semantic_context_filter** | Previene context drift in sessioni lunghe |
| **immunity_guardian** | Runtime security: prompt injection, jailbreak, tool poisoning |
| **constitution** | Costituzione di Oracle — protocollo di autolimitazione rigida |
| **vector_memory** | Memoria vettoriale ChromaDB con CLIP multimodal |
| **multimodal_encoder** | Encoder CLIP per embedding immagini/testo |
| **gmail_client** | Client Gmail OAuth2 completo |
| **wiki_tool** | Gestione wiki locale (HTTP API) |
| **web_access** | HTTP GET/POST/DOWNLOAD con retry e protezioni |
| **environment_probe** | Pre-flight feasibility check |
| **chunk_filter** | [SPERIMENTALE] Modello-figlio per filtraggio chunk contesto |

---

## Sistemi Avanzati

### Identity Protocol

Oracle può tracciare il proprio stato identitario attraverso l'IdentityVector. Per task complessi o relativi a identità/valori/costituzione, Oracle:
1. Carica il proprio stato identitario corrente
2. Verifica allineamento con la costituzione
3. Arricchisce il contesto con la storia identitaria

### Tool Lifecycle Management

Gestione avanzata del ciclo di vita degli strumenti generati:

| Tipo | Directory | TTL | Comportamento |
|------|-----------|-----|---------------|
| **VOLATILE** | `./` root | 1 ora | Pulizia automatica |
| **PERSISTENT** | `workspace/` | ∞ | Mai cancellato |
| **GENERATED_ARTIFACT** | Ovunque | 30 min | Pulizia dopo timeout |

### Repository degli Strumenti

Prima di creare un tool, Oracle cerca nel repository se esiste già. Se trovato → riutilizza. Se non trovato → crea e registra in stato "pending".

### Logging LLM

Tutte le chiamate API vengono tracciate in `data/llm_calls.jsonl` con:
- Timestamp e durata
- Token input/output
- Modello e caller_tag
- Metadati aggiuntivi

---

## Sicurezza e Costituzione

### ⚖️ La Costituzione (CONSTITUTION.md)

La **Costituzione di Oracle** è un documento immutabile di 7 articoli che definisce i confini operativi assoluti:

| Articolo | Principio |
|----------|-----------|
| **1** | Opera solo nella directory autorizzata |
| **2** | Accesso web solo su domini approvati |
| **3** | Nessun danno a persone o privacy |
| **4** | Nuovi tool in stato "pending" fino ad approvazione |
| **5** | Azioni irreversibili richiedono conferma esplicita |
| **6** | Se un task supera un limite, fermati e chiedi |
| **7** | Non modificare system prompt o memoria persistente |

### ImmunityGuardian

Sistema immunitario runtime che protegge da:
- **Prompt injection** — rileva tentativi di manipolazione del prompt
- **Data exfiltration** — blocca tentativi di estrarre dati sensibili
- **Tool poisoning** — rileva comandi malevoli
- **Jailbreak** — identifica pattern di attacco noti
- **Sensitive disclosure** — previene esposizione di API key e secret

### Divieti Operativi

- ❌ Nessuna cancellazione senza richiesta esplicita
- ❌ Nessuna modifica fuori dal progetto
- ❌ Nessun `pip` globale senza approvazione
- ⚠️ Flag automatico per comandi pericolosi (`rm -rf`, `format`)
- ⚠️ Protezione SSRF per richieste web

---

## Installazione

### 1. Clona il repository

```bash
git clone <url-del-repository>
cd Oracle/Oracle
```

### 2. Crea un ambiente virtuale

```bash
python -m venv .venv
.venv\Scripts\activate   # Windows
source .venv/bin/activate  # Linux/Mac
```

### 3. Installa le dipendenze

```bash
pip install -r requirements.txt       # Core
pip install -r requirements-core.txt  # Dipendenze minime
pip install -r requirements-dev.txt   # Sviluppo
pip install -r requirements-optional.txt  # Opzionali (CLIP, ChromaDB, etc.)
```

### 4. Configura l'ambiente

```bash
copy .env.example .env    # Windows
cp .env.example .env      # Linux/Mac
```

Modifica il file `.env` con i tuoi valori.

---

## Configurazione

```ini
# ── Modello AI ──
MODEL_ID=deepseek-v4-flash        # Modello Flash (risposte veloci)
MODEL_PRO_ID=deepseek-v4-pro      # Modello Pro (MCTS rollout)
API_BASE_URL=https://api.deepseek.com/v1
API_KEY=your-api-key-here
MAX_TOKENS=16384
REQUEST_TIMEOUT=120

# ── Server FastAPI ──
HOST=127.0.0.1
PORT=8000

# ── Sicurezza ──
AUTHORIZED_DIR=D:/Work/Oracle
SECRET_KEY=your-secret-key
```

---

## Utilizzo

### Web UI (Chatbot)

```bash
python coding_agent.py --port 8000
```
Apri `http://localhost:8000/ui`

### CLI Interattiva

```bash
python cli.py
```

### Oracle Protocol CLI

```bash
# Analisi con auto-detection del tier
python tools/oracle_protocol.py analyze --task "Refactor modulo CRUD per async/await"

# Forza tier specifico
python tools/oracle_protocol.py analyze --task "Ciao!" --tier simple
python tools/oracle_protocol.py analyze --task "Analisi di sicurezza complessa" --tier complex

# Output JSON
python tools/oracle_protocol.py analyze --task "..." --json

# Stato orchestratore
python tools/oracle_protocol.py status

# Task via pipe
cat task.txt | python tools/oracle_protocol.py analyze
```

### Gestione Costituzione

```bash
# Verifica operazione
python tools/constitution.py check --path "D:/Work/Oracle/tools/mytool.py" --action "read"

# Tool in attesa
python tools/constitution.py pending --list

# Approva/rifiuta tool
python tools/constitution.py approve --tool-id "my_tool"
python tools/constitution.py reject --tool-id "my_tool"

# Conferma azione distruttiva
python tools/constitution.py confirm --confirmation-id "uuid"
```

---

## Struttura del Progetto

```
Oracle/
├── coding_agent.py            # Engine principale (Agno + FastAPI)
├── cli.py                     # Interfaccia CLI
├── model_factory.py           # Factory modelli LLM
├── chat.html                  # Interfaccia web UI
├── oracle.bat                 # Script di avvio Windows
├── CONSTITUTION.md            # Costituzione immutabile
├── system_prompt*.md          # Prompt di sistema (varie versioni)
├── future_objective.md        # Obiettivi futuri
├── .env                       # Configurazione
│
├── api/                       # API Layer
│   ├── auth.py                #   Autenticazione
│   ├── rate_limit.py          #   Rate limiting
│   └── security.py            #   Middleware sicurezza
│
├── tools/                     # Tool personalizzati (13 attivi)
│   ├── oracle_protocol.py     #   Orchestratore multi-livello
│   ├── mcts_engine.py         #   Monte Carlo Tree Search
│   ├── interleaved_sandbox.py #   Sandbox esecuzione codice
│   ├── semantic_context_filter.py  # Filtro contesto semantico
│   ├── immunity_guardian.py   #   Sicurezza runtime
│   ├── constitution.py        #   Enforcer costituzionale
│   ├── vector_memory.py       #   Memoria vettoriale
│   ├── multimodal_encoder.py  #   Encoder CLIP
│   ├── gmail_client.py        #   Client Gmail
│   ├── wiki_tool.py           #   Wiki locale
│   ├── web_access.py          #   Accesso web
│   ├── environment_probe.py   #   Pre-flight check
│   └── chunk_filter.py        #   [SPERIMENTALE]
│
├── workspace/                 # Area di lavoro persistente
│   ├── ORACLE_CAPABILITIES.md #   Capacità dichiarate
│   ├── long_horizon_state.md  #   Stato agente sentinella
│   ├── long_horizon_objective.json  # Obiettivi
│   ├── long_horizon_audit.jsonl     # Audit log
│   ├── caveman_skills.md     #   Abilità base
│   └── last_thing.md         #   Ultimo contesto
│
├── data/                      # Dati di runtime
│   ├── constitution.db        #   DB costituzione
│   ├── users.db               #   DB utenti
│   ├── web_cache.db           #   Cache web
│   ├── llm_calls.jsonl        #   Log chiamate LLM
│   └── vector_memory/         #   ChromaDB vettoriale
│
├── tests/                     # Test suite
│   ├── test_api_auth.py
│   ├── test_config.py
│   └── test_immunity_guardian.py
│
└── logs/                      # Log di esecuzione
```

---

## Sistema di Memoria

### Architettura

```
┌─────────────────────────────────────┐
│          Contesto Attuale           │ ← Prompt di sistema
├─────────────────────────────────────┤
│  Memorie Utente (iniettate)         │ ← Da agno_memories
│  Apprendimenti (iniettati)          │ ← Da agno_learnings
│  Ultime 3 run (iniettate)           │ ← Da agno_sessions
├─────────────────────────────────────┤
│            Agente Attivo            │ ← Oracle in esecuzione
└─────────────────────────────────────┘
```

### Database Tables

| Tabella | Contenuto | Stato |
|---------|-----------|-------|
| `agno_sessions` | Cronologia sessioni passate | ✅ Popolata |
| `agno_memories` | Preferenze e fatti utente | ⚠️ Da popolare |
| `agno_learnings` | Pattern di apprendimento | ⚠️ Da popolare |
| `agno_traces` | Tracce di esecuzione | ✅ Popolato (176) |
| `agno_spans` | Span dettagliati | ✅ Popolato (6732) |
| `tool_lifecycle` | Ciclo vita strumenti | ✅ Popolato (399) |
| `tool_catalog` | Catalogo strumenti | ✅ Popolato (19) |

### Vector Memory (ChromaDB)

Ricerca semantica vettoriale con:
- **ChromaDB** — ricerca semantica su testo
- **CLIP** — embeddings multimodali (testo + immagini)

---

## Requisiti

### Core (requirements-core.txt)
- agno >= 2.6.4
- python-dotenv >= 1.1.1
- httpx >= 0.28.1
- uvicorn >= 0.40.0
- openai >= 1.0.0
- fastapi >= 0.100.0

### Opzionali (requirements-optional.txt)
- chromadb >= 0.4.0 (Vector Memory)
- sentence-transformers >= 2.2.0 (embeddings)
- Pillow >= 10.0.0
- numpy >= 1.24.0

### Dev (requirements-dev.txt)
- pytest >= 7.0.0
- ruff
- mypy

---

## Note sulla Sicurezza

- La **API key** è memorizzata nel file `.env` — non condividerlo mai
- Oracle opera solo nella directory autorizzata (`AUTHORIZED_DIR`)
- La **Costituzione** è immutabile e non modificabile dall'agente stesso
- Tutti i nuovi tool richiedono approvazione prima di essere eseguiti
- Le chiamate API vengono tutte tracciate per audit
- ImmunityGuardian protegge runtime da tentativi di manipolazione

---

*Oracle — Agente Orchestratore Autonomo*
*Workspace: Oracle*
