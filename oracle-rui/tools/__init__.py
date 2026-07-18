"""
Tool Repository — Tool riutilizzabili promossi dal lifecycle.

Ogni file in questa directory è un tool registrato nel catalogo
`tool_catalog` del database `coding_agent.db`, gestito da
`workspace.tool_repository.py`.

Per cercare tool:
    from workspace.tool_repository import ToolRepository
    repo = ToolRepository()
    tools = repo.search("keyword")
"""

import importlib
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).parent.resolve()


def discover() -> list[dict]:
    """Scopre tutti i tool Python disponibili in /tools/ (esclude __init__)."""
    tools = []
    for f in sorted(TOOLS_DIR.glob("*.py")):
        if f.name == "__init__.py":
            continue
        tools.append({
            "name": f.stem,
            "file": str(f.relative_to(TOOLS_DIR.parent)),
            "path": str(f),
        })
    return tools


def load(name: str):
    """
    Carica un tool per nome (senza .py).
    
    Esempio:
        take_screenshot = load("take_screenshot")
        take_screenshot.main()
    """
    module_path = TOOLS_DIR / f"{name}.py"
    if not module_path.exists():
        raise ImportError(f"Tool '{name}' non trovato in tools/. Tool disponibili: {discover()}")
    
    spec = importlib.util.spec_from_file_location(name, str(module_path))
    if spec is None or spec.loader is None:
        raise ImportError(f"Impossibile caricare il tool '{name}'")
    
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module