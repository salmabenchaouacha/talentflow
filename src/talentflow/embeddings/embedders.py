"""Embeddings des profils et des besoins.

- `HashingEmbedder` : hors-ligne (scikit-learn), mots + n-grammes de caractères, après
  normalisation des compétences. Aucune donnée ne sort de la machine.
- `OpenAICompatibleEmbedder` : OpenAI, Mistral, Ollama (nomic-embed-text, bge-m3…).
- `SentenceTransformerEmbedder` : modèle local (ex. intfloat/multilingual-e5-small).

Tous renvoient des vecteurs normalisés L2 (produit scalaire = similarité cosinus).
"""

from __future__ import annotations

import json
import re
import urllib.request
from abc import ABC, abstractmethod

import numpy as np

from talentflow.extraction.skills import detect_skills


class Embedder(ABC):
    dim: int

    @abstractmethod
    def embed(self, texts: list[str]) -> np.ndarray: ...

    def embed_one(self, text: str) -> np.ndarray:
        return self.embed([text])[0]

    @staticmethod
    def _l2(m: np.ndarray) -> np.ndarray:
        norms = np.linalg.norm(m, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return (m / norms).astype(np.float32)


class HashingEmbedder(Embedder):
    def __init__(self, dim: int = 512) -> None:
        from sklearn.feature_extraction.text import HashingVectorizer

        self.dim = dim
        self._words = HashingVectorizer(
            n_features=dim, analyzer="word", ngram_range=(1, 2), alternate_sign=False,
            norm=None, token_pattern=r"(?u)\b[\w+#./-]{2,}\b",
        )
        self._chars = HashingVectorizer(
            n_features=dim, analyzer="char_wb", ngram_range=(3, 4), alternate_sign=False,
            norm=None,
        )

    @staticmethod
    def _prep(text: str) -> str:
        # On répète les compétences canoniques pour leur donner plus de poids
        skills = detect_skills(text)
        canon = " ".join(f"SKILL_{re.sub(r'[^A-Za-z0-9]+', '_', s)}" for s in skills)
        return f"{text.lower()} {canon} {canon}"

    def embed(self, texts: list[str]) -> np.ndarray:
        prepped = [self._prep(t) for t in texts]
        w = self._words.transform(prepped).toarray()
        c = self._chars.transform(prepped).toarray()
        w = self._l2(w)
        c = self._l2(c)
        return self._l2(0.7 * w + 0.3 * c)


class OpenAICompatibleEmbedder(Embedder):  # pragma: no cover - réseau
    def __init__(self, base_url: str, model: str, api_key: str = "", dim: int = 1536) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.dim = dim

    def embed(self, texts: list[str]) -> np.ndarray:
        out: list[list[float]] = []
        for i in range(0, len(texts), 64):
            payload = json.dumps({"model": self.model, "input": texts[i : i + 64]}).encode()
            headers = {"Content-Type": "application/json"}
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"
            req = urllib.request.Request(f"{self.base_url}/embeddings", payload, headers)
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.loads(resp.read())
            out.extend(d["embedding"] for d in sorted(data["data"], key=lambda d: d["index"]))
        m = np.array(out, dtype=np.float32)
        if m.shape[1] != self.dim:
            raise ValueError(f"Dimension {m.shape[1]} ≠ TF_EMBEDDING_DIM={self.dim}")
        return self._l2(m)


class SentenceTransformerEmbedder(Embedder):  # pragma: no cover - dépendance optionnelle
    def __init__(self, model: str = "intfloat/multilingual-e5-small") -> None:
        from sentence_transformers import SentenceTransformer

        self.model = SentenceTransformer(model)
        self.dim = self.model.get_sentence_embedding_dimension()
        self._e5 = "e5" in model

    def embed(self, texts: list[str]) -> np.ndarray:
        if self._e5:
            texts = [f"query: {t}" for t in texts]
        return self._l2(np.asarray(self.model.encode(texts, normalize_embeddings=True)))


def build_embedder(settings) -> Embedder:
    p = settings.embedding_provider
    if p == "hashing":
        return HashingEmbedder(dim=settings.embedding_dim)
    if p in {"openai", "ollama", "mistral"}:
        default = {
            "openai": "https://api.openai.com/v1",
            "ollama": "http://localhost:11434/v1",
            "mistral": "https://api.mistral.ai/v1",
        }[p]
        return OpenAICompatibleEmbedder(
            base_url=settings.embedding_base_url or default,
            model=settings.embedding_model,
            api_key=settings.embedding_api_key,
            dim=settings.embedding_dim,
        )
    if p == "sentence-transformers":
        return SentenceTransformerEmbedder(settings.embedding_model)
    raise ValueError(f"Fournisseur d'embeddings inconnu : {p}")
