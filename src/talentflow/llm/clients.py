"""Clients LLM sans dépendance lourde (urllib) avec sortie JSON validée par Pydantic.

- `OpenAICompatibleClient` : OpenAI, Groq, Mistral, Ollama (/v1), serveur llama.cpp —
  tous exposent /chat/completions. Le serveur llama.cpp permet de garder les CV
  sur l'infrastructure de l'entreprise (aucune donnée ne sort).
- `GeminiClient` : API Google Generative Language.

`complete_json` : demande du JSON, l'extrait, le valide contre le schéma Pydantic et,
en cas d'erreur de validation, renvoie l'erreur au modèle pour qu'il corrige (1 à N fois).
"""

from __future__ import annotations

import json
import logging
import re
import time
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TypeVar

from pydantic import BaseModel, ValidationError

log = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)

DEFAULT_BASE_URLS = {
    "openai": "https://api.openai.com/v1",
    "groq": "https://api.groq.com/openai/v1",
    "mistral": "https://api.mistral.ai/v1",
    "ollama": "http://localhost:11434/v1",
    "llamacpp": "http://localhost:8080/v1",
}


class LLMError(RuntimeError):
    pass


# Certains fournisseurs (Groq via Cloudflare) rejettent l'agent par défaut « Python-urllib »
# avec un HTTP 403 « error code: 1010 » : on s'identifie explicitement.
USER_AGENT = "TalentFlow/1.0 (+https://github.com/salmabenchaouacha/talentflow)"


@dataclass
class LLMUsage:
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_s: list[float] = field(default_factory=list)
    validation_retries: int = 0


def json_skeleton(schema: dict, defs: dict | None = None) -> object:
    """Transforme un schéma JSON Pydantic en gabarit lisible :
    {"score": "integer 0-100", "skills": [{"name": "string", ...}], ...}."""
    defs = defs if defs is not None else schema.get("$defs", {})
    if "$ref" in schema:
        return json_skeleton(defs[schema["$ref"].split("/")[-1]], defs)
    for key in ("anyOf", "oneOf"):
        if key in schema:
            options = [o for o in schema[key] if o.get("type") != "null"]
            out = json_skeleton(options[0], defs) if options else "null"
            return f"{out} ou null" if isinstance(out, str) else out
    if "enum" in schema:
        return " | ".join(str(v) for v in schema["enum"])
    if "const" in schema:
        return schema["const"]
    t = schema.get("type")
    if t == "object" or "properties" in schema:
        return {k: json_skeleton(v, defs) for k, v in schema.get("properties", {}).items()}
    if t == "array":
        return [json_skeleton(schema.get("items", {}), defs)]
    label = {"string": "texte", "integer": "entier", "number": "nombre",
             "boolean": "true/false"}.get(t, "valeur")
    if schema.get("format") == "date":
        label = "AAAA-MM-JJ"
    lo, hi = schema.get("minimum"), schema.get("maximum")
    if lo is not None and hi is not None:
        label += f" {lo}-{hi}"
    return label


def extract_json(text: str) -> dict:
    """Récupère le premier objet JSON d'une réponse (tolère ```json … ``` et du texte autour)."""
    text = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if fenced:
        text = fenced.group(1)
    start = text.find("{")
    if start == -1:
        raise LLMError("Aucun objet JSON dans la réponse du modèle")
    depth, in_str, esc = 0, False, False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(text[start : i + 1])
    raise LLMError("Objet JSON incomplet dans la réponse du modèle")


class LLMClient(ABC):
    def __init__(self, model: str, temperature: float = 0.0, timeout_s: int = 60,
                 max_retries: int = 2) -> None:
        self.model = model
        self.temperature = temperature
        self.timeout_s = timeout_s
        self.max_retries = max_retries
        self.usage = LLMUsage()
        self.disabled_reason: str | None = None  # coupe-circuit après une erreur d'accès

    @abstractmethod
    def _chat(self, system: str, user: str, json_mode: bool) -> str: ...

    def complete_text(self, system: str, user: str) -> str:
        return self._timed(system, user, json_mode=False)

    def complete_json(self, system: str, user: str, schema: type[T]) -> T:
        # Gabarit compact plutôt que le schéma JSON complet : moins de tokens, et le modèle
        # ne risque plus de recopier le schéma au lieu de répondre.
        skeleton = json.dumps(json_skeleton(schema.model_json_schema()), ensure_ascii=False)
        sys_prompt = (
            f"{system}\n\nRéponds UNIQUEMENT avec un objet JSON (sans texte autour) qui suit "
            f"ce gabarit, en remplaçant chaque type par la vraie valeur :\n{skeleton}"
        )
        prompt = user
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            raw = self._timed(sys_prompt, prompt, json_mode=True)
            try:
                return schema.model_validate(extract_json(raw))
            except (ValidationError, LLMError, json.JSONDecodeError) as exc:
                last_error = exc
                self.usage.validation_retries += 1
                log.warning("Sortie LLM invalide (tentative %s) : %s", attempt + 1, exc)
                prompt = (
                    f"{user}\n\nTa réponse précédente était invalide :\n{str(exc)[:1500]}\n"
                    "Corrige-la et renvoie uniquement le JSON."
                )
        raise LLMError(f"Sortie LLM invalide après {self.max_retries + 1} essais : {last_error}")

    def _timed(self, system: str, user: str, json_mode: bool) -> str:
        if self.disabled_reason:  # inutile de rappeler le fournisseur 90 fois
            raise LLMError(self.disabled_reason)
        t0 = time.perf_counter()
        out = self._chat(system, user, json_mode)
        self.usage.calls += 1
        self.usage.latency_s.append(time.perf_counter() - t0)
        return out

    # ------------------------------------------------------------- HTTP
    def _post(self, url: str, payload: dict, headers: dict[str, str]) -> dict:
        data = json.dumps(payload).encode("utf-8")
        attempts = 6
        for attempt in range(attempts):
            req = urllib.request.Request(
                url, data=data,
                headers={"Content-Type": "application/json", "Accept": "application/json",
                         "User-Agent": USER_AGENT, **headers},
            )
            try:
                with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                body = exc.read().decode("utf-8", errors="replace")[:500]
                if exc.code in {429, 500, 502, 503, 504} and attempt < attempts - 1:
                    wait = retry_delay(exc.headers, body, attempt)
                    log.warning("HTTP %s (limite de débit ou indisponibilité) : nouvel essai "
                                "dans %.1f s", exc.code, wait)
                    time.sleep(wait)
                    continue
                if exc.code in {401, 403}:
                    self.disabled_reason = (
                        f"Accès refusé par le fournisseur LLM (HTTP {exc.code} : {body[:120]}). "
                        "Vérifiez TF_LLM_API_KEY / TF_LLM_MODEL ; repli sur le mode hors-ligne."
                    )
                    log.error(self.disabled_reason)
                if exc.code in {400, 404} and "model" in body.lower():
                    self.disabled_reason = f"Modèle introuvable ou invalide : {body[:200]}"
                    log.error(self.disabled_reason)
                raise LLMError(f"HTTP {exc.code} sur {url} : {body}") from exc
            except urllib.error.URLError as exc:
                if attempt < attempts - 1:
                    time.sleep(2 ** attempt)
                    continue
                raise LLMError(f"Connexion impossible à {url} : {exc}") from exc
        raise LLMError("Nombre maximal de tentatives atteint")


def retry_delay(headers, body: str, attempt: int, cap: float = 60.0) -> float:
    """Délai d'attente : en-tête Retry-After, sinon « try again in 11.1s » (Groq, OpenAI),
    sinon backoff exponentiel."""
    value = headers.get("retry-after") if headers else None
    if value:
        try:
            return min(cap, float(value) + 0.5)
        except ValueError:
            pass
    m = re.search(r"try again in (?:(\d+)m)?([\d.]+)(ms|s)", body)
    if m:
        minutes = int(m.group(1) or 0)
        secs = float(m.group(2)) / (1000 if m.group(3) == "ms" else 1)
        return min(cap, minutes * 60 + secs + 0.5)
    return min(cap, 2.0 * (2 ** attempt))


class OpenAICompatibleClient(LLMClient):
    def __init__(self, base_url: str, api_key: str = "", **kw) -> None:
        super().__init__(**kw)
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key

    def _chat(self, system: str, user: str, json_mode: bool) -> str:
        payload: dict = {
            "model": self.model,
            "temperature": self.temperature,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        resp = self._post(f"{self.base_url}/chat/completions", payload, headers)
        usage = resp.get("usage") or {}
        self.usage.prompt_tokens += usage.get("prompt_tokens", 0)
        self.usage.completion_tokens += usage.get("completion_tokens", 0)
        try:
            return resp["choices"][0]["message"]["content"]
        except (KeyError, IndexError) as exc:
            raise LLMError(f"Réponse inattendue : {str(resp)[:300]}") from exc


class GeminiClient(LLMClient):
    BASE = "https://generativelanguage.googleapis.com/v1beta/models"

    def __init__(self, api_key: str, **kw) -> None:
        super().__init__(**kw)
        self.api_key = api_key

    def _chat(self, system: str, user: str, json_mode: bool) -> str:
        payload: dict = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {"temperature": self.temperature},
        }
        if json_mode:
            payload["generationConfig"]["responseMimeType"] = "application/json"
        url = f"{self.BASE}/{self.model}:generateContent"
        resp = self._post(url, payload, {"x-goog-api-key": self.api_key})
        meta = resp.get("usageMetadata") or {}
        self.usage.prompt_tokens += meta.get("promptTokenCount", 0)
        self.usage.completion_tokens += meta.get("candidatesTokenCount", 0)
        try:
            return resp["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError) as exc:
            raise LLMError(f"Réponse inattendue : {str(resp)[:300]}") from exc


def build_llm(settings) -> LLMClient | None:
    """Instancie le client selon TF_LLM_PROVIDER ; None en mode hors-ligne."""
    provider = settings.llm_provider
    if provider == "offline":
        return None
    common = dict(
        model=settings.llm_model,
        temperature=settings.llm_temperature,
        timeout_s=settings.llm_timeout_s,
        max_retries=settings.llm_max_retries,
    )
    if provider == "gemini":
        return GeminiClient(api_key=settings.llm_api_key, **common)
    if provider in DEFAULT_BASE_URLS:
        base = settings.llm_base_url or DEFAULT_BASE_URLS[provider]
        return OpenAICompatibleClient(base_url=base, api_key=settings.llm_api_key, **common)
    raise ValueError(f"Fournisseur LLM inconnu : {provider}")
