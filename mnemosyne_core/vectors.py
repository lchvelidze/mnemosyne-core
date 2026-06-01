from __future__ import annotations

import hashlib
import math
import re

VECTOR_DIMENSIONS = 128
VECTOR_VERSION = "local-hash-v1"

_TOKEN_PATTERN = re.compile(r"[a-zA-Z0-9]+")
_SYNONYMS = {
    "lfp": ["lithium", "iron", "phosphate", "battery", "batteries"],
    "nmc": ["nickel", "manganese", "cobalt", "battery", "batteries"],
    "liion": ["lithium", "ion", "battery", "batteries"],
    "lithium": ["liion"],
    "battery": ["batteries", "cell", "cells", "storage"],
    "batteries": ["battery", "cell", "cells", "storage"],
    "storage": ["battery", "batteries"],
    "safety": ["risk", "hazard", "thermal"],
    "risk": ["safety", "hazard"],
    "safe": ["safety", "risk"],
    "memory": ["recall", "retrieval", "context"],
    "agent": ["assistant", "workflow"],
    "agents": ["assistant", "workflow"],
    "openclaw": ["inference", "model"],
}


def embed_text(text: str, *, dimensions: int = VECTOR_DIMENSIONS) -> list[float]:
    vector = [0.0] * dimensions
    for token in _expanded_tokens(text):
        index = _token_index(token, dimensions)
        vector[index] += 1.0
    magnitude = math.sqrt(sum(value * value for value in vector))
    if magnitude == 0:
        return vector
    return [round(value / magnitude, 6) for value in vector]


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    return sum(a * b for a, b in zip(left, right, strict=True))


def vector_text_for_skill(
    *,
    name: str,
    description: str,
    instructions: str,
    trigger_terms: list[str],
) -> str:
    return " ".join([name, description, instructions, " ".join(trigger_terms)])


def _expanded_tokens(text: str) -> list[str]:
    tokens = [match.group(0).lower() for match in _TOKEN_PATTERN.finditer(text)]
    expanded: list[str] = []
    for token in tokens:
        expanded.append(token)
        if token.endswith("ies") and len(token) > 3:
            expanded.append(f"{token[:-3]}y")
        if token.endswith("s") and len(token) > 4:
            expanded.append(token[:-1])
        expanded.extend(_SYNONYMS.get(token, []))
    return expanded


def _token_index(token: str, dimensions: int) -> int:
    digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big") % dimensions
