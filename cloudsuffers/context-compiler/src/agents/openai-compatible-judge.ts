import type { JudgeModel, JudgeModelResponse } from "./llm-judge.js";

export interface OpenAICompatibleJudgeConfig {
  readonly baseUrl: string;
  readonly apiKey: string;
  readonly model: string;
  readonly timeoutMs: number;
}

export class OpenAICompatibleJudgeModel implements JudgeModel {
  readonly model: string;
  constructor(private readonly config: OpenAICompatibleJudgeConfig, private readonly fetcher: typeof fetch = fetch) { this.model = config.model; }

  async generate(system: string, input: string): Promise<JudgeModelResponse> {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), this.config.timeoutMs);
    try {
      const response = await this.fetcher(`${this.config.baseUrl.replace(/\/$/, "")}/chat/completions`, {
        method: "POST", signal: controller.signal,
        headers: { authorization: `Bearer ${this.config.apiKey}`, "content-type": "application/json" },
        body: JSON.stringify({ model: this.config.model, temperature: 0, messages: [{ role: "system", content: system }, { role: "user", content: input }], response_format: { type: "json_object" } }),
      });
      if (!response.ok) throw new Error(`Judge provider returned HTTP ${response.status}`);
      const body = await response.json() as { choices?: Array<{ message?: { content?: string } }>; usage?: { prompt_tokens?: number; completion_tokens?: number } };
      const content = body.choices?.[0]?.message?.content;
      if (!content) throw new Error("Judge provider returned no content");
      return { content, usage: { inputTokens: body.usage?.prompt_tokens ?? 0, outputTokens: body.usage?.completion_tokens ?? 0 } };
    } finally { clearTimeout(timeout); }
  }
}
