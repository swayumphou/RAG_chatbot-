"""Optional Gemma 4 answers through Ollama (only when ENABLE_LLM=true).

The prompt contains ONLY the top retrieved projects and the question.
Contact details are never put in the prompt.

Settings that matter:
- temperature 0.0 -> the same question gives the same answer
- num_ctx 8192    -> must be set, or Ollama's small default window cuts the evidence
- num_predict 600 -> keeps the answer inside one Telegram message
"""

from __future__ import annotations

import logging
import time

import httpx

from config import NUM_CTX, NUM_PREDICT, TEMPERATURE, get_config

logger = logging.getLogger(__name__)

LLM_NO_ANSWER = "No indexed project covers this question."
TIMEOUT_SECONDS = 120.0  # a slow answer is replaced by the extractive answer

SYSTEM_PROMPT = f"""You answer questions about past RUPP senior projects using ONLY the
passages inside <retrieved_projects>.

Rules:
1. Use only the provided passages. Do not add outside knowledge.
2. Cite every factual sentence as [Project: <title>, p.<page>].
3. If the passages do not answer the question, output exactly:
   {LLM_NO_ANSWER}
4. Never invent a senior name, supervisor, year, or repository link.
5. Keep the answer under 900 characters — it is delivered as a Telegram message."""


class LLMError(RuntimeError):
    """Any LLM problem. The answer service then serves the extractive answer."""


class LLMTimeout(LLMError):
    pass


class LLMUnavailable(LLMError):
    pass


class LLMTruncated(LLMError):
    """The model hit num_predict and stopped mid-answer (done_reason == "length")."""


def build_messages(query: str, citations) -> list[dict]:
    """System prompt + one user message with the passages and the question."""
    blocks = []
    for c in citations:
        header = f"[Project: {c.title} | p.{c.page_number}]"
        blocks.append(
            f'<project title="{c.title}" page="{c.page_number}">\n{header}\n{c.snippet}\n</project>'
        )
    context = "\n\n".join(blocks)
    user = f"<retrieved_projects>\n{context}\n</retrieved_projects>\n\nQuestion: {query}"
    return [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}]


def call_ollama(payload: dict, url: str) -> dict:
    """POST to Ollama /api/chat and turn every failure into an LLMError."""
    try:
        response = httpx.post(f"{url}/api/chat", json=payload, timeout=TIMEOUT_SECONDS)
        response.raise_for_status()
        return response.json()
    except httpx.TimeoutException as error:
        raise LLMTimeout(f"Ollama timed out after {TIMEOUT_SECONDS}s") from error
    except httpx.HTTPError as error:
        raise LLMUnavailable(f"Ollama request failed: {error}") from error
    except ValueError as error:
        raise LLMUnavailable("Ollama returned invalid JSON") from error


def synthesize(query, citations) -> str:
    """Ask Gemma for an answer. Raises LLMError on timeout, connection error, or cut-off."""
    config = get_config()
    model = config.ollama_model  # pinned tag, e.g. gemma4:12b-it-qat
    payload = {
        "model": model,
        "messages": build_messages(query, citations),
        "stream": False,
        "options": {"temperature": TEMPERATURE, "num_ctx": NUM_CTX, "num_predict": NUM_PREDICT},
    }
    start = time.perf_counter()
    data = call_ollama(payload, config.ollama_url)
    latency_ms = int((time.perf_counter() - start) * 1000)
    logger.info("ollama model=%s latency_ms=%d", model, latency_ms)

    if data.get("done_reason") == "length":
        raise LLMTruncated("the answer was cut off at num_predict")
    text = (data.get("message") or {}).get("content", "").strip()
    if not text:
        raise LLMError("Ollama returned an empty answer")
    return text
