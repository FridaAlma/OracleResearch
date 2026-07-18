# Oracle Tool Catalog
## Riferimento completo — carica con `read_file("tools/tool_catalog.md")` per dettagli

### CodingTools (built-in, sempre disponibili)
- **read_file**: Legge file con line numbers. Parametri: file_path, offset, limit.
- **edit_file**: Edits file via find/replace esatto. Parametri: file_path, old_text, new_text.
- **write_file**: Crea/sovrascrive file. Crea automaticamente le directory parent.
- **run_shell**: Esegue comandi shell con timeout. Parametri: command, timeout.
- **grep**: Cerca pattern in file. Parametri: pattern, path, include, ignore_case, context.
- **find**: Cerca file per glob. Parametri: pattern, path.
- **ls**: Elenca directory. Parametri: path, limit.

### 1.3 Memory (Sqlite, built-in)
`coding_agent.db` — History auto-salvata, agentic memory, ultime 3 run iniettate.

### 1.4 Vector Memory
**File**: `tools/vector_memory.py`
**CLI**: `python tools/vector_memory.py <add|search|get|delete|list-collections|info|image-stats>`
**Python**: `from tools.vector_memory import VectorMemoryEngine`
**Multimodal**: `from tools.multimodal_encoder import MultimodalEncoder` (CLIP)
**Collections**: knowledge, projects/*, sessions/*, code/*, user/*

### 1.5 Environment Probe
**File**: `tools/environment_probe.py`
**CLI**: `python tools/environment_probe.py <port|dep|fs|env|check>`
**Python**: `from tools.environment_probe import EnvironmentProbe, quick_probe, is_feasible`
**Output**: FEASIBLE / BLOCKED. **Mandatory** prima di build complessi.

### 1.6 Interleaved Sandbox
**File**: `tools/interleaved_sandbox.py`
**Funzioni**: analyze(text), analyze_stream(tokens), run_block(code, lang)
**SafetyFilter**: Blocca rm -rf /, fork bomb, dd. AST pre-validation + refinement loop.

### 1.7 Semantic Context Filter
**File**: `tools/semantic_context_filter.py`
**API**: extract_and_store(conv) → get_compressed_context(query, goal) → check_drift(goal, action)
**Uso**: Sessioni >5 turni per prevenire drift.

### 1.8 MCTS Engine
**File**: `tools/mcts_engine.py`
**API**: analyze(task, context) → 4-5 branches → evaluate (coherence/feasibility/alignment) → prune → rollout → best path.

### 1.9 Oracle Protocol
**File**: `tools/oracle_protocol.py`
**API**: ComplexityDetector (simple/standard/complex). Entry point per richieste orchestrate.

### 1.10 WebAccess
**File**: `tools/web_access.py`
**Features**: HTTP GET/POST/DOWNLOAD con retry, SSRF protection, SQLite caching, rate limiting, HTML scraping.

---

## Tool Lifecycle
- **VOLATILE**: root (./), TTL=1h. Scripts: analyze_*, cleanup_*, test_*, check_*, fetch_*, _*
- **PERSISTENT**: workspace/, TTL=infinite
- **GENERATED_ARTIFACT**: where needed, TTL=30min
- **Cleanup**: `python workspace/tool_lifecycle.py cleanup`
- **Repository**: `python workspace/tool_repository.py search "keywords"` — cerca prima di creare
- **Promote**: `python workspace/tool_repository.py promote --name X --source Y --desc "Z" --tags "a,b,c"`