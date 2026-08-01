Here's a prompt I'd give to Claude Code, Codex, or GitHub Copilot Agent. It is opinionated and asks the agent to build a production-ready system rather than just integrating Langfuse.

---

# Codex / Claude Code Prompt

```text
You are a Principal AI Systems Engineer.

Your objective is to integrate Langfuse into an existing Context Compiler application and build a production-grade AI Observability & Evaluation framework.

DO NOT simply add tracing.
Design the system so every AI recommendation is reproducible, explainable, measurable and backed by evidence.

=========================================================
APPLICATION OVERVIEW
=========================================================

Application Name:
Context Compiler

Purpose:

The application transforms

Feature Specification
        +
Observed Events
        +
Business Context

into

• ClickHouse Schema
• Context Diff
• SQL Queries
• Product Recommendations

Every recommendation must be reproducible.

Pipeline:

Feature Spec
        │
        ▼
Context Compiler
        │
 ┌──────┼─────────┐
 ▼      ▼         ▼
Events  Docs   Business Context
        │
        ▼
ClickHouse Model
        │
        ▼
SQL Generation
        │
        ▼
Evidence Verification
        │
        ▼
Recommendation Generation

=========================================================
NEW REQUIREMENT
=========================================================

Integrate Langfuse deeply into the pipeline.

Langfuse is NOT only for tracing.

It must become the observability layer for

• Agent reasoning
• Prompt versions
• Tool calls
• SQL generation
• Evidence verification
• Recommendation generation
• Evaluation scores
• Cost
• Latency
• Model versions

=========================================================
ARCHITECTURE
=========================================================

Design the following modules.

1.
Tracing Module

Every request starts a root trace.

Every major step becomes a span.

Example

Trace

    Spec Parsing

    Schema Generation

    Context Resolution

    SQL Generation

    SQL Execution

    Evidence Verification

    Recommendation Generation

    Evaluations

Each span should contain

metadata

inputs

outputs

latency

tokens

cost

errors

tool results

=========================================================

2.
Reasoning Provenance

Every recommendation must contain provenance.

Example

Recommendation ID

Spec Version

Schema Version

Prompt Version

Model Version

Context Version

SQL Query

Evidence IDs

Trace ID

Evaluation Scores

Timestamp

This provenance should be persisted.

=========================================================

3.
Evaluation Engine

Implement a pluggable evaluator system.

Each evaluator returns

name

score (0-1)

reason

metadata

Create these evaluators

SQLValidityEvaluator

EvidenceCoverageEvaluator

FreshnessEvaluator

GroundednessEvaluator

SpecAlignmentEvaluator

SchemaConsistencyEvaluator

HallucinationRiskEvaluator

RecommendationConfidenceEvaluator

BusinessImpactEvaluator

Each evaluator should be independently executable.

=========================================================

4.
LLM Judge

Implement an LLM-as-a-Judge pipeline.

Input

Question

Generated Recommendation

Evidence

SQL Result

Feature Spec

Judge Prompt

Judge Output

Store

reason

score

confidence

model

prompt version

=========================================================

5.
Evidence Verification

The recommendation agent must NEVER invent numbers.

Every numerical statement must map to

SQL Result

If a sentence references

"42%"

the verifier must locate

42%

inside SQL output.

If unsupported

fail evaluation.

=========================================================

6.
Freshness Gate

Recommendations must verify

Spec Version

Business Context Version

Schema Version

If outdated

block recommendation

return

STALE_CONTEXT

=========================================================

7.
Langfuse Scores

Automatically publish

Groundedness

Freshness

Confidence

Evidence Coverage

Hallucination Risk

Business Alignment

Recommendation Quality

SQL Validity

using Langfuse Scores API.

=========================================================

8.
ClickHouse Analytics

Create tables for

traces

spans

recommendations

evaluations

judge_results

reasoning_provenance

Build SQL examples

Average confidence by model

Hallucination trend

Prompt version comparison

Evaluator failures

Average latency

Average cost

Top failing prompts

Spec alignment over time

Freshness violations

=========================================================

9.
Dashboard Metrics

Design dashboard queries for

Recommendation Accuracy

Average Confidence

Average Evidence Coverage

Groundedness

Latency

Cost

Tokens

Failure Rate

Hallucination %

Context Freshness

Evaluator Heatmap

=========================================================

10.
Folder Structure

Refactor project into

src/

  agents/

  tracing/

  evaluators/

  provenance/

  prompts/

  langfuse/

  clickhouse/

  analytics/

  dashboard/

  types/

  utils/

=========================================================

11.
Code Requirements

Use TypeScript.

Use Langfuse SDK.

Use dependency injection.

Avoid singleton state.

Every module must have interfaces.

Strong typing.

Reusable evaluators.

Extensive logging.

Unit tests.

=========================================================

12.
Deliverables

Generate

✔ Architecture Diagram

✔ Folder Structure

✔ Interfaces

✔ Data Models

✔ Langfuse Integration

✔ Evaluation Framework

✔ Provenance Layer

✔ ClickHouse Schemas

✔ SQL Analytics

✔ Dashboard Queries

✔ Example Trace

✔ Example Evaluation

✔ Example Recommendation

=========================================================

Goal

The final system should behave like an AI analytics platform.

Every recommendation should answer

Why was this generated?

What evidence supports it?

Which SQL produced it?

Which prompt generated it?

Which model generated it?

How confident is it?

Is the context fresh?

What evaluations passed?

Can this recommendation be reproduced later?

No recommendation should exist without complete traceability.

Think like you're building Datadog + LangSmith + Langfuse + ClickHouse + OpenTelemetry specifically for AI product analytics.
```

---

### One enhancement I'd add

Since you're building this for the ClickHouse hackathon, I'd ask the coding agent to implement **OpenTelemetry-first instrumentation** rather than Langfuse-specific instrumentation.

That architecture would look like:

```text
Agents
    │
    ▼
OpenTelemetry Spans
    │
    ├──► Langfuse (AI Observability)
    ├──► ClickHouse (Analytics)
    ├──► OpenTelemetry Collector
    └──► Future Backends (Jaeger/Grafana/Tempo)
```

This gives you vendor independence while still taking full advantage of Langfuse's AI-specific features. It also aligns well with your existing observability stack and will make your project more extensible if you later add OpenTelemetry-native dashboards or collectors.
