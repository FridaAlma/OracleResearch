"""
Oracle - Model Factory
======================
Dynamic model instantiation for agno models.

Reads configuration from .env:
    MODEL_PROVIDER  – provider name (openai, deepseek, openrouter, ...)
    MODEL_ID        – model id for the flash/default tier
    MODEL_PRO_ID    – model id for the pro tier
    API_KEY         – API key (if required)
    API_BASE_URL    – base URL (for OpenAI-compatible providers)
    MAX_TOKENS      – max tokens limit
    REQUEST_TIMEOUT – timeout in seconds

Usage:
    from model_factory import create_model, get_provider_info

    provider = os.getenv("MODEL_PROVIDER", "openai")
    model = create_model(provider=provider, is_pro=False)
    model_pro = create_model(provider=provider, is_pro=True)
    info = get_provider_info(provider)
"""

import importlib
import logging
import os
import time
from typing import Any, Dict, Optional

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("oracle.model_factory")

# ── Provider Registry ───────────────────────────────────────────
# Maps provider names (as used in MODEL_PROVIDER) to their agno
# model class and display metadata.
#
# Keys are lower-case.  The special key "__openai_like__" is used
# as fallback when a provider does not have a native agno class.
#
# To add a new provider simply add an entry here.  The import is
# lazy (done inside get_provider_info / create_model) so missing
# optional dependencies won't break the rest of the application.
PROVIDER_REGISTRY: Dict[str, Dict[str, Any]] = {
    # ── Native providers (first-class agno support) ──────────────
    "openai": {
        "module": "agno.models.openai",
        "class": "OpenAIChat",
        "display_name": "OpenAI",
        "native": True,
        "default_model": "gpt-4o-mini",
        "default_pro_model": "gpt-4o",
    },
    "deepseek": {
        "module": "agno.models.deepseek",
        "class": "DeepSeek",
        "display_name": "DeepSeek",
        "native": True,
        "default_model": "deepseek-chat",
        "default_pro_model": "deepseek-reasoner",
    },
    "openrouter": {
        "module": "agno.models.openrouter",
        "class": "OpenRouter",
        "display_name": "OpenRouter",
        "native": True,
        "default_model": "openai/gpt-4o-mini",
        "default_pro_model": "openai/gpt-4o",
    },
    "google": {
        "module": "agno.models.google",
        "class": "Gemini",
        "display_name": "Google Gemini",
        "native": True,
        "default_model": "gemini-2.0-flash",
        "default_pro_model": "gemini-2.0-pro",
    },
    "ollama": {
        "module": "agno.models.ollama",
        "class": "Ollama",
        "display_name": "Ollama (local)",
        "native": True,
        "default_model": "llama3.2",
        "default_pro_model": "llama3.1",
    },
    "together": {
        "module": "agno.models.together",
        "class": "Together",
        "display_name": "Together AI",
        "native": True,
        "default_model": "mistralai/Mixtral-8x7B-Instruct-v0.1",
        "default_pro_model": "mistralai/Mixtral-8x22B-Instruct-v0.1",
    },
    "fireworks": {
        "module": "agno.models.fireworks",
        "class": "Fireworks",
        "display_name": "Fireworks AI",
        "native": True,
        "default_model": "accounts/fireworks/models/llama-v3p2-3b-instruct",
        "default_pro_model": "accounts/fireworks/models/llama-v3p1-405b-instruct",
    },
    "perplexity": {
        "module": "agno.models.perplexity",
        "class": "Perplexity",
        "display_name": "Perplexity",
        "native": True,
        "default_model": "sonar-pro",
        "default_pro_model": "sonar-pro",
    },
    "nebius": {
        "module": "agno.models.nebius",
        "class": "Nebius",
        "display_name": "Nebius AI",
        "native": True,
        "default_model": "meta-llama/Meta-Llama-3.1-8B-Instruct",
        "default_pro_model": "meta-llama/Meta-Llama-3.1-405B-Instruct",
    },
    "sambanova": {
        "module": "agno.models.sambanova",
        "class": "Sambanova",
        "display_name": "SambaNova",
        "native": True,
        "default_model": "Meta-Llama-3.1-8B-Instruct",
        "default_pro_model": "Meta-Llama-3.1-405B-Instruct",
    },
    "vllm": {
        "module": "agno.models.vllm",
        "class": "VLLM",
        "display_name": "vLLM",
        "native": True,
        "default_model": "meta-llama/Meta-Llama-3-8B-Instruct",
        "default_pro_model": "meta-llama/Meta-Llama-3-70B-Instruct",
    },
    # ── OpenAI-compatible (use OpenAILike as fallback) ───────────
    # These providers work via the OpenAI-compatible API and don't
    # have a dedicated agno model class.
    "groq": {
        "module": None,  # → fallback to OpenAILike
        "display_name": "Groq",
        "native": False,
        "default_model": "llama3-8b-8192",
        "default_pro_model": "llama3-70b-8192",
    },
    "mistral": {
        "module": None,
        "display_name": "Mistral AI",
        "native": False,
        "default_model": "mistral-small-latest",
        "default_pro_model": "mistral-large-latest",
    },
    "cohere": {
        "module": None,
        "display_name": "Cohere",
        "native": False,
        "default_model": "command-r",
        "default_pro_model": "command-r-plus",
    },
    "xai": {
        "module": None,
        "display_name": "xAI",
        "native": False,
        "default_model": "grok-beta",
        "default_pro_model": "grok-beta",
    },
    "cerebras": {
        "module": None,
        "display_name": "Cerebras",
        "native": False,
        "default_model": "llama3.1-8b",
        "default_pro_model": "llama3.1-70b",
    },
    "github": {
        "module": None,
        "display_name": "GitHub Models",
        "native": False,
        "default_model": "gpt-4o-mini",
        "default_pro_model": "gpt-4o",
    },
    "azure": {
        "module": None,
        "display_name": "Azure OpenAI",
        "native": False,
        "default_model": "gpt-4o-mini",
        "default_pro_model": "gpt-4o",
    },
}


# ── Lazy-load helpers ───────────────────────────────────────────

def _resolve_entry(provider: str) -> Dict[str, Any]:
    """Return the registry entry for *provider*, or build a generic
    OpenAI-compatible entry if the provider is unknown."""
    key = provider.lower().strip()
    entry = PROVIDER_REGISTRY.get(key)
    if entry is not None:
        return entry
    # Unknown provider → treat as OpenAI-compatible
    logger.info("Unknown provider '%s' – treating as OpenAI-compatible", provider)
    return {
        "module": None,
        "display_name": provider.title(),
        "native": False,
        "default_model": "gpt-4o-mini",
        "default_pro_model": "gpt-4o",
    }


def _import_model_class(entry: Dict[str, Any]) -> type:
    """Dynamically import the agno model class for the given registry entry.

    Falls back to ``agno.models.openai.OpenAILike`` when the entry has
    ``module=None`` or the native import fails.
    """
    from agno.models.openai import OpenAILike

    mod_path = entry.get("module")
    if mod_path is None:
        return OpenAILike

    try:
        mod = importlib.import_module(mod_path)
        cls_name = entry["class"]
        cls = getattr(mod, cls_name)
        logger.debug("Loaded model class %s from %s", cls_name, mod_path)
        return cls
    except Exception as exc:
        logger.warning(
            "Cannot load %s from %s (%s). Falling back to OpenAILike.",
            entry.get("class", "?"),
            mod_path,
            exc,
        )
        return OpenAILike


# ── Public API ──────────────────────────────────────────────────

def get_provider_info(provider: str) -> Dict[str, Any]:
    """Return metadata about *provider*.

    Returns at least ``display_name`` and may include ``native`` (bool).
    """
    entry = _resolve_entry(provider)
    return {
        "display_name": entry["display_name"],
        "native": entry.get("native", False),
    }


def create_model(
    provider: str,
    is_pro: bool = False,
    **overrides: Any,
):
    """Create and return an agno model instance for the given *provider*.

    Parameters
    ----------
    provider : str
        Provider name (e.g. ``'openai'``, ``'deepseek'``, …).  Used to
        look up the correct model class in PROVIDER_REGISTRY.
    is_pro : bool
        If ``True`` the model id is taken from ``MODEL_PRO_ID`` (env),
        otherwise from ``MODEL_ID``.
    **overrides
        Any additional keyword arguments forwarded to the model constructor
        (these take precedence over environment variables).

    Returns
    -------
    An agno ``Model`` instance (e.g. ``OpenAIChat``, ``DeepSeek``, …).
    """
    entry = _resolve_entry(provider)
    model_cls = _import_model_class(entry)

    # ── Read configuration from environment ──────────────────────
    model_id_key = "MODEL_PRO_ID" if is_pro else "MODEL_ID"
    model_id = os.getenv(model_id_key) or entry.get(
        "default_pro_model" if is_pro else "default_model", "gpt-4o-mini"
    )
    api_key: Optional[str] = os.getenv("API_KEY")
    base_url: Optional[str] = os.getenv("API_BASE_URL") or None

    try:
        max_tokens = int(os.getenv("MAX_TOKENS", "16384"))
    except (ValueError, TypeError):
        max_tokens = 16384

    try:
        timeout = int(os.getenv("REQUEST_TIMEOUT", "300"))
    except (ValueError, TypeError):
        timeout = 300

    # ── Build constructor kwargs ─────────────────────────────────
    kwargs: Dict[str, Any] = {
        "id": model_id,
        "max_tokens": max_tokens,
        "timeout": timeout,
    }

    # Only pass api_key / base_url if they are actually set.
    if api_key:
        kwargs["api_key"] = api_key
    if base_url:
        kwargs["base_url"] = base_url

    # ── Role map compatibility ───────────────────────────────────
    # The latest OpenAI API maps 'system' → 'developer' but many
    # OpenAI-compatible providers (DeepSeek, Groq, Together, …) do
    # NOT support the 'developer' role yet.
    # We default to the traditional 'system' role in two cases:
    #   1. The provider is not explicitly OpenAI.
    #   2. A custom API_BASE_URL is set (likely an OpenAI-compatible
    #      proxy / alternative provider).
    _is_vanilla_openai = (
        provider.lower().strip() == "openai"
        and not base_url
    )
    if not _is_vanilla_openai:
        if "role_map" not in overrides:
            kwargs["role_map"] = {
                "system": "system",
                "user": "user",
                "assistant": "assistant",
                "tool": "tool",
            }

    # Apply user overrides (highest priority)
    kwargs.update(overrides)

    # ── Instantiate ──────────────────────────────────────────────
    try:
        model = model_cls(**kwargs)
        logger.info(
            "Created %s model: provider=%s id=%s base_url=%s",
            "PRO" if is_pro else "FLASH",
            provider,
            model_id,
            base_url or "(default)",
        )
        # ── Instrument with LLM call logging ─────────────────────
        _wrap_model_for_logging(model, model_id, provider)
        return model
    except Exception as exc:
        logger.error(
            "Failed to instantiate model %s (provider=%s, id=%s): %s",
            model_cls.__name__,
            provider,
            model_id,
            exc,
        )
        raise


# ── LLM Call Logging Wrapper ────────────────────────────────────

def _wrap_model_for_logging(model, model_id: str, provider: str):
    """Wrap the agno model's response/aresponse/response_stream/aresponse_stream
    to log every LLM call (both sync/async and streaming/non-streaming).

    The caller tag is read from the thread-local set by the endpoint.
    """
    try:
        from llm_logger import get_caller_tag, log_llm_call
    except ImportError:
        return  # llm_logger not available — no instrumentation

    def _extract_usage(obj):
        """Extract token counts from an agno ModelResponse / event object."""
        if obj is None:
            return 0, 0
        if hasattr(obj, "input_tokens"):
            inp = obj.input_tokens or 0
            out = obj.output_tokens or 0
            if inp > 0 or out > 0:
                return inp, out
        if hasattr(obj, "response_usage") and obj.response_usage:
            usage = obj.response_usage
            inp = getattr(usage, "input_tokens", 0) or getattr(usage, "prompt_tokens", 0) or 0
            out = getattr(usage, "output_tokens", 0) or getattr(usage, "completion_tokens", 0) or 0
            if inp > 0 or out > 0:
                return inp, out
        return 0, 0

    def _make_log_meta(method, status="ok"):
        m = {"provider": provider, "method": method}
        if status != "ok":
            m["status"] = status
        return m

    # ── IMPORTANT: each closure captures a DIFFERENT variable name
    # so that reassignment in later blocks does NOT affect earlier closures.

    # ────────────────────────────────────────────────────────────
    # 1) Sync non-streaming: response()
    # ────────────────────────────────────────────────────────────
    if hasattr(model, "response") and callable(model.response):
        _orig_resp = model.response

        def _logged_response(*args, **kwargs):
            caller_tag = get_caller_tag()
            t0 = time.time()
            try:
                result = _orig_resp(*args, **kwargs)
                duration = time.time() - t0
                inp, out = _extract_usage(result)
                log_llm_call(caller_tag, duration, inp, out, model_id,
                             metadata=_make_log_meta("response"))
                return result
            except Exception:
                duration = time.time() - t0
                log_llm_call(caller_tag, duration, 0, 0, model_id,
                             metadata=_make_log_meta("response", "error"))
                raise

        model.response = _logged_response

    # ────────────────────────────────────────────────────────────
    # 2) Async non-streaming: aresponse()
    # ────────────────────────────────────────────────────────────
    if hasattr(model, "aresponse") and callable(model.aresponse):
        _orig_aresp = model.aresponse

        async def _logged_aresponse(*args, **kwargs):
            caller_tag = get_caller_tag()
            t0 = time.time()
            try:
                result = await _orig_aresp(*args, **kwargs)
                duration = time.time() - t0
                inp, out = _extract_usage(result)
                log_llm_call(caller_tag, duration, inp, out, model_id,
                             metadata=_make_log_meta("aresponse"))
                return result
            except Exception:
                duration = time.time() - t0
                log_llm_call(caller_tag, duration, 0, 0, model_id,
                             metadata=_make_log_meta("aresponse", "error"))
                raise

        model.aresponse = _logged_aresponse

    # ────────────────────────────────────────────────────────────
    # 3) Sync streaming: response_stream()
    #    Returns Iterator[ModelResponse | Event].  We wrap the
    #    generator so that when it is exhausted (or closes) we log
    #    the call with whatever token info we saw.
    # ────────────────────────────────────────────────────────────
    if hasattr(model, "response_stream") and callable(model.response_stream):
        _orig_rstream = model.response_stream

        def _logged_response_stream(*args, **kwargs):
            caller_tag = get_caller_tag()
            t0 = time.time()
            inp, out = 0, 0
            try:
                for item in _orig_rstream(*args, **kwargs):
                    i, o = _extract_usage(item)
                    if i > 0:
                        inp = i
                    if o > 0:
                        out = o
                    yield item
                duration = time.time() - t0
                log_llm_call(caller_tag, duration, inp, out, model_id,
                             metadata=_make_log_meta("response_stream"))
            except GeneratorExit:
                duration = time.time() - t0
                log_llm_call(caller_tag, duration, inp, out, model_id,
                             metadata=_make_log_meta("response_stream"))
                raise
            except Exception:
                duration = time.time() - t0
                log_llm_call(caller_tag, duration, inp, out, model_id,
                             metadata=_make_log_meta("response_stream", "error"))
                raise

        model.response_stream = _logged_response_stream

    # ────────────────────────────────────────────────────────────
    # 4) Async streaming: aresponse_stream()
    #    Returns AsyncIterator[...].  Same wrapping pattern.
    # ────────────────────────────────────────────────────────────
    if hasattr(model, "aresponse_stream") and callable(model.aresponse_stream):
        _orig_astream = model.aresponse_stream

        async def _logged_aresponse_stream(*args, **kwargs):
            caller_tag = get_caller_tag()
            t0 = time.time()
            inp, out = 0, 0
            try:
                async for item in _orig_astream(*args, **kwargs):
                    i, o = _extract_usage(item)
                    if i > 0:
                        inp = i
                    if o > 0:
                        out = o
                    yield item
                duration = time.time() - t0
                log_llm_call(caller_tag, duration, inp, out, model_id,
                             metadata=_make_log_meta("aresponse_stream"))
            except GeneratorExit:
                duration = time.time() - t0
                log_llm_call(caller_tag, duration, inp, out, model_id,
                             metadata=_make_log_meta("aresponse_stream"))
                raise
            except Exception:
                duration = time.time() - t0
                log_llm_call(caller_tag, duration, inp, out, model_id,
                             metadata=_make_log_meta("aresponse_stream", "error"))
                raise

        model.aresponse_stream = _logged_aresponse_stream


# ── Testing / debug ─────────────────────────────────────────────
if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)

    provider = os.getenv("MODEL_PROVIDER", "openai")
    info = get_provider_info(provider)
    print(f"[INFO] Provider: {info['display_name']} "
          f"({'native' if info.get('native') else 'OpenAI-compatible'})")

    model = create_model(provider=provider, is_pro=False)
    print(f"[INFO] Flash model:  {model}")

    model_pro = create_model(provider=provider, is_pro=True)
    print(f"[INFO] Pro model:    {model_pro}")
