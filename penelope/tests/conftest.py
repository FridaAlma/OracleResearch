"""
Pytest configuration for Penelope tests.
Adds Oracle/ to sys.path for importing egida (4th layer).
"""

import sys
from pathlib import Path

_ORACLE_ROOT_DIR = Path(__file__).resolve().parent.parent.parent  # Oracle/
if str(_ORACLE_ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(_ORACLE_ROOT_DIR))
