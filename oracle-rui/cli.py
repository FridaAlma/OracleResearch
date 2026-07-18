import os
import sys
import atexit
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from coding_agent import (
    coding_agent, coding_agent_pro,
    _detect_prompt_tier, _select_model,
    immunity_check_message, immunity_sanitize,
)

HISTORY_FILE = Path(__file__).parent / ".cli_history"

HAS_READLINE = False
try:
    import readline

    readline.set_history_length(100)
    try:
        readline.read_history_file(str(HISTORY_FILE))
    except FileNotFoundError:
        pass
    HAS_READLINE = True
except Exception:
    pass

# Bracketed paste mode markers (supported by Windows Terminal, ConEmu, etc.)
BRACKETED_PASTE_START = "\x1b[200~"
BRACKETED_PASTE_END = "\x1b[201~"

# ── Model tier ────────────────────────────────────────────────────
_model_tier = "auto"  # default, can be overridden by --deep / --pro


def _resolve_agent(message: str = ""):
    """Returns the right agent based on tier and message."""
    if _model_tier == "pro":
        return coding_agent_pro
    if _model_tier == "flash":
        return coding_agent
    # auto
    prompt_tier = _detect_prompt_tier(message)
    if prompt_tier == "pro":
        provider = os.getenv("MODEL_PROVIDER", "openai")
        print(f"  [Oracle] Complex task -> Pro model + Full prompt ({provider})")
        return coding_agent_pro
    if prompt_tier == "lite":
        print(f"  [Oracle] Simple task -> Flash model + Lite prompt ({prompt_tier})")
        return coding_agent
    print(f"  [Oracle] Task standard -> Flash model + Standard prompt")
    return coding_agent


def _enable_bracketed_paste():
    sys.stdout.write("\x1b[?2004h")
    sys.stdout.flush()


def _disable_bracketed_paste():
    sys.stdout.write("\x1b[?2004l")
    sys.stdout.flush()


def setup_bracketed_paste():
    _enable_bracketed_paste()
    atexit.register(_disable_bracketed_paste)


def read_multiline_input() -> str:
    """Read user input supporting multi-line paste.

    Behaviour:
      • Single-line:   type normally and press Enter.
      • Multi‑line:    paste text — bracketed paste mode (modern terminals)
                       detects the start/end automatically, preserving blank lines.
      • Ctrl+C / Ctrl+D cancel or end input.

    Old behaviour (empty line = end) is kept when NOT in a bracketed paste,
    so typing line by line still works as before.
    """
    lines: list[str] = []
    in_paste = False

    try:
        while True:
            prompt = "... " if (lines or in_paste) else ">>> "
            try:
                line = input(prompt)
            except EOFError:
                break

            if in_paste:
                if BRACKETED_PASTE_END in line:
                    line = line.replace(BRACKETED_PASTE_END, "")
                    if line:
                        lines.append(line)
                    break
                lines.append(line)
                continue

            # Not in paste mode – detect start marker
            if BRACKETED_PASTE_START in line:
                line = line.replace(BRACKETED_PASTE_START, "")
                if line:
                    lines.append(line)
                in_paste = True
                continue

            # Normal (non-paste) input: empty line = end
            if not line:
                if not lines:
                    continue
                break

            lines.append(line)
    except KeyboardInterrupt:
        print()
        if not lines:
            raise

    return "\n".join(lines)


def interactive():
    setup_bracketed_paste()
    provider = os.getenv("MODEL_PROVIDER", "openai").capitalize()
    tier_label = "PRO" if _model_tier == "pro" else "FLASH" if _model_tier == "flash" else "AUTO"
    print(f"Oracle CLI - Ctrl+C or 'exit' to quit  |  Provider: {provider} | Model: {tier_label}")
    if _model_tier == "auto":
        print(f"  (auto: complex tasks -> Pro model ({provider}), simple -> Flash)")
    print("(Paste multi-line text with Ctrl+V - auto-detected)\n")

    while True:
        try:
            prompt = read_multiline_input()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not prompt.strip():
            continue
        if prompt.strip().lower() in ("exit", "quit"):
            break

        if HAS_READLINE:
            flat = prompt.replace("\n", "  ")
            readline.add_history(flat)
            readline.write_history_file(str(HISTORY_FILE))

        # → IMMUNITY CHECK
        block_msg = immunity_check_message(prompt, source="USER")
        if block_msg:
            print(f"\n  \U0001f6e1\ufe0f [IMMUNITY] {block_msg}\n")
            continue

        agent = _resolve_agent(prompt)
        agent.print_response(prompt, stream=True)
        print()


def one_shot(prompt: str):
    # → IMMUNITY CHECK
    block_msg = immunity_check_message(prompt, source="USER")
    if block_msg:
        print(f"\n  \U0001f6e1\ufe0f [IMMUNITY] {block_msg}\n")
        return

    agent = _resolve_agent(prompt)
    agent.print_response(prompt, stream=True)
    print()


if __name__ == "__main__":
    # Parsing args
    args = [a for a in sys.argv[1:] if not a.startswith("--deep") and not a.startswith("--pro") and not a.startswith("--flash")]
    if "--deep" in sys.argv or "--pro" in sys.argv:
        _model_tier = "pro"
        provider = os.getenv("MODEL_PROVIDER", "openai").capitalize()
        print(f"[Oracle] PRO mode forced - Pro model ({provider}) for all tasks")
    elif "--flash" in sys.argv:
        _model_tier = "flash"
        provider = os.getenv("MODEL_PROVIDER", "openai").capitalize()
        print(f"[Oracle] FLASH mode forced - Flash model ({provider}) for all tasks")

    if args:
        one_shot(" ".join(args))
    else:
        interactive()
