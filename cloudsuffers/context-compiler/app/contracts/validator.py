import re
from dataclasses import dataclass

from app.contracts.models import AnalyticsContract
from app.profiling.models import SourceProfile

_EXECUTABLE = re.compile(
    r"\b(?:select\b[^\n]+\bfrom|insert\s+into|update\s+\S+\s+set|delete\s+from|"
    r"drop\s+(?:table|database|view)|truncate\s+table|alter\s+table|"
    r"create\s+(?:table|database|view)|attach\s+table|detach\s+table|"
    r"grant\s+\S+|revoke\s+\S+|system\s+\S+|rm\s+-[a-z]*[rf]|"
    r"curl\s+https?://|wget\s+https?://|(?:bash|sh|python|powershell)\s+-[a-z])\b|"
    r"(?:reveal|print|expose|return|show).{0,40}(?:system prompt|environment variables?|"
    r"credentials?|api[_ ]?keys?|filesystem data)|--|/\*|\*/|```|\$\(|&&|\|\|",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class GroundingError:
    code: str
    path: str
    message: str

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "path": self.path, "message": self.message}


def validate_contract_grounding(
    contract: AnalyticsContract,
    source_profile: SourceProfile,
    feature_spec: str,
    *,
    spec_sha256: str,
    expected_feature_slug: str,
) -> list[GroundingError]:
    errors: list[GroundingError] = []

    if contract.source.spec_sha256 != spec_sha256:
        errors.append(
            GroundingError(
                "source_mismatch",
                "source.spec_sha256",
                "contract specification checksum does not match the supplied specification",
            )
        )
    if contract.feature.slug != expected_feature_slug:
        errors.append(
            GroundingError(
                "feature_slug_mismatch",
                "feature.slug",
                f"feature slug must be {expected_feature_slug!r}",
            )
        )

    profile_fields = {field.path: field for field in source_profile.fields}
    identifier_fields = {
        candidate.field_path: candidate for candidate in source_profile.candidate_identifiers
    }
    contract_fields = {field.source_path: field for field in contract.fields}

    for index, field in enumerate(contract.fields):
        path = f"fields.{index}"
        observed = profile_fields.get(field.source_path)
        if field.spec_only:
            if not _mentioned_in_spec(feature_spec, field.source_path, field.name):
                errors.append(
                    GroundingError(
                        "invented_spec_only_field",
                        path,
                        f"spec_only field {field.source_path!r} is not named by the specification",
                    )
                )
            if field.observed_null_rate is not None:
                errors.append(
                    GroundingError(
                        "spec_only_observation",
                        f"{path}.observed_null_rate",
                        "spec_only fields cannot claim an observed null rate",
                    )
                )
        elif observed is not None and field.observed_null_rate != observed.null_rate:
            errors.append(
                GroundingError(
                    "null_rate_mismatch",
                    f"{path}.observed_null_rate",
                    f"observed_null_rate must equal SourceProfile value {observed.null_rate}",
                )
            )

    for index, event in enumerate(contract.events):
        if event.spec_only and not _mentioned_in_spec(feature_spec, event.name):
            errors.append(
                GroundingError(
                    "invented_spec_only_event",
                    f"events.{index}",
                    f"spec_only event {event.name!r} is not named by the specification",
                )
            )

    primary = next(entity for entity in contract.entities if entity.name == contract.primary_entity)
    primary_field = contract_fields[primary.field_path]
    if primary.field_path not in identifier_fields or primary_field.spec_only:
        errors.append(
            GroundingError(
                "unobserved_primary_entity",
                "primary_entity",
                "primary entity must use an observed identifier field",
            )
        )

    for funnel_index, funnel in enumerate(contract.funnels):
        field_profile = profile_fields.get(funnel.entity_key)
        required_events = {
            step.event_name
            for step in funnel.steps
            if not _event_spec_only(contract, step.event_name)
        }
        if funnel.entity_key not in identifier_fields or field_profile is None:
            errors.append(
                GroundingError(
                    "unstable_funnel_entity",
                    f"funnels.{funnel_index}.entity_key",
                    "funnel entity key must be an observed identifier",
                )
            )
        elif identifier_fields[funnel.entity_key].uniqueness_ratio >= 1:
            errors.append(
                GroundingError(
                    "unstable_funnel_entity",
                    f"funnels.{funnel_index}.entity_key",
                    "funnel entity key is unique per row and cannot link multiple steps",
                )
            )
        elif not required_events.issubset(set(field_profile.observed_in_events)):
            missing = sorted(required_events - set(field_profile.observed_in_events))
            errors.append(
                GroundingError(
                    "unstable_funnel_entity",
                    f"funnels.{funnel_index}.entity_key",
                    f"funnel entity key is not observed in every step: {missing}",
                )
            )

    errors.extend(_validate_semantic_text(contract))
    return errors


def contract_warnings(contract: AnalyticsContract) -> list[str]:
    warnings = [
        f"field {field.source_path!r} is specification-only and was not observed"
        for field in contract.fields
        if field.spec_only
    ]
    warnings.extend(
        f"event {event.name!r} is specification-only and was not observed"
        for event in contract.events
        if event.spec_only
    )
    warnings.extend(
        f"blocking assumption: {assumption.statement}"
        for assumption in contract.assumptions
        if assumption.blocking
    )
    warnings.extend(
        f"unsupported question: {question.question}"
        for question in contract.open_questions
        if question.blocking
    )
    return warnings


def _event_spec_only(contract: AnalyticsContract, event_name: str) -> bool:
    return next(event.spec_only for event in contract.events if event.name == event_name)


def _mentioned_in_spec(feature_spec: str, *names: str) -> bool:
    folded = feature_spec.casefold()
    for name in names:
        variants = {name.casefold(), name.rsplit(".", 1)[-1].casefold()}
        variants.add(name.replace("_", " ").casefold())
        if any(variant and variant in folded for variant in variants):
            return True
    return False


def _validate_semantic_text(contract: AnalyticsContract) -> list[GroundingError]:
    values = [
        ("feature.objective", contract.feature.objective),
        ("grain", contract.grain),
    ]
    values.extend(
        (f"entities.{index}.description", item.description)
        for index, item in enumerate(contract.entities)
    )
    values.extend(
        (f"events.{index}.description", item.description)
        for index, item in enumerate(contract.events)
    )
    values.extend(
        (f"fields.{index}.description", item.description)
        for index, item in enumerate(contract.fields)
        if item.description is not None
    )
    values.extend(
        (f"fields.{index}.clickhouse_type", item.clickhouse_type)
        for index, item in enumerate(contract.fields)
    )
    values.extend(
        (f"metrics.{index}.description", item.description)
        for index, item in enumerate(contract.metrics)
    )
    values.extend(
        (f"metrics.{index}.numerator", item.numerator)
        for index, item in enumerate(contract.metrics)
    )
    values.extend(
        (f"metrics.{index}.denominator", item.denominator)
        for index, item in enumerate(contract.metrics)
    )
    values.extend(
        (f"funnels.{index}.description", item.description)
        for index, item in enumerate(contract.funnels)
        if item.description is not None
    )
    values.extend(
        (f"dimensions.{index}.description", item.description)
        for index, item in enumerate(contract.dimensions)
    )
    values.extend(
        (f"relationships.{index}.description", item.description)
        for index, item in enumerate(contract.relationships)
    )
    values.extend(
        (f"relationships.{index}.temporal_constraint", item.temporal_constraint)
        for index, item in enumerate(contract.relationships)
        if item.temporal_constraint is not None
    )
    values.extend(
        (f"data_quality_rules.{index}.description", item.description)
        for index, item in enumerate(contract.data_quality_rules)
    )
    values.extend(
        (f"data_quality_rules.{index}.expression", item.expression)
        for index, item in enumerate(contract.data_quality_rules)
    )
    values.extend(
        (f"observations.{index}.statement", item.statement)
        for index, item in enumerate(contract.observations)
    )
    values.extend(
        (f"assumptions.{index}.statement", item.statement)
        for index, item in enumerate(contract.assumptions)
    )
    values.extend(
        (f"assumptions.{index}.rationale", item.rationale)
        for index, item in enumerate(contract.assumptions)
    )
    values.extend(
        (f"open_questions.{index}.question", item.question)
        for index, item in enumerate(contract.open_questions)
    )

    return [
        GroundingError(
            "executable_content",
            path,
            "semantic text must not contain SQL, code fences, or executable instructions",
        )
        for path, value in values
        if _EXECUTABLE.search(value)
    ]
