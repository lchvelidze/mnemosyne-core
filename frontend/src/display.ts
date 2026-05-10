export function cleanFinalAnswerForDisplay(answer: string) {
  return answer
    .replace(/<tool_call\b[\s\S]*?<\/tool_call>/gi, "")
    .replace(/<tool_calls\b[\s\S]*?<\/tool_calls>/gi, "")
    .replace(/^\s*tool_calls\s*:\s*\[[\s\S]*?\]\s*/i, "")
    .trim();
}

export function asPayloadText(eventType: string, payload: Record<string, unknown>) {
  const displayPayload =
    eventType === "run.completed" && "final_answer" in payload
      ? { ...payload, final_answer: "[shown in Final Answer]" }
      : eventType === "model.synthesis_completed" && "message" in payload
        ? { ...payload, message: "[shown in Final Answer]" }
        : eventType === "memory.retrieved" && "records" in payload
          ? { records: "[shown in Memory Hits]" }
          : payload;
  return JSON.stringify(displayPayload, null, 2);
}
