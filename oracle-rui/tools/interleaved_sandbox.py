#!/usr/bin/env python3
"""
🪹 Interleaved Sandbox — Inference-time code execution + verification for Oracle.

Esegue blocchi di codice in ambienti isolati (subprocess), cattura
risultati, e li inietta nel contesto del modello per auto-correzione.

Architettura:
  TokenBuffer     → rileva blocchi di codice ``` in streaming
  CodeExtractor   → isola codice Python/Bash/SQL
  SafetyFilter    → blocca comandi pericolosi
  EphemeralExec   → esegue in subprocess con timeout + AST parse
  ResultFormatter → output strutturato per injection contesto

Modalità:
  1. analyze(text)         — post-generation: estrai + esegui blocchi
  2. analyze_stream(tokens) — streaming: rileva blocchi in tempo reale
  3. run_block(code, lang) — esecuzione diretta
  4. refinement_loop(...)  — loop: genera → esegui → correggi → ripeti

Usage CLI:
  python tools/interleaved_sandbox.py analyze "testo con \`\`\`python\\nprint(1)\\n\`\`\`"
  python tools/interleaved_sandbox.py run --code "print('hello')" --lang python
  echo "print(42)" | python tools/interleaved_sandbox.py run --lang python
  python tools/interleaved_sandbox.py extract "testo con codice" --json

Usage Python:
  from tools.interleaved_sandbox import InterleavedSandbox, SandboxConfig
  sb = InterleavedSandbox()
  results = sb.analyze_response(response_text)
  print(sb.format_results_injection(results))
"""

import argparse
import ast
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

# ── Config ─────────────────────────────────────────────────────────────────

DEFAULT_TIMEOUT = 30       # secondi per esecuzione
MAX_OUTPUT_SIZE = 4096     # caratteri per stdout/stderr
MAX_ITERATIONS = 5         # refinement loop
SUPPORTED_LANGS = {'python', 'bash', 'sh', 'sql'}

# ── Dataclasses ────────────────────────────────────────────────────────────

@dataclass
class CodeBlock:
    """Blocco di codice estratto da markdown."""
    language: str
    code: str
    start_line: int = 0
    end_line: int = 0

@dataclass
class ExecResult:
    """Risultato esecuzione blocco."""
    language: str
    code: str
    exit_code: int
    stdout: str
    stderr: str
    duration: float
    status: str            # success | error | timeout | blocked
    error_type: str = ''   # syntax | runtime | timeout | safety | unsupported
    summary: str = ''

@dataclass
class SandboxConfig:
    """Configurazione sandbox."""
    timeout: int = DEFAULT_TIMEOUT
    max_output: int = MAX_OUTPUT_SIZE
    max_iterations: int = MAX_ITERATIONS
    safety_enabled: bool = True
    working_dir: Optional[str] = None


# ── Safety patterns ────────────────────────────────────────────────────────

DANGEROUS_PATTERNS = [
    r'rm\s+(-rf?|--recursive)\s+/',
    r'rm\s+(-rf?|--recursive)\s+~',
    r'rm\s+(-rf?|--recursive)\s+/\w+\s+--no-preserve-root',
    r':\(\)\s*\{[^}]*:[^}]*\}',                      # fork bomb
    r'dd\s+if=/dev/zero\s+of=/dev/sd',
    r'dd\s+if=/dev/urandom\s+of=/dev/sd',
    r'mkfs\.\w+', r'fdisk\s+/dev/sd',
    r'format\s+[a-zA-Z]:\s*/q',
    r'chmod\s+777\s+/',
    r'(wget|curl)\s+\S+\s*\|\s*(bash|sh)',
    r'(shutdown|reboot|halt|poweroff)\s+(-[a-z]+\s+)?(now|\+0|\d+)',
    r'>\s*/dev/sd[a-z]',
    r':(){[^}]*};:',
]


# ═══════════════════════════════════════════════════════════════════════════
#  SAFETY FILTER
# ═══════════════════════════════════════════════════════════════════════════

class SafetyFilter:
    """Blocca comandi shell pericolosi via pattern matching."""

    @staticmethod
    def check(code: str, language: str) -> tuple[bool, str]:
        """(safe, reason) — safe=True se non ci sono pattern pericolosi."""
        if language not in ('bash', 'sh', 'shell'):
            return True, ''
        for pat in DANGEROUS_PATTERNS:
            m = re.search(pat, code, re.IGNORECASE | re.MULTILINE)
            if m:
                return False, f"Pattern pericoloso: {m.group(0)[:80]}"
        return True, ''


# ═══════════════════════════════════════════════════════════════════════════
#  CODE EXTRACTOR
# ═══════════════════════════════════════════════════════════════════════════

class CodeExtractor:
    """Estrae blocchi ```language ... ``` da testo markdown."""

    BLOCK_RE = re.compile(r'```(\w+)?\s*\n(.*?)```', re.DOTALL)

    @classmethod
    def extract(cls, text: str) -> list[CodeBlock]:
        """Ritorna tutti i CodeBlock trovati."""
        blocks = []
        for m in cls.BLOCK_RE.finditer(text):
            lang = m.group(1) or 'text'
            code = m.group(2).strip()
            if not code:
                continue
            start_line = text[:m.start()].count('\n')
            end_line = text[:m.end()].count('\n')
            blocks.append(CodeBlock(
                language=cls._norm(lang),
                code=code,
                start_line=start_line,
                end_line=end_line,
            ))
        return blocks

    @staticmethod
    def _norm(lang: str) -> str:
        m = {
            'py': 'python', 'python3': 'python',
            'sh': 'bash', 'shell': 'bash', 'bash': 'bash', 'zsh': 'bash',
            '': 'text',
        }
        return m.get(lang.lower(), lang.lower())


# ═══════════════════════════════════════════════════════════════════════════
#  EPHEMERAL EXECUTOR
# ═══════════════════════════════════════════════════════════════════════════

class EphemeralExecutor:
    """Esegue codice in subprocess isolato. Supporta python/bash/sql."""

    @classmethod
    def execute(cls, block: CodeBlock, config: SandboxConfig) -> ExecResult:
        if block.language not in SUPPORTED_LANGS:
            return ExecResult(
                language=block.language, code=block.code,
                exit_code=-1, stdout='', stderr=f'Linguaggio non supportato: {block.language}',
                duration=0, status='error', error_type='unsupported',
                summary=f'{block.language} non eseguibile',
            )
        t0 = time.time()
        try:
            if block.language == 'python':
                return cls._exec_python(block, config, t0)
            elif block.language in ('bash', 'sh'):
                return cls._exec_bash(block, config, t0)
            elif block.language == 'sql':
                return cls._exec_sql(block, config, t0)
        except Exception as e:
            return ExecResult(
                language=block.language, code=block.code,
                exit_code=-1, stdout='', stderr=str(e),
                duration=time.time() - t0, status='error',
                error_type='runtime', summary=str(e)[:120],
            )

    @staticmethod
    def _exec_python(block: CodeBlock, config: SandboxConfig, t0: float) -> ExecResult:
        # Pre-validate AST
        try:
            ast.parse(block.code)
        except SyntaxError as e:
            return ExecResult(
                language='python', code=block.code, exit_code=1, stdout='',
                stderr=f'SyntaxError: {e}', duration=time.time() - t0,
                status='error', error_type='syntax',
                summary=f'SyntaxError linea {e.lineno}: {e.msg}',
            )
        try:
            r = subprocess.run(
                [sys.executable, '-c', block.code],
                capture_output=True, text=True, timeout=config.timeout,
                cwd=config.working_dir or Path.cwd(),
            )
            dur = time.time() - t0
            out = r.stdout.strip()[:config.max_output]
            err = r.stderr.strip()[:config.max_output]
            ok = r.returncode == 0
            return ExecResult(
                language='python', code=block.code,
                exit_code=r.returncode, stdout=out, stderr=err,
                duration=dur,
                status='success' if ok else 'error',
                error_type='' if ok else 'runtime',
                summary=f"OK -> {out[:120]}" if ok else f"Exit {r.returncode} -> {err[:120]}",
            )
        except subprocess.TimeoutExpired:
            return ExecResult(
                language='python', code=block.code,
                exit_code=-1, stdout='', stderr=f'Timeout {config.timeout}s',
                duration=time.time() - t0, status='timeout',
                error_type='timeout', summary=f'Timeout {config.timeout}s',
            )

    @staticmethod
    def _exec_bash(block: CodeBlock, config: SandboxConfig, t0: float) -> ExecResult:
        try:
            r = subprocess.run(
                block.code, capture_output=True, text=True,
                timeout=config.timeout, shell=True,
                cwd=config.working_dir or Path.cwd(),
            )
            dur = time.time() - t0
            out = r.stdout.strip()[:config.max_output]
            err = r.stderr.strip()[:config.max_output]
            ok = r.returncode == 0
            return ExecResult(
                language='bash', code=block.code,
                exit_code=r.returncode, stdout=out, stderr=err,
                duration=dur,
                status='success' if ok else 'error',
                error_type='' if ok else 'runtime',
                summary=f"OK -> {out[:120]}" if ok else f"Exit {r.returncode} -> {err[:120]}",
            )
        except subprocess.TimeoutExpired:
            return ExecResult(
                language='bash', code=block.code,
                exit_code=-1, stdout='', stderr=f'Timeout {config.timeout}s',
                duration=time.time() - t0, status='timeout',
                error_type='timeout', summary=f'Timeout {config.timeout}s',
            )

    @staticmethod
    def _exec_sql(block: CodeBlock, config: SandboxConfig, t0: float) -> ExecResult:
        # Temp in-memory SQLite DB
        try:
            r = subprocess.run(
                ['sqlite3', ':memory:'],
                input=block.code.strip() + ';\n.quit\n',
                capture_output=True, text=True, timeout=config.timeout,
            )
            dur = time.time() - t0
            out = r.stdout.strip()[:config.max_output]
            err = r.stderr.strip()[:config.max_output]
            ok = r.returncode == 0
            return ExecResult(
                language='sql', code=block.code,
                exit_code=r.returncode, stdout=out, stderr=err,
                duration=dur,
                status='success' if ok else 'error',
                error_type='' if ok else 'runtime',
                summary=f"OK -> {out[:120]}" if ok else f"Exit {r.returncode} -> {err[:120]}",
            )
        except FileNotFoundError:
            return ExecResult(
                language='sql', code=block.code,
                exit_code=-1, stdout='', stderr='sqlite3 non trovato. Installa SQLite.',
                duration=time.time() - t0, status='error',
                error_type='dependency', summary='sqlite3 mancante',
            )
        except subprocess.TimeoutExpired:
            return ExecResult(
                language='sql', code=block.code,
                exit_code=-1, stdout='', stderr=f'Timeout {config.timeout}s',
                duration=time.time() - t0, status='timeout',
                error_type='timeout', summary=f'Timeout {config.timeout}s',
            )


# ═══════════════════════════════════════════════════════════════════════════
#  TOKEN BUFFER (streaming detection)
# ═══════════════════════════════════════════════════════════════════════════

class TokenBuffer:
    """
    Buffer a stati per rilevare blocchi di codice in streaming.
    Stati: idle → in_backtick_open → in_code → (chiusura → idle)
    """

    def __init__(self):
        self.buffer = ''
        self._in_block = False
        self._current_lang = ''
        self._current_code = ''
        self._current_start = 0
        self._line = 0

    def feed(self, token: str) -> list[CodeBlock]:
        """Inserisci token, ritorna blocchi completi rilevati."""
        self.buffer += token
        completed: list[CodeBlock] = []
        i = 0
        while i < len(token):
            c = token[i]
            if c == '\n':
                self._line += 1

            if not self._in_block:
                # cerca ``` di apertura
                if token[i:i+3] == '```':
                    self._in_block = True
                    self._current_lang = ''
                    self._current_code = ''
                    self._current_start = self._line
                    i += 2
                i += 1
            else:
                # dentro un blocco — cerca ``` di chiusura
                close = token.find('```', i)
                if close >= 0:
                    # testo prima di ```
                    self._current_code += token[i:close]
                    block = CodeBlock(
                        language=self._norm_lang(self._current_lang),
                        code=self._current_code.strip(),
                        start_line=self._current_start,
                        end_line=self._line,
                    )
                    if block.code:
                        completed.append(block)
                    self._in_block = False
                    self._current_lang = ''
                    self._current_code = ''
                    i = close + 3
                else:
                    # non ancora chiuso
                    rest = token[i:]
                    # Se è l'inizio del blocco, cerca la label del linguaggio
                    if not self._current_lang and not self._current_code:
                        nl = rest.find('\n')
                        if nl >= 0:
                            self._current_lang = rest[:nl].strip()
                            self._current_code += rest[nl+1:]
                        else:
                            self._current_lang += rest
                    else:
                        self._current_code += rest
                    i = len(token)
        return completed

    def flush_pending(self) -> Optional[CodeBlock]:
        """Ritorna blocco incompleto se ancora in_code (es. stream interrotto)."""
        if self._in_block and self._current_code.strip():
            return CodeBlock(
                language=self._norm_lang(self._current_lang),
                code=self._current_code.strip(),
                start_line=self._current_start,
                end_line=self._line,
            )
        return None

    @staticmethod
    def _norm_lang(l: str) -> str:
        m = {'py': 'python', 'sh': 'bash', 'shell': 'bash', '': 'text'}
        return m.get(l.lower().strip(), l.lower().strip() or 'text')

    def reset(self):
        self.__init__()


# ═══════════════════════════════════════════════════════════════════════════
#  RESULT FORMATTER
# ═══════════════════════════════════════════════════════════════════════════

class ResultFormatter:
    """Formatta ExecResult per injection nel contesto del modello."""

    @staticmethod
    def format(results: list[ExecResult]) -> str:
        if not results:
            return ''

        lines = [
            '## EXECUTION REPORT - Interleaved Sandbox ##',
        ]
        for i, r in enumerate(results, 1):
            icon = '[OK]' if r.status == 'success' else '[X]' if r.status in ('error', 'blocked') else '[T]'
            lines.append(f'')
            lines.append(f'  #{i} [{r.status.upper()}] {icon}  {r.language}  ({r.duration:.2f}s)')
            lines.append(f'  -> {r.summary}')
            if r.stdout and r.status == 'success':
                preview = r.stdout[:200]
                lines.append(f'  Output: {preview}')
            if r.stderr and r.status != 'success':
                preview = r.stderr[:200]
                lines.append(f'  Error:  {preview}')
        lines.append('')
        lines.append('## ########################################## ##')
        return '\n'.join(lines)

    @staticmethod
    def format_refinement_prompt(results: list[ExecResult], goal: str) -> str:
        """Crea prompt di refinement per il modello."""
        report = ResultFormatter.format(results)
        has_err = any(r.status != 'success' for r in results)
        if has_err:
            return (
                f"[TEST FAILED] I test del codice hanno prodotto ERRORI.\n"
                f"Correggi il codice in base al report sottostante.\n\n"
                f"{report}\n\n"
                f"Obiettivo: {goal}\n\n"
                f"Riscrivi SOLO il codice corretto, non l'intera risposta."
            )
        else:
            return (
                f"[TEST PASSED] I test del codice sono PASSATI.\n\n"
                f"{report}\n\n"
                f"Obiettivo: {goal}\n\n"
                f"Il codice funziona. Prosegui o finalizza."
            )


# ═══════════════════════════════════════════════════════════════════════════
#  INTERLEAVED SANDBOX (Engine principale)
# ═══════════════════════════════════════════════════════════════════════════

class InterleavedSandbox:
    """
    Engine principale.
    
    Usage:
        sb = InterleavedSandbox()
        # Post-generation
        results = sb.analyze_response(text)
        # Streaming
        results = sb.analyze_stream(token_list)
        # Diretto
        result = sb.run_block("print(1)", "python")
        # Formatta per injection
        print(sb.format_results(results))
    """

    def __init__(self, config: Optional[SandboxConfig] = None):
        self.config = config or SandboxConfig()
        self.safety = SafetyFilter()
        self.executor = EphemeralExecutor()
        self.extractor = CodeExtractor()
        self.token_buffer = TokenBuffer()
        self.history: list[ExecResult] = []
        self.formatter = ResultFormatter()

    def analyze_response(self, text: str) -> list[ExecResult]:
        """Post-generation: estrai blocchi da risposta completa ed esegui."""
        blocks = self.extractor.extract(text)
        return self._execute_blocks(blocks)

    def analyze_stream(self, tokens: list[str]) -> list[ExecResult]:
        """Streaming: alimenta token buffer, esegui blocchi completi."""
        results = []
        for tok in tokens:
            completed = self.token_buffer.feed(tok)
            results.extend(self._execute_blocks(completed))
        # Flush pending (blocco non chiuso)
        pending = self.token_buffer.flush_pending()
        if pending:
            results.extend(self._execute_blocks([pending]))
        return results

    def run_block(self, code: str, language: str = 'python') -> ExecResult:
        """Esegui un blocco di codice direttamente."""
        block = CodeBlock(language=language, code=code)
        return self._execute_block(block)

    def _execute_blocks(self, blocks: list[CodeBlock]) -> list[ExecResult]:
        results = []
        for b in blocks:
            r = self._execute_block(b)
            results.append(r)
        return results

    def _execute_block(self, block: CodeBlock) -> ExecResult:
        # Safety
        if self.config.safety_enabled:
            safe, reason = self.safety.check(block.code, block.language)
            if not safe:
                r = ExecResult(
                    language=block.language, code=block.code,
                    exit_code=-1, stdout='', stderr=f'BLOCKED: {reason}',
                    duration=0, status='blocked', error_type='safety',
                    summary=f'Bloccato: {reason[:80]}',
                )
                self.history.append(r)
                return r
        # Execute
        r = self.executor.execute(block, self.config)
        self.history.append(r)
        return r

    def format_results(self, results: list[ExecResult]) -> str:
        return self.formatter.format(results)

    def format_refinement_prompt(self, results: list[ExecResult], goal: str) -> str:
        return self.formatter.format_refinement_prompt(results, goal)

    def get_stats(self) -> dict:
        t = len(self.history)
        s = sum(1 for r in self.history if r.status == 'success')
        e = sum(1 for r in self.history if r.status == 'error')
        to = sum(1 for r in self.history if r.status == 'timeout')
        b = sum(1 for r in self.history if r.status == 'blocked')
        dur = sum(r.duration for r in self.history)
        return {
            'total': t, 'successes': s, 'errors': e,
            'timeouts': to, 'blocked': b,
            'total_duration_sec': round(dur, 2),
            'languages': list(set(r.language for r in self.history)),
        }


# ═══════════════════════════════════════════════════════════════════════════
#  CLI
# ═══════════════════════════════════════════════════════════════════════════

def _read_text(args) -> str:
    text = ' '.join(getattr(args, 'text', [])) if hasattr(args, 'text') else ''
    if not text and not sys.stdin.isatty():
        text = sys.stdin.read().strip()
    return text


def cmd_analyze(args):
    text = _read_text(args)
    if not text:
        print('ERRORE: fornisci testo come argomento o via pipe'); sys.exit(1)
    sb = InterleavedSandbox(SandboxConfig(timeout=args.timeout, safety_enabled=not args.no_safety))
    results = sb.analyze_response(text)
    if args.json:
        print(json.dumps([{
            'language': r.language, 'status': r.status,
            'exit_code': r.exit_code, 'stdout': r.stdout, 'stderr': r.stderr,
            'duration': round(r.duration, 3), 'summary': r.summary,
        } for r in results], ensure_ascii=False, indent=2))
    else:
        print(_ascii(sb.format_results(results)))
        ok = sum(1 for r in results if r.status == 'success')
        print(_ascii(f'\nStats: {len(results)} blocchi, {ok} OK, {len(results)-ok} fallimenti'))


def _ascii(s: str) -> str:
    """Rimpiazza caratteri Unicode non cp1252 con equivalenti ASCII."""
    return s.replace('\u2192', '->').replace('\u2713', '[OK]').replace('\u2717', '[X]').replace('\u23f1', '[T]').replace('\u2500', '-').replace('\u2554', '##').replace('\u2557', '##').replace('\u255a', '##').replace('\u255d', '##').replace('\u2551', '|').replace('\u2560', '##').replace('\u2563', '##').replace('\u256c', '##').replace('\u2502', '|').replace('\u251c', '|').replace('\u2524', '|').replace('\u253c', '+').replace('\u250c', '--').replace('\u2510', '--').replace('\u2514', '--').replace('\u2518', '--')


def cmd_run(args):
    code = args.code or ''
    if not code and not sys.stdin.isatty():
        code = sys.stdin.read().strip()
    if not code:
        print('ERRORE: fornisci codice con --code o via pipe'); sys.exit(1)
    sb = InterleavedSandbox(SandboxConfig(timeout=args.timeout, safety_enabled=not args.no_safety))
    r = sb.run_block(code, args.language)
    if args.json:
        print(json.dumps({
            'language': r.language, 'status': r.status,
            'exit_code': r.exit_code, 'stdout': r.stdout, 'stderr': r.stderr,
            'duration': round(r.duration, 3), 'summary': r.summary,
        }, ensure_ascii=False, indent=2))
    else:
        print(_ascii(f'[{r.status.upper()}] exit={r.exit_code} dur={r.duration:.2f}s'))
        if r.stdout: print(_ascii(f'OUT:\n{r.stdout}'))
        if r.stderr: print(_ascii(f'ERR:\n{r.stderr}'))
        print(_ascii(f'-> {r.summary}'))


def cmd_extract(args):
    text = _read_text(args)
    if not text:
        print('ERRORE: fornisci testo come argomento o via pipe'); sys.exit(1)
    blocks = CodeExtractor.extract(text)
    if args.json:
        print(json.dumps([{
            'language': b.language, 'code': b.code,
            'start_line': b.start_line, 'end_line': b.end_line,
        } for b in blocks], ensure_ascii=False, indent=2))
    else:
        for i, b in enumerate(blocks, 1):
            print(f'[{i}] {b.language} (L{b.start_line}-{b.end_line}): {b.code[:80].strip()}')


def main():
    p = argparse.ArgumentParser(
        description='🪹 Interleaved Sandbox — esecuzione/verifica codice a tempo di inferenza',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            'Esempi:\n'
            '  %(prog)s analyze "Ecco ```python\\nprint(2+2)\\n```"\n'
            '  %(prog)s run --code "print(\'hello\')" --lang python\n'
            '  echo "print(42)" | %(prog)s run --lang python\n'
            '  %(prog)s extract "testo con codice" --json\n'
        ),
    )
    sp = p.add_subparsers(dest='cmd')

    pa = sp.add_parser('analyze', help='Analizza testo, estrae ed esegue blocchi codice')
    pa.add_argument('text', nargs='*')
    pa.add_argument('--timeout', '-t', type=int, default=DEFAULT_TIMEOUT)
    pa.add_argument('--no-safety', action='store_true')
    pa.add_argument('--json', action='store_true')

    pr = sp.add_parser('run', help='Esegue codice direttamente')
    pr.add_argument('--code', '-c', default='')
    pr.add_argument('--language', '-l', default='python', choices=list(SUPPORTED_LANGS))
    pr.add_argument('--timeout', '-t', type=int, default=DEFAULT_TIMEOUT)
    pr.add_argument('--no-safety', action='store_true')
    pr.add_argument('--json', action='store_true')

    pe = sp.add_parser('extract', help='Estrae blocchi codice senza eseguirli')
    pe.add_argument('text', nargs='*')
    pe.add_argument('--json', action='store_true')

    args = p.parse_args()
    if args.cmd == 'analyze':
        cmd_analyze(args)
    elif args.cmd == 'run':
        cmd_run(args)
    elif args.cmd == 'extract':
        cmd_extract(args)
    else:
        p.print_help()


if __name__ == '__main__':
    main()