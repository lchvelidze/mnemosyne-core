from __future__ import annotations


def score_answer(
    answer: str | None,
    *,
    used_memory: bool,
    tool_count: int,
    error: str | None = None,
) -> tuple[float, str, bool]:
    if error:
        return 0.0, f"Run failed before completion: {error}", False
    if not answer or not answer.strip():
        return 0.0, "No final answer was produced.", False
    score = 0.55
    notes = ["Final answer produced."]
    if used_memory:
        score += 0.2
        notes.append("Relevant memory was retrieved.")
    if tool_count:
        score += 0.15
        notes.append(f"{tool_count} safe tool call(s) completed.")
    score = min(score, 1.0)
    passed = score >= 0.6
    return score, " ".join(notes), passed
