export const recommendationJudgePrompt = {
  name: "context-compiler-recommendation-judge",
  version: "1",
  template: `You are evaluating a product recommendation. Judge only from the supplied feature specification, SQL, SQL result, and evidence. Do not add outside facts. Check factual support, relevance, actionability, and whether every numeric claim is present in SQL output. Return strict JSON only: {"score": number 0..1, "confidence": number 0..1, "reason": string}.`,
} as const;
