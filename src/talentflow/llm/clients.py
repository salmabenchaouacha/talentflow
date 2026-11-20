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
        schema_hint = json.dumps(schema.model_json_schema(), ensure_ascii=False)
        sys_prompt = (
            f"{system}\n\nRéponds UNIQUEMENT avec un objet JSON valide conforme à ce "
            f"schéma JSON :\n{schema_hint}"
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
        backoff = 1.5
        for attempt in range(4):
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
                if exc.code in {429, 500, 502, 503, 504} and attempt < 3:
                    time.sleep(backoff ** (attempt + 1))
                    continue
                if exc.code in {401, 403}:
                    self.disabled_reason = (
                        f"Accès refusé par le fournisseur LLM (HTTP {exc.code} : {body[:120]}). "
                        "Vérifiez TF_LLM_API_KEY / TF_LLM_MODEL ; repli sur le mode hors-ligne."
                    )
                    log.error(self.disabled_reason)
                raise LLMError(f"HTTP {exc.code} sur {url} : {body}") from exc
            except urllib.error.URLError as exc:
                if attempt < 3:
                    time.sleep(backoff ** (attempt + 1))
                    continue
                raise LLMError(f"Connexion impossible à {url} : {exc}") from exc
        raise LLMError("Nombre maximal de tentatives atteint")


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
