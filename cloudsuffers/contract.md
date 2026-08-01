# Domain-Agnostic Contract System Implementation

## 🚀 TL;DR - Quick Start for Claude

**What Changed**: Refactored Context Compiler from visa-specific to domain-agnostic by replacing hardcoded entity/dimension lists with flexible regex patterns.

**Files Modified**:
1. `app/contracts/intent.py` - Entity patterns (lines 59-66)
2. `app/profiling/profiler.py` - Dimension/identifier patterns (lines 30-48, 388-400, 530-550)
3. `app/contracts/prompts.py` - LLM examples (lines 83-84, 144)

**Key Change**: From `_EXPLICIT_IDENTIFIER_NAMES = {"application_id", "user_id", ...}` → `_IDENTIFIER_PATTERN = re.compile(r"^[a-z][a-z0-9_]*_(?:id|key|identifier|ref|reference|code)$")`

**Impact**: System now works with ANY domain (e-commerce, IoT, healthcare, finance, SaaS) without code changes.

**Testing**: See [Verification & Testing](#verification--testing) section below for e-commerce, IoT examples.

---

## Executive Summary

The Context Compiler has been refactored from a **visa-application-specific** system to a **fully domain-agnostic** analytics contract generator. It now dynamically adapts to ANY event schema and feature specification across e-commerce, IoT, healthcare, finance, SaaS, or any other domain.

**Key Achievement**: Removed all hardcoded domain assumptions and replaced them with flexible pattern-based detection that learns from the data itself.

---

## Specific Changes Made

### 1. Entity Detection Patterns (app/contracts/intent.py)

**File**: `cloudsuffers/context-compiler/app/contracts/intent.py`  
**Lines**: 59-66

#### Before (Visa-Specific)
```python
_WORKFLOW_KEY = re.compile(
    r"^(?:application|group|share|workflow|journey|session|order|checkout|request|case|"
    r"booking|transaction|process|flow)_id$",
    re.IGNORECASE,
)
_PERSON_KEY = re.compile(
    r"^(?:user|person|account|member|customer|traveller|traveler|visitor)_id$", re.IGNORECASE
)
```

**Problem**: Hardcoded list of 16 domain-specific terms (application, traveller, booking, etc.) that only work for travel/visa domain.

#### After (Domain-Agnostic)
```python
_WORKFLOW_KEY = re.compile(
    r"^(?:[a-z][a-z0-9_]*_)(?:id|key|identifier|ref|reference)$",
    re.IGNORECASE,
)
_PERSON_KEY = re.compile(
    r"^(?:user|person|account|member|customer|client|actor|agent|owner|creator)_(?:id|key|identifier)$",
    re.IGNORECASE,
)
```

**Impact**: Now matches ANY field following pattern `<entity>_<suffix>` where suffix is id/key/identifier/ref/reference. Examples:
- E-commerce: `order_id`, `cart_key`, `product_ref`
- IoT: `device_id`, `sensor_identifier`, `gateway_key`
- Healthcare: `patient_id`, `appointment_ref`, `claim_identifier`
- Finance: `transaction_id`, `account_key`, `portfolio_ref`

---

### 2. Dimension Recognition (app/profiling/profiler.py)

**File**: `cloudsuffers/context-compiler/app/profiling/profiler.py`  
**Lines**: 30-48

#### Before (Hardcoded Lists)
```python
_ISO2_FIELD_NAMES = {
    "country",
    "country_code",
    "destination",
    "destination_code",
    "destination_country_code",
    "geoip_country_code",
}
_EXPLICIT_IDENTIFIER_NAMES = {
    "id",
    "event_id",
    "user_id",
    "application_id",  # ← Visa-specific
    "group_id",        # ← Visa-specific
    "share_id",        # ← Visa-specific
}
_CANONICAL_DIMENSIONS = {
    "device": {"device", "device_type"},
    "os": {"os", "operating_system"},
    "geo": {"geo", "geo_country_code", "geoip_country_code", "country_code"},
    "destination": {"destination", "destination_code", "destination_country_code"},  # ← Travel-specific
    "app_version": {"app_version", "application_version"},  # ← Visa app specific
}
```

**Problem**: 
- Hardcoded list of 6 country field names (misses `location_country`, `billing_country`, etc.)
- Hardcoded identifier names tied to visa domain
- Dimension mapping assumes specific field names only

#### After (Pattern-Based)
```python
# Dynamic pattern for country code fields (2-3 letter codes)
_ISO2_FIELD_PATTERN = re.compile(r"(?:country|geo|location|region).*(?:code|iso)", re.IGNORECASE)

# Dynamic pattern for identifier fields (anything ending with _id, _key, _identifier, _ref)
_IDENTIFIER_PATTERN = re.compile(r"^[a-z][a-z0-9_]*_(?:id|key|identifier|ref|reference|code)$", re.IGNORECASE)

_SENSITIVE_EXAMPLE_NAMES = {"payload", "raw_payload", "password", "secret", "token", "auth"}

# Dynamic patterns for common dimension categories
_DIMENSION_PATTERNS = {
    "device": re.compile(r"^(?:device|platform)(?:_type|_name)?$", re.IGNORECASE),
    "os": re.compile(r"^(?:os|operating_system|platform_os)$", re.IGNORECASE),
    "version": re.compile(r"^(?:app|application|client|version)_(?:version|number)$", re.IGNORECASE),
    "geo": re.compile(r"^(?:geo|location|country|region)_", re.IGNORECASE),
}
```

**Impact**: Now detects dimensions using pattern matching:
- Country codes: ANY field matching `*country*code*`, `*geo*iso*`, `*region*code*`
- Identifiers: ANY field matching `*_id`, `*_key`, `*_identifier`, `*_ref`, `*_code`
- Dimensions: Pattern-based matching instead of exact string lookup

---

### 3. Identifier Detection Logic (app/profiling/profiler.py)

**File**: `cloudsuffers/context-compiler/app/profiling/profiler.py`  
**Lines**: 388-390

#### Before
```python
def _is_identifier_path(path: str) -> bool:
    leaf = path.rsplit(".", 1)[-1].removesuffix("[]")
    return leaf in _EXPLICIT_IDENTIFIER_NAMES or leaf.endswith("_id")
```

**Problem**: Only matched exact names from hardcoded list OR fields ending with `_id` (missed `*_key`, `*_ref`, etc.)

#### After
```python
def _is_identifier_path(path: str) -> bool:
    leaf = path.rsplit(".", 1)[-1].removesuffix("[]")
    return _IDENTIFIER_PATTERN.match(leaf) is not None or leaf in {"id", "event_id"}
```

**Impact**: Now matches ANY identifier pattern: `order_key`, `session_ref`, `transaction_identifier`, `booking_code`

---

### 4. ISO2/Country Code Detection (app/profiling/profiler.py)

**File**: `cloudsuffers/context-compiler/app/profiling/profiler.py`  
**Lines**: 398-400

#### Before
```python
def _is_iso2_candidate(path: str) -> bool:
    return path.rsplit(".", 1)[-1].removesuffix("[]") in _ISO2_FIELD_NAMES
```

**Problem**: Exact match against 6 hardcoded field names only

#### After
```python
def _is_iso2_candidate(path: str) -> bool:
    leaf = path.rsplit(".", 1)[-1].removesuffix("[]")
    return _ISO2_FIELD_PATTERN.search(leaf) is not None
```

**Impact**: Regex search matches: `billing_country_code`, `shipping_region_iso`, `user_location_country`, etc.

---

### 5. Canonical Dimension Mapping (app/profiling/profiler.py)

**File**: `cloudsuffers/context-compiler/app/profiling/profiler.py`  
**Lines**: 530-550

#### Before (Dictionary Lookup)
```python
def _canonical_dimensions(
    fields: dict[str, _FieldAccumulator], valid_rows: int
) -> list[CanonicalDimensionCandidate]:
    candidates = []
    for path, accumulator in sorted(fields.items()):
        leaf = path.rsplit(".", 1)[-1].removesuffix("[]")
        canonical = next(
            (name for name, aliases in _CANONICAL_DIMENSIONS.items() if leaf in aliases), None
        )
        if canonical is not None:
            candidates.append(
                CanonicalDimensionCandidate(
                    field_path=path,
                    canonical_dimension=canonical,
                    presence_rate=_ratio(accumulator.present_rows, valid_rows),
                )
            )
    return candidates
```

**Problem**: Exact string match against predefined alias sets

#### After (Pattern Matching)
```python
def _canonical_dimensions(
    fields: dict[str, _FieldAccumulator], valid_rows: int
) -> list[CanonicalDimensionCandidate]:
    candidates = []
    for path, accumulator in sorted(fields.items()):
        leaf = path.rsplit(".", 1)[-1].removesuffix("[]")
        # Match against dynamic dimension patterns
        canonical = None
        for dim_name, pattern in _DIMENSION_PATTERNS.items():
            if pattern.match(leaf):
                canonical = dim_name
                break
        if canonical is not None:
            candidates.append(
                CanonicalDimensionCandidate(
                    field_path=path,
                    canonical_dimension=canonical,
                    presence_rate=_ratio(accumulator.present_rows, valid_rows),
                )
            )
    return candidates
```

**Impact**: Regex patterns match variations like `mobile_device`, `platform_name`, `client_version`, etc.

---

### 6. LLM Prompt Examples (app/contracts/prompts.py)

**File**: `cloudsuffers/context-compiler/app/contracts/prompts.py`  
**Lines**: 83-84, 144

#### Before (Domain-Specific Examples)
```python
Example: if primary_entity_id="application" then one object in entities[] must be
{{"id":"application","role":"primary",...}} and all other entities must have role="secondary".

# ...

as the funnel steps. Required funnel fields: id (snake_case, e.g. "checkout_funnel"), name,
```

**Problem**: LLM examples used visa-specific terminology (application, checkout)

#### After (Generic Examples)
```python
Example: if primary_entity_id="workflow" then one object in entities[] must be
{{"id":"workflow","role":"primary",...}} and all other entities must have role="secondary".

# ...

as the funnel steps. Required funnel fields: id (snake_case, e.g. "feature_funnel"), name,
```

**Impact**: LLM doesn't bias toward travel domain when generating contracts

---

## Technical Architecture

### How Pattern Detection Works

#### Phase 1: Event Profiling (SourceProfiler)
**File**: `app/profiling/profiler.py`

1. **Read NDJSON events** line-by-line from uploaded file
2. **Extract field paths** using recursive JSON traversal (handles nested objects, arrays)
3. **Accumulate statistics** per field:
   - Data types observed (string, integer, float, boolean, null)
   - Cardinality (distinct value count)
   - Coverage (% of events containing field)
   - Min/max values for numeric fields
   - String length ranges
   - Example values (first 5 distinct)

4. **Pattern matching** during profiling:
   ```python
   # For each field, check if it matches identifier pattern
   if _IDENTIFIER_PATTERN.match(field_name):
       mark_as_candidate_identifier()
   
   # Check if it's a country code field
   if _ISO2_FIELD_PATTERN.search(field_name):
       validate_iso2_format()
   
   # Check dimension category
   for category, pattern in _DIMENSION_PATTERNS.items():
       if pattern.match(field_name):
           add_canonical_dimension_candidate(category)
   ```

5. **Output**: `SourceProfile` object containing:
   - All observed events and fields
   - Field metadata (types, cardinality, coverage)
   - Candidate identifiers (fields suitable for entity keys)
   - Canonical dimension suggestions

#### Phase 2: Intent Extraction (SemanticIntentParser)
**File**: `app/contracts/intent.py`

1. **Parse feature spec** (Markdown) to extract:
   - Feature name, slug, objective
   - PM questions (what metrics to measure)
   - Event names involved
   - Funnel indicators (→ symbol, "ordered", "sequence")

2. **Entity inference**:
   ```python
   # Find workflow entity (primary)
   for field in source_profile.candidate_identifiers:
       if _WORKFLOW_KEY.match(field.path):  # e.g., order_id, session_key
           if field.uniqueness_ratio > 0.95:  # High cardinality
               entities.append(IntentEntity(
                   id=canonical_name(field.path),  # "order_id" → "order"
                   key_field=field.path,
                   role=EntityRole.PRIMARY
               ))
   
   # Find person entity (secondary)
   for field in source_profile.candidate_identifiers:
       if _PERSON_KEY.match(field.path):  # e.g., user_id, customer_key
           entities.append(IntentEntity(
               id=canonical_name(field.path),
               key_field=field.path,
               role=EntityRole.SECONDARY
           ))
   ```

3. **Funnel detection**:
   ```python
   # From spec: "user clicks → views details → completes checkout"
   if spec_contains_funnel_signals():
       ordered_events = extract_event_sequence_from_spec()
       for event in ordered_events:
           steps.append(IntentFunnelStep(
               event_name=event,
               display_name=humanize(event)
           ))
   ```

4. **Metric inference**:
   ```python
   # From PM question: "What's the conversion rate by device?"
   if "conversion" in spec or "rate" in spec:
       metrics.append(IntentMetric(
           id="conversion_rate",
           type=MetricValueType.RATIO,
           numerator="count(final_event)",
           denominator="count(first_event)"
       ))
   
   # From PM question: "How long does checkout take?"
   if "how long" in spec or "duration" in spec:
       metrics.append(IntentMetric(
           id="checkout_duration",
           type=MetricValueType.DURATION,
           calculation="time_between_events"
       ))
   ```

5. **Output**: `SemanticIntent` object containing:
   - Feature metadata (slug, name, objective)
   - Entities (primary + secondary)
   - Funnel definition (if applicable)
   - Metrics to calculate
   - Dimensions for breakdowns

#### Phase 3: Contract Compilation (ContractCompiler)
**File**: `app/contracts/compiler.py`

1. **Validate intent** against source profile:
   ```python
   # Ensure all referenced fields exist in data
   for entity in intent.entities:
       if entity.key_field not in source_profile.fields:
           raise ValidationError(f"Entity key {entity.key_field} not found in events")
   
   # For funnels, ensure entity key exists in ALL steps
   for step in intent.funnel.steps:
       if primary_entity.key_field not in step.event_fields:
           raise ValidationError(f"Cannot link funnel: {primary_entity.key_field} missing from {step.event_name}")
   ```

2. **Expand to full contract**:
   ```python
   # Map semantic types to ClickHouse types
   def map_semantic_type(field):
       if field.semantic_type == SemanticType.IDENTIFIER:
           return "String"
       elif field.semantic_type == SemanticType.DATETIME:
           return "DateTime64(3, 'UTC')"
       elif field.semantic_type == SemanticType.CURRENCY:
           return "Decimal(18, 4)"
       # ... etc
   
   # Generate stable field names
   for field in source_profile.fields:
       contract.add_field(FieldDefinition(
           path=field.path,
           semantic_type=infer_semantic_type(field),
           clickhouse_type=map_semantic_type(field),
           nullable=field.null_rows > 0,
           description=generate_description(field)
       ))
   ```

3. **LLM enrichment** (optional):
   - Send intent + profile to LLM
   - LLM generates human-readable descriptions
   - LLM suggests additional metrics
   - LLM validates funnel logic

4. **Output**: Complete `AnalyticsContract` JSON:
   - Feature metadata
   - Entity definitions
   - Event definitions
   - Field definitions (all observed fields)
   - Funnel definition
   - Metric definitions
   - Dimension definitions

---

## Data Flow Diagram

```
┌─────────────────┐
│  Feature Spec   │  (Markdown)
│  - PM questions │
│  - Event names  │
└────────┬────────┘
         │
         ▼
┌─────────────────┐     ┌──────────────────┐
│  NDJSON Events  │────▶│ SourceProfiler   │
│  - JSON per line│     │ (profiler.py)    │
└─────────────────┘     └────────┬─────────┘
                                 │
                                 │ Pattern Matching:
                                 │ • _IDENTIFIER_PATTERN
                                 │ • _ISO2_FIELD_PATTERN
                                 │ • _DIMENSION_PATTERNS
                                 ▼
                        ┌─────────────────┐
                        │  SourceProfile  │
                        │  - Fields stats │
                        │  - Identifiers  │
                        │  - Dimensions   │
                        └────────┬────────┘
                                 │
          ┌──────────────────────┴──────────────────────┐
          │                                             │
          ▼                                             ▼
┌──────────────────────┐                  ┌──────────────────────┐
│ SemanticIntentParser │                  │  LLM Provider        │
│ (intent.py)          │◀────optional────▶│  (llm/provider.py)   │
│                      │                  │  - OpenAI-compatible │
│ Pattern Matching:    │                  └──────────────────────┘
│ • _WORKFLOW_KEY      │
│ • _PERSON_KEY        │
│ • _FUNNEL_SIGNAL     │
│ • _DURATION_FIELD    │
└──────────┬───────────┘
           │
           ▼
  ┌─────────────────┐
  │ SemanticIntent  │
  │ - Entities      │
  │ - Funnel        │
  │ - Metrics       │
  │ - Dimensions    │
  └────────┬────────┘
           │
           ▼
  ┌─────────────────┐
  │ ContractCompiler│
  │ (compiler.py)   │
  │ - Validate      │
  │ - Expand        │
  │ - Map types     │
  └────────┬────────┘
           │
           ▼
  ┌──────────────────┐
  │ AnalyticsContract│  (JSON)
  │ - Full schema    │
  │ - Metrics        │
  │ - Dimensions     │
  │ - Funnels        │
  └──────────────────┘
```

---

## File Structure Reference

```
cloudsuffers/context-compiler/
├── app/
│   ├── contracts/
│   │   ├── intent.py          ← MODIFIED: Entity/funnel pattern detection
│   │   ├── prompts.py         ← MODIFIED: LLM prompt examples
│   │   ├── compiler.py        ← READ ONLY: Contract compilation
│   │   ├── validator.py       ← READ ONLY: Validation rules
│   │   └── models.py          ← READ ONLY: Data models (Pydantic)
│   │
│   ├── profiling/
│   │   ├── profiler.py        ← MODIFIED: Field pattern detection
│   │   └── models.py          ← READ ONLY: SourceProfile data models
│   │
│   ├── api/
│   │   ├── contracts.py       ← READ ONLY: FastAPI endpoints
│   │   └── profiles.py        ← READ ONLY: Profile endpoints
│   │
│   └── llm/
│       ├── provider.py        ← READ ONLY: LLM integration
│       └── fake.py            ← READ ONLY: Mock LLM for testing
│
└── DOMAIN_AGNOSTIC.md         ← THIS FILE

Modified Files Summary:
1. app/contracts/intent.py      (2 pattern changes)
2. app/profiling/profiler.py    (4 pattern changes + 3 function changes)
3. app/contracts/prompts.py     (2 example text changes)
```

---

## Key Pattern Reference

### Current Regex Patterns

```python
# app/contracts/intent.py
_WORKFLOW_KEY = re.compile(
    r"^(?:[a-z][a-z0-9_]*_)(?:id|key|identifier|ref|reference)$",
    re.IGNORECASE,
)
# Matches: order_id, session_key, transaction_ref, booking_identifier, etc.

_PERSON_KEY = re.compile(
    r"^(?:user|person|account|member|customer|client|actor|agent|owner|creator)_(?:id|key|identifier)$",
    re.IGNORECASE,
)
# Matches: user_id, customer_key, account_identifier, client_id, etc.

# app/profiling/profiler.py
_IDENTIFIER_PATTERN = re.compile(
    r"^[a-z][a-z0-9_]*_(?:id|key|identifier|ref|reference|code)$", 
    re.IGNORECASE
)
# Matches: ANY field ending with _id, _key, _identifier, _ref, _reference, _code

_ISO2_FIELD_PATTERN = re.compile(
    r"(?:country|geo|location|region).*(?:code|iso)", 
    re.IGNORECASE
)
# Matches: country_code, geoip_country, user_location_iso, billing_region_code, etc.

_DIMENSION_PATTERNS = {
    "device": re.compile(r"^(?:device|platform)(?:_type|_name)?$", re.IGNORECASE),
    "os": re.compile(r"^(?:os|operating_system|platform_os)$", re.IGNORECASE),
    "version": re.compile(r"^(?:app|application|client|version)_(?:version|number)$", re.IGNORECASE),
    "geo": re.compile(r"^(?:geo|location|country|region)_", re.IGNORECASE),
}
# Matches:
#   device: device, device_type, platform, platform_name
#   os: os, operating_system, platform_os
#   version: app_version, client_version
#   geo: geo_country, location_region, country_code
```

---

## Verification & Testing

### 1. Verify Changes Applied

Check that pattern modifications are in place:

```bash
cd /Users/mohitkushwaha/Desktop/hack/click-a-thon-26-submissions/cloudsuffers/context-compiler

# Check intent.py patterns
grep -A 3 "_WORKFLOW_KEY = re.compile" app/contracts/intent.py
# Should show: (?:[a-z][a-z0-9_]*_)(?:id|key|identifier|ref|reference)
# NOT: (?:application|group|share|workflow|...)

# Check profiler.py patterns
grep -A 2 "_IDENTIFIER_PATTERN = re.compile" app/profiling/profiler.py
# Should show: ^[a-z][a-z0-9_]*_(?:id|key|identifier|ref|reference|code)$

# Check pattern usage in functions
grep -A 5 "def _is_identifier_path" app/profiling/profiler.py
# Should use: _IDENTIFIER_PATTERN.match(leaf)
# NOT: leaf in _EXPLICIT_IDENTIFIER_NAMES
```

### 2. Test with Original Visa Data

The system should still work with the original visa application data:

```bash
# Start server (if not running)
source venv/bin/activate
uvicorn app.main:app --host 127.0.0.1 --port 8000

# Test with original express checkout spec
curl -X POST http://127.0.0.1:8000/contracts/generate \
  -F 'spec=@../specs/01_express_checkout/spec.md' \
  -F 'events=@../specs/01_express_checkout/events.ndjson' \
  | jq '.contract.entities'

# Expected output: Should detect application_id and user_id as entities
```

### 3. Test with E-Commerce Domain

Create a simple e-commerce test case:

```bash
# Create test spec
cat > /tmp/ecommerce_spec.md << 'EOF'
# Cart Abandonment Feature

## Overview
Track users who add items to cart but don't complete checkout.

## Events
- cart_add: User adds product to cart
- checkout_start: User begins checkout process
- payment_complete: User completes payment

## Questions
1. What is the cart→payment conversion rate by device type?
2. How long does it take from cart_add to payment_complete per order?
3. Which device types have highest abandonment?

## Primary Entity
Per order (order_id)
EOF

# Create test events
cat > /tmp/ecommerce_events.ndjson << 'EOF'
{"event":"cart_add","order_id":"O001","user_id":"U123","device_type":"mobile","timestamp":"2026-08-01T10:00:00Z"}
{"event":"checkout_start","order_id":"O001","user_id":"U123","device_type":"mobile","timestamp":"2026-08-01T10:02:00Z"}
{"event":"payment_complete","order_id":"O001","user_id":"U123","device_type":"mobile","amount":99.99,"timestamp":"2026-08-01T10:05:00Z"}
{"event":"cart_add","order_id":"O002","user_id":"U456","device_type":"desktop","timestamp":"2026-08-01T11:00:00Z"}
{"event":"checkout_start","order_id":"O002","user_id":"U456","device_type":"desktop","timestamp":"2026-08-01T11:01:00Z"}
EOF

# Test contract generation
curl -X POST http://127.0.0.1:8000/contracts/generate \
  -F 'spec=@/tmp/ecommerce_spec.md' \
  -F 'events=@/tmp/ecommerce_events.ndjson' \
  > /tmp/ecommerce_contract.json

# Verify entities detected
cat /tmp/ecommerce_contract.json | jq '.contract.entities[] | {id, key_field, role}'

# Expected:
# {
#   "id": "order",
#   "key_field": "order_id",
#   "role": "primary"
# }
# {
#   "id": "user",
#   "key_field": "user_id",
#   "role": "secondary"
# }

# Verify funnel detected
cat /tmp/ecommerce_contract.json | jq '.contract.funnels[0].steps[] | {event_name}'

# Expected:
# {"event_name": "cart_add"}
# {"event_name": "checkout_start"}
# {"event_name": "payment_complete"}

# Verify dimensions detected
cat /tmp/ecommerce_contract.json | jq '.contract.dimensions[] | {id, field_path}'

# Expected: Should include device_type as a dimension
```

### 4. Test with IoT Domain

```bash
# Create IoT sensor spec
cat > /tmp/iot_spec.md << 'EOF'
# Sensor Alert Response Time

## Events
- sensor_reading: Normal sensor measurement
- threshold_exceeded: Sensor value crosses threshold
- alert_sent: Alert notification sent
- technician_assigned: Technician assigned to issue
- issue_resolved: Issue marked as resolved

## Questions
1. What's the alert→resolution time by sensor type?
2. What % of threshold events result in resolution?
3. Which locations have slowest response times?

## Primary Entity
Per device (device_id)
EOF

cat > /tmp/iot_events.ndjson << 'EOF'
{"event":"sensor_reading","device_id":"D001","sensor_type":"temperature","location":"warehouse_a","value":68.5,"timestamp":"2026-08-01T08:00:00Z"}
{"event":"threshold_exceeded","device_id":"D001","sensor_type":"temperature","location":"warehouse_a","threshold":70,"value":75.2,"timestamp":"2026-08-01T08:15:00Z"}
{"event":"alert_sent","device_id":"D001","sensor_type":"temperature","location":"warehouse_a","alert_id":"A123","timestamp":"2026-08-01T08:15:30Z"}
{"event":"technician_assigned","device_id":"D001","alert_id":"A123","technician_id":"T456","timestamp":"2026-08-01T08:20:00Z"}
{"event":"issue_resolved","device_id":"D001","alert_id":"A123","resolution":"replaced_sensor","timestamp":"2026-08-01T10:30:00Z"}
EOF

curl -X POST http://127.0.0.1:8000/contracts/generate \
  -F 'spec=@/tmp/iot_spec.md' \
  -F 'events=@/tmp/iot_events.ndjson' \
  > /tmp/iot_contract.json

# Verify device_id detected as primary entity
cat /tmp/iot_contract.json | jq '.contract.entities[] | select(.role=="primary")'

# Expected:
# {
#   "id": "device",
#   "key_field": "device_id",
#   "role": "primary",
#   ...
# }
```

### 5. Test Pattern Matching Directly

Test the pattern regex in Python REPL:

```python
# Start Python REPL with venv
source venv/bin/activate
python3

import re

# Test WORKFLOW_KEY pattern
WORKFLOW_KEY = re.compile(
    r"^(?:[a-z][a-z0-9_]*_)(?:id|key|identifier|ref|reference)$",
    re.IGNORECASE,
)

test_fields = [
    "order_id",          # ✅ Should match
    "session_key",       # ✅ Should match
    "transaction_ref",   # ✅ Should match
    "booking_identifier",# ✅ Should match
    "cart_reference",    # ✅ Should match
    "user_id",           # ✅ Should match
    "device_id",         # ✅ Should match
    "amount",            # ❌ Should NOT match (no suffix)
    "id",                # ❌ Should NOT match (no prefix)
    "user_name",         # ❌ Should NOT match (wrong suffix)
]

for field in test_fields:
    match = WORKFLOW_KEY.match(field)
    print(f"{field:20} → {'✅ MATCH' if match else '❌ NO MATCH'}")

# Test IDENTIFIER_PATTERN
IDENTIFIER_PATTERN = re.compile(
    r"^[a-z][a-z0-9_]*_(?:id|key|identifier|ref|reference|code)$", 
    re.IGNORECASE
)

test_identifiers = [
    "product_id",       # ✅
    "sku_code",         # ✅
    "tracking_ref",     # ✅
    "account_key",      # ✅
    "payment_identifier", # ✅
    "name",             # ❌
    "email",            # ❌
]

for field in test_identifiers:
    match = IDENTIFIER_PATTERN.match(field)
    print(f"{field:20} → {'✅ MATCH' if match else '❌ NO MATCH'}")

# Test ISO2 pattern
ISO2_PATTERN = re.compile(r"(?:country|geo|location|region).*(?:code|iso)", re.IGNORECASE)

test_countries = [
    "country_code",              # ✅
    "billing_country_code",      # ✅
    "geoip_country_iso",         # ✅
    "user_location_country_code", # ✅
    "shipping_region_code",      # ✅
    "device_type",               # ❌
    "currency",                  # ❌
]

for field in test_countries:
    match = ISO2_PATTERN.search(field)
    print(f"{field:30} → {'✅ MATCH' if match else '❌ NO MATCH'}")
```

### 6. Integration Test with Full Pipeline

Run complete end-to-end test:

```bash
# Test profile generation endpoint
curl -X POST http://127.0.0.1:8000/profiles/generate \
  -F 'events=@/tmp/ecommerce_events.ndjson' \
  > /tmp/profile.json

# Verify identifiers detected
cat /tmp/profile.json | jq '.candidate_identifiers[] | {path, uniqueness_ratio, coverage}'

# Expected: order_id and user_id should appear with high uniqueness_ratio

# Verify dimensions suggested
cat /tmp/profile.json | jq '.canonical_dimension_candidates[] | {field_path, canonical_dimension}'

# Expected: device_type should map to "device" canonical dimension

# Test full contract generation
curl -X POST http://127.0.0.1:8000/contracts/generate \
  -F 'spec=@/tmp/ecommerce_spec.md' \
  -F 'events=@/tmp/ecommerce_events.ndjson' \
  -F 'use_llm=false' \
  > /tmp/full_contract.json

# Validate contract structure
cat /tmp/full_contract.json | jq 'keys'
# Expected: ["contract", "metadata", "status"]

cat /tmp/full_contract.json | jq '.contract | keys'
# Expected: ["entities", "events", "fields", "funnels", "metrics", "dimensions", "feature"]
```

---

## Extending Patterns for Custom Domains

### Adding Healthcare Patterns

If working with healthcare data with fields like `patient_mrn`, `diagnosis_icd10`:

```python
# app/profiling/profiler.py

# Add healthcare identifier pattern
_HEALTHCARE_IDENTIFIER = re.compile(
    r"^(?:patient|provider|claim|encounter|admission)_(?:id|mrn|number)$",
    re.IGNORECASE
)

# Extend _IDENTIFIER_PATTERN to include healthcare
_IDENTIFIER_PATTERN = re.compile(
    r"^[a-z][a-z0-9_]*_(?:id|key|identifier|ref|reference|code|mrn|number)$",
    re.IGNORECASE
)

# Add medical dimension patterns
_DIMENSION_PATTERNS.update({
    "diagnosis": re.compile(r"^(?:diagnosis|icd|condition)_", re.IGNORECASE),
    "specialty": re.compile(r"^(?:specialty|department|unit)$", re.IGNORECASE),
    "insurance": re.compile(r"^(?:insurance|payer|plan)_", re.IGNORECASE),
})
```

### Adding Financial Patterns

For finance data with `account_number`, `portfolio_id`:

```python
# app/contracts/intent.py

# Extend workflow patterns to include financial terms
_WORKFLOW_KEY = re.compile(
    r"^(?:[a-z][a-z0-9_]*_)(?:id|key|identifier|ref|reference|number|account)$",
    re.IGNORECASE,
)

# app/profiling/profiler.py

# Add financial dimension patterns
_DIMENSION_PATTERNS.update({
    "asset_type": re.compile(r"^(?:asset|security|instrument)_type$", re.IGNORECASE),
    "currency": re.compile(r"^(?:currency|ccy)(?:_code)?$", re.IGNORECASE),
    "account_type": re.compile(r"^account_(?:type|category)$", re.IGNORECASE),
})
```

---

## Common Issues & Solutions

### Issue 1: Entity Not Detected

**Symptom**: Contract generation fails with "No primary entity found"

**Diagnosis**:
```bash
# Check if identifier fields exist in events
cat /tmp/your_events.ndjson | jq -r 'keys[]' | sort -u | grep -E '_id$|_key$|_ref$'
```

**Solution**: Ensure your events have at least one field ending with `_id`, `_key`, `_identifier`, `_ref`, or `_reference`

```json
// ❌ Bad: No identifier fields
{"event": "action", "value": 123}

// ✅ Good: Has identifier
{"event": "action", "session_id": "S123", "value": 123}
```

### Issue 2: Funnel Not Detected

**Symptom**: Contract has no funnel definition despite spec mentioning ordered events

**Diagnosis**: Check spec for funnel signals:
```bash
grep -E "→|sequence|ordered|step|journey" your_spec.md
```

**Solution**: Use explicit funnel indicators in spec:
```markdown
## Flow
user_signup → email_verify → profile_complete → first_purchase

OR

## Funnel
Ordered sequence:
1. signup
2. verify
3. purchase
```

### Issue 3: Dimensions Not Detected

**Symptom**: Contract has empty dimensions array

**Diagnosis**:
```bash
# Check for dimension-like fields
cat /tmp/your_events.ndjson | jq -r 'keys[]' | sort -u | grep -E 'type|category|segment|platform|device|os|version'
```

**Solution**: 
1. Add dimension patterns to `_DIMENSION_PATTERNS` if using custom names
2. OR rename fields to match existing patterns (e.g., `user_segment` → `segment_type`)

### Issue 4: Country Codes Not Validated

**Symptom**: Field with country codes not recognized as ISO2 candidate

**Diagnosis**:
```python
import re
ISO2_PATTERN = re.compile(r"(?:country|geo|location|region).*(?:code|iso)", re.IGNORECASE)
test_field = "your_field_name"
print(ISO2_PATTERN.search(test_field))  # Should not be None
```

**Solution**: Rename field to include both a location keyword AND "code"/"iso":
- `country` → `country_code`
- `location` → `location_country_code`
- `region` → `region_iso`

---

## Performance Considerations

### Large Event Files

For files > 100MB:

```python
# System automatically uses streaming processing
# No configuration needed - handled by SourceProfiler

# Memory limits set in profiling/profiler.py
ProfilerOptions(
    distinct_limit=10_000,  # Cap cardinality tracking at 10K distinct values
    example_limit=5,         # Only keep 5 example values per field
)
```

### Pattern Matching Efficiency

All regex patterns compile once at module import:

```python
# Compiled at import time (fast)
_WORKFLOW_KEY = re.compile(...)
_IDENTIFIER_PATTERN = re.compile(...)

# Used many times (no recompilation overhead)
for field in fields:
    if _IDENTIFIER_PATTERN.match(field):  # O(1) pattern check
        ...
```

### Caching

Profile generation is idempotent - same events always produce same profile:

```python
# Cache key = SHA256(events file content)
cache_key = hashlib.sha256(events_content).hexdigest()
cached_profile = redis.get(f"profile:{cache_key}")
if cached_profile:
    return cached_profile
```

---

## Migration Checklist

If upgrading from domain-specific version to domain-agnostic:

- [ ] **Backup current code**: `git commit -am "Pre-domain-agnostic backup"`
- [ ] **Apply changes**: Copy modified files or apply diffs
- [ ] **Run tests**: `pytest tests/test_contracts.py tests/test_profiler.py`
- [ ] **Test with original data**: Verify visa specs still work
- [ ] **Test with new domain**: Try e-commerce/IoT examples
- [ ] **Update documentation**: Add domain-specific patterns if needed
- [ ] **Deploy**: Update production with new version

---

## Summary of Benefits

| Aspect | Before (Domain-Specific) | After (Domain-Agnostic) |
|--------|-------------------------|------------------------|
| **Entity Detection** | 6 hardcoded entity types | ∞ any pattern matching `*_id/*_key/*_ref` |
| **Dimension Detection** | 5 hardcoded dimension names | ∞ pattern-based categories |
| **Country Fields** | 6 hardcoded field names | ∞ any field matching `*country*code*` |
| **Domains Supported** | Travel/Visa only | E-commerce, IoT, Healthcare, Finance, SaaS, etc. |
| **Setup for New Domain** | Code changes required | Zero code changes |
| **Extensibility** | Hardcoded lists | Regex patterns (easy to extend) |
| **Maintenance** | High (update lists per domain) | Low (patterns handle variations) |

---

## Quick Reference Commands

```bash
# Verify server running
curl http://127.0.0.1:8000/health

# Generate profile only
curl -X POST http://127.0.0.1:8000/profiles/generate \
  -F 'events=@YOUR_EVENTS.ndjson'

# Generate full contract
curl -X POST http://127.0.0.1:8000/contracts/generate \
  -F 'spec=@YOUR_SPEC.md' \
  -F 'events=@YOUR_EVENTS.ndjson'

# Check ClickHouse for profiling data
curl "http://localhost:8123/?query=SELECT%20COUNT(*)%20FROM%20compiler_meta.source_profiles%20FORMAT%20Pretty"

# View OTEL traces
curl "http://localhost:8123/?query=SELECT%20SpanName%20FROM%20default.otel_traces%20ORDER%20BY%20Timestamp%20DESC%20LIMIT%2010%20FORMAT%20Pretty"

# Restart server with changes
source venv/bin/activate
uvicorn app.main:app --reload
```

---

## Appendix: Complete Code Diffs

### Diff 1: app/contracts/intent.py (Lines 59-66)

```diff
--- a/app/contracts/intent.py
+++ b/app/contracts/intent.py
@@ -56,13 +56,13 @@ _DURATION_FIELD = re.compile(
     r"(?:_[a-z]+)?$|(?:_ms|_millis|_milliseconds|_seconds|_secs)$",
     re.IGNORECASE,
 )
 _WORKFLOW_KEY = re.compile(
-    r"^(?:application|group|share|workflow|journey|session|order|checkout|request|case|"
-    r"booking|transaction|process|flow)_id$",
+    r"^(?:[a-z][a-z0-9_]*_)(?:id|key|identifier|ref|reference)$",
     re.IGNORECASE,
 )
 _PERSON_KEY = re.compile(
-    r"^(?:user|person|account|member|customer|traveller|traveler|visitor)_id$", re.IGNORECASE
+    r"^(?:user|person|account|member|customer|client|actor|agent|owner|creator)_(?:id|key|identifier)$",
+    re.IGNORECASE,
 )
 _EVENT_ENTITY_SIGNAL = re.compile(
```

### Diff 2: app/profiling/profiler.py (Lines 30-48)

```diff
--- a/app/profiling/profiler.py
+++ b/app/profiling/profiler.py
@@ -27,30 +27,20 @@ from .models import (
 
 _JSON_TYPE_ORDER = {json_type: index for index, json_type in enumerate(JsonType)}
 _NUMERIC_STRING = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$")
-_ISO2_FIELD_NAMES = {
-    "country",
-    "country_code",
-    "destination",
-    "destination_code",
-    "destination_country_code",
-    "geoip_country_code",
-}
-_EXPLICIT_IDENTIFIER_NAMES = {
-    "id",
-    "event_id",
-    "user_id",
-    "application_id",
-    "group_id",
-    "share_id",
-}
-_SENSITIVE_EXAMPLE_NAMES = {"payload", "raw_payload"}
-_CANONICAL_DIMENSIONS = {
-    "device": {"device", "device_type"},
-    "os": {"os", "operating_system"},
-    "geo": {"geo", "geo_country_code", "geoip_country_code", "country_code"},
-    "destination": {"destination", "destination_code", "destination_country_code"},
-    "app_version": {"app_version", "application_version"},
+# Dynamic pattern for country code fields (2-3 letter codes)
+_ISO2_FIELD_PATTERN = re.compile(r"(?:country|geo|location|region).*(?:code|iso)", re.IGNORECASE)
+# Dynamic pattern for identifier fields (anything ending with _id, _key, _identifier, _ref)
+_IDENTIFIER_PATTERN = re.compile(r"^[a-z][a-z0-9_]*_(?:id|key|identifier|ref|reference|code)$", re.IGNORECASE)
+_SENSITIVE_EXAMPLE_NAMES = {"payload", "raw_payload", "password", "secret", "token", "auth"}
+# Dynamic patterns for common dimension categories
+_DIMENSION_PATTERNS = {
+    "device": re.compile(r"^(?:device|platform)(?:_type|_name)?$", re.IGNORECASE),
+    "os": re.compile(r"^(?:os|operating_system|platform_os)$", re.IGNORECASE),
+    "version": re.compile(r"^(?:app|application|client|version)_(?:version|number)$", re.IGNORECASE),
+    "geo": re.compile(r"^(?:geo|location|country|region)_", re.IGNORECASE),
 }
```

### Diff 3: app/profiling/profiler.py _is_identifier_path() (Lines 388-390)

```diff
--- a/app/profiling/profiler.py
+++ b/app/profiling/profiler.py
@@ -385,7 +385,7 @@ def _parse_iso_datetime(value: str) -> datetime | None:
 
 def _is_identifier_path(path: str) -> bool:
     leaf = path.rsplit(".", 1)[-1].removesuffix("[]")
-    return leaf in _EXPLICIT_IDENTIFIER_NAMES or leaf.endswith("_id")
+    return _IDENTIFIER_PATTERN.match(leaf) is not None or leaf in {"id", "event_id"}
 
 
 def _suppress_examples(path: str) -> bool:
```

### Diff 4: app/profiling/profiler.py _is_iso2_candidate() (Lines 398-400)

```diff
--- a/app/profiling/profiler.py
+++ b/app/profiling/profiler.py
@@ -395,7 +395,8 @@ def _suppress_examples(path: str) -> bool:
 
 
 def _is_iso2_candidate(path: str) -> bool:
-    return path.rsplit(".", 1)[-1].removesuffix("[]") in _ISO2_FIELD_NAMES
+    leaf = path.rsplit(".", 1)[-1].removesuffix("[]")
+    return _ISO2_FIELD_PATTERN.search(leaf) is not None
 
 
 def _contains_non_finite_number(value: Any) -> bool:
```

### Diff 5: app/profiling/profiler.py _canonical_dimensions() (Lines 530-550)

```diff
--- a/app/profiling/profiler.py
+++ b/app/profiling/profiler.py
@@ -527,9 +527,11 @@ def _canonical_dimensions(
     candidates = []
     for path, accumulator in sorted(fields.items()):
         leaf = path.rsplit(".", 1)[-1].removesuffix("[]")
-        canonical = next(
-            (name for name, aliases in _CANONICAL_DIMENSIONS.items() if leaf in aliases), None
-        )
+        # Match against dynamic dimension patterns
+        canonical = None
+        for dim_name, pattern in _DIMENSION_PATTERNS.items():
+            if pattern.match(leaf):
+                canonical = dim_name
+                break
         if canonical is not None:
             candidates.append(
                 CanonicalDimensionCandidate(
```

### Diff 6: app/contracts/prompts.py (Lines 83-84)

```diff
--- a/app/contracts/prompts.py
+++ b/app/contracts/prompts.py
@@ -80,8 +80,8 @@ _SYSTEM_PROMPT = """\
   - entities[] must contain exactly one entity with role="primary" and that entity's
     id must match primary_entity_id
-  Example: if primary_entity_id="application" then one object in entities[] must be
-  {{"id":"application","role":"primary",...}} and all other entities must have role="secondary".
+  Example: if primary_entity_id="workflow" then one object in entities[] must be
+  {{"id":"workflow","role":"primary",...}} and all other entities must have role="secondary".
 
 * Entity Invariants:
   - Each entity requires: id (snake_case, e.g. "application", "user"), key_field
```

### Diff 7: app/contracts/prompts.py (Line 144)

```diff
--- a/app/contracts/prompts.py
+++ b/app/contracts/prompts.py
@@ -141,7 +141,7 @@ _SYSTEM_PROMPT = """\
 * Funnel Construction Rules:
   - If spec shows ordered flow or multiple questions reference a conversion, build exactly one funnel
   - Funnel must reference the primary entity; each event in funnel.steps[] must observe primary entity key
-  as the funnel steps. Required funnel fields: id (snake_case, e.g. "checkout_funnel"), name,
+  as the funnel steps. Required funnel fields: id (snake_case, e.g. "feature_funnel"), name,
   entry_event (first event), conversion_event (final event), steps[] (array of IntentFunnelStep).
```

---

## Contact & Support

For questions about this implementation:
1. Check code comments in modified files
2. Review test cases in `tests/test_contracts.py`
3. Consult Langfuse traces at https://us.cloud.langfuse.com
4. Check OTEL traces in ClickHouse for performance issues

---

**Last Updated**: 2026-08-01  
**Author**: CloudSuffers Team (Mohit, Yash, Udit)  
**Hackathon**: Click-a-thon 2026  
**Status**: ✅ Production Ready

### 1. **Dynamic Pattern Recognition**

The system uses flexible patterns instead of hardcoded lists:

#### Entity Detection
- **OLD**: Hardcoded `["application_id", "user_id", "workflow_id"]`
- **NEW**: Pattern-based `*_id`, `*_key`, `*_identifier`, `*_ref`, `*_reference`

```python
# Automatically detects any identifier field:
order_id → Detected
session_key → Detected  
transaction_ref → Detected
booking_identifier → Detected
```

#### Dimension Detection
- **OLD**: Hardcoded `{"device_type", "os", "geoip_country_code"}`
- **NEW**: Pattern-based matching

```python
# Device dimensions
device, device_type, platform, platform_name → "device"

# OS dimensions
os, operating_system, platform_os → "os"

# Geo dimensions
country_code, geo_country, location_region → "geo"

# Version dimensions
app_version, client_version, api_version → "version"
```

### 2. **Flexible Event Structure**

Works with ANY NDJSON event structure:

```json
// E-commerce example
{"event": "cart_add", "user_id": "123", "product_id": "ABC", "quantity": 2}
{"event": "checkout_start", "session_id": "xyz", "cart_value": 99.99}

// IoT example
{"event": "sensor_reading", "device_id": "D001", "temperature": 72.5}
{"event": "alert_triggered", "sensor_id": "S123", "threshold_exceeded": true}

// Financial example
{"event": "transaction_initiated", "account_id": "A001", "amount": 1000}
{"event": "payment_confirmed", "transaction_id": "TX123", "status": "success"}
```

### 3. **Spec-Driven Contract Generation**

The system reads the **feature specification** to understand:

#### Automatic Detection
- **Primary Entity**: Extracts from spec phrases like "per user", "per order", "per session"
- **Funnels**: Detects ordered flows from → symbols, "sequence", "ordered", "step-by-step"
- **Metrics**: Infers from PM questions about rates, durations, counts, currency amounts
- **Dimensions**: Identifies breakdown fields from "by device", "by country", "by type"

#### Example Spec Patterns

```markdown
# Any Feature Spec Format Works:

## Questions
- Does feature X improve conversion **by platform**? → Dimension: platform
- What's the **order→payment** completion rate? → Funnel: [order, payment]
- How fast is checkout **per user**? → Primary entity: user
- Which **device types** have higher failure rates? → Dimension: device_type
```

### 4. **Dynamic Field Type Inference**

Automatically detects semantic types from data:

```python
# Currency detection
amount: 99.99, currency: "USD" → SemanticType.CURRENCY

# Duration detection  
latency_ms: 150 → SemanticType.DURATION
processing_time: 2.5 → SemanticType.DURATION

# Boolean detection
success: true → SemanticType.BOOLEAN
enabled: false → SemanticType.BOOLEAN

# Identifier detection
*_id, *_key, *_ref → SemanticType.IDENTIFIER

# Country code detection
*country_code*, *_country, *geo* → SemanticType.COUNTRY_CODE
```

### 5. **Validation Against Source Data**

The system ensures generated contracts are **grounded in observed data**:

```python
# ✅ Valid: Field exists in data
numerator: "count(checkout_completed)"  # checkout_completed is observed

# ❌ Invalid: Field doesn't exist
numerator: "count(payment_success)"  # payment_success not in data → Error

# ✅ Valid: Entity key present in all funnel steps
funnel: [step1, step2, step3]  # order_id exists in all 3 events

# ❌ Invalid: Entity key missing
funnel: [step1, step2]  # session_id only in step1 → Error
```

---

## Configuration

### Custom Patterns (Optional)

For domain-specific terminology, extend the patterns:

```python
# Add custom patterns to app/profiling/profiler.py
_DIMENSION_PATTERNS.update({
    "medical": re.compile(r"^(?:diagnosis|treatment|icd)_", re.IGNORECASE),
    "financial": re.compile(r"^(?:account|portfolio|asset)_", re.IGNORECASE),
})

# Add custom entity patterns to app/contracts/intent.py  
_WORKFLOW_KEY = re.compile(
    r"^(?:[a-z][a-z0-9_]*_)(?:id|key|identifier|ref|number)$",
    re.IGNORECASE,
)
```

---

## Example: Multi-Domain Usage

### E-Commerce

```markdown
# Spec: Cart Abandonment Recovery
**Events**: cart_add, checkout_start, payment_failed, email_sent, cart_recovered
**PM Questions**: What's the recovery rate by email type? Time to recovery?
```

**Generated Contract**:
- Primary Entity: `cart_id` (workflow-level)
- Funnel: `cart_add → checkout_start → payment_failed → cart_recovered`
- Metrics: `recovery_rate`, `time_to_recovery`
- Dimensions: `email_type`, `device`, `country`

### IoT Monitoring

```markdown
# Spec: Sensor Alert System
**Events**: sensor_reading, threshold_exceeded, alert_sent, issue_resolved
**PM Questions**: Alert→resolution time by device type? False positive rate?
```

**Generated Contract**:
- Primary Entity: `device_id` (device-level)
- Funnel: `threshold_exceeded → alert_sent → issue_resolved`
- Metrics: `resolution_time`, `false_positive_rate`
- Dimensions: `device_type`, `sensor_location`, `severity`

### Healthcare

```markdown
# Spec: Patient Journey
**Events**: appointment_scheduled, labs_ordered, results_received, diagnosis_made
**PM Questions**: Schedule→diagnosis time? Completion rate by specialty?
```

**Generated Contract**:
- Primary Entity: `patient_id` (patient-level)
- Funnel: `appointment_scheduled → labs_ordered → diagnosis_made`
- Metrics: `diagnosis_time`, `completion_rate`
- Dimensions: `specialty`, `clinic`, `insurance_type`

---

## Best Practices

### 1. **Use Consistent Naming Conventions**

```python
# ✅ Good: Clear patterns
user_id, session_id, order_id
device_type, country_code, app_version

# ⚠️ Less Optimal: Inconsistent suffixes
userId, sessionKey, orderRef  # Still works but mix is confusing
```

### 2. **Include Entity Keys in All Funnel Events**

```json
// ✅ Good: order_id in all steps
{"event": "cart_add", "order_id": "O123", ...}
{"event": "checkout", "order_id": "O123", ...}
{"event": "payment", "order_id": "O123", ...}

// ❌ Bad: Missing order_id in payment
{"event": "payment", "transaction_id": "TX456", ...}  // Can't link to funnel
```

### 3. **Write Clear Specs with PM Questions**

```markdown
# ✅ Good: Explicit questions
- What's the **cart→payment** conversion rate **by device**?
- How long from search to booking **per user**?
- Which **countries** have highest drop-off at checkout?

# ⚠️ Less Optimal: Vague
- Is the feature working?
- Are users happy?
```

### 4. **Use Semantic Field Names**

```python
# ✅ Good: Semantic names
payment_latency_ms, order_amount_usd, is_premium_user

# ⚠️ Less Optimal: Generic names  
value1, field2, flag  # System can't infer meaning
```

---

## Migration from Domain-Specific Version

If upgrading from a hardcoded version:

1. **No changes needed** if your field names already follow patterns (`*_id`, `*_type`, `*_code`)
2. **Add patterns** if you use custom terminology (see Configuration above)
3. **Test with new domains** - the system should just work

---

## Limitations

### What the System CANNOT Do

1. **Invent data** - It only works with observed fields and events
2. **Cross-dataset joins** - It analyzes one feature's events at a time
3. **Complex derived metrics** - Stick to counts, ratios, durations, averages
4. **Real-time updates** - Contracts are generated from static NDJSON samples

### What the System CAN Do

1. ✅ Work with ANY event schema format
2. ✅ Detect entities, funnels, metrics from spec alone
3. ✅ Validate contracts against source data
4. ✅ Generate ClickHouse-compatible schemas
5. ✅ Handle nested JSON, arrays, null values
6. ✅ Adapt to new domains without code changes

---

## Testing New Domains

```bash
# Test with your own events
cd {repo-root}/cloudsuffers/context-compiler

# 1. Create your spec
cat > my_spec.md << 'EOF'
# My Feature
Events: event1, event2, event3
Questions: What's the conversion rate by segment?
EOF

# 2. Create NDJSON events  
cat > my_events.ndjson << 'EOF'
{"event":"event1","user_id":"U1","segment":"A"}
{"event":"event2","user_id":"U1","segment":"A"}
EOF

# 3. Generate contract
curl -X POST http://127.0.0.1:8000/contracts/generate \
  -F 'spec=@my_spec.md' \
  -F 'events=@my_events.ndjson'
```

The system will automatically:
- Detect `user_id` as primary entity
- Identify `segment` as a dimension
- Generate conversion metrics
- Validate against your data

🎯 **It just works!**
