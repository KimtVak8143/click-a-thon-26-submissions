import {
  EvaluationEngine,
  InMemoryProvenanceRepository,
  ProvenanceService,
  RecommendationFramework,
  defaultEvaluators,
  type EvaluationContext,
  type Logger,
} from "../src/index.js";
import { InMemoryScorePublisher } from "../src/langfuse/in-memory.js";
import { InMemoryTraceService } from "../src/tracing/in-memory.js";

const logger: Logger = { debug() {}, info(message, metadata) { console.log(message, metadata); }, warn() {}, error() {} };
const version = (id: string) => ({ id, checksum: "a".repeat(64) });
const context: EvaluationContext = {
  candidate: {
    id: "rec-checkout-001", question: "What should the checkout team do next?",
    text: "Recommend testing the shortened checkout because conversion is 42%.", confidence: 0.86,
    featureSpec: "Improve checkout conversion through controlled experiments.",
    businessContext: "Prioritize checkout conversion growth and low-risk experiments.",
    sql: "SELECT conversion_rate FROM product.checkout_daily WHERE day = today() - 1",
    evidenceIds: ["ev-checkout-001"],
    prompt: { name: "product-recommendation", version: "12" },
    model: { provider: "openai", name: "gpt-5", version: "2026-08-01" },
    versions: { spec: version("spec-v4"), schema: version("schema-v9"), businessContext: version("context-v7") },
    allowedSchema: { "product.checkout_daily": ["day", "conversion_rate"] },
  },
  currentVersions: { spec: version("spec-v4"), schema: version("schema-v9"), businessContext: version("context-v7") },
  evidence: [{ id: "ev-checkout-001", sql: "SELECT conversion_rate FROM product.checkout_daily WHERE day = today() - 1", rows: [{ conversion_rate: 0.42 }], checksum: "e".repeat(64), executedAt: new Date().toISOString() }],
  now: new Date().toISOString(), maxEvidenceAgeMs: 86_400_000,
};

const framework = new RecommendationFramework(
  new InMemoryTraceService(), new EvaluationEngine(defaultEvaluators()), new ProvenanceService(),
  new InMemoryProvenanceRepository(), new InMemoryScorePublisher(), logger,
);
console.log(JSON.stringify(await framework.evaluate(context), null, 2));
