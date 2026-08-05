"""AI output validation and safety controls.

Validates generated content before persisting drafts. Ensures non-empty output,
required sections are present, length limits are respected, and output is
properly labelled with generation metadata.

PII Handling Rules:
- Template engine uses only data explicitly provided in the Candidate model.
- AI-generated content is prompted with candidate-supplied facts only.
- No external data sources are queried to enrich candidate profiles.
- Drafts are stored per-user and never shared across accounts.
- Deletion of drafts is permanent (no soft-delete retention).
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ValidationResult:
    """Outcome of a content validation check."""

    valid: bool
    errors: list[str] = field(default_factory=list)


def validate_board_cv(content: str) -> ValidationResult:
    """Validate a board CV draft has required sections."""
    errors: list[str] = []

    if not content or not content.strip():
        errors.append("Board CV content is empty")
        return ValidationResult(valid=False, errors=errors)

    lower = content.lower()

    has_summary = any(
        term in lower
        for term in ["summary", "value proposition", "board value proposition"]
    )
    if not has_summary:
        errors.append("Board CV missing required section: Summary or value proposition")

    has_experience = any(
        term in lower
        for term in ["experience", "governance experience"]
    )
    if not has_experience:
        errors.append("Board CV missing required section: Experience or governance experience")

    return ValidationResult(valid=len(errors) == 0, errors=errors)


def validate_bio(content: str) -> ValidationResult:
    """Validate a director bio has an intro paragraph."""
    errors: list[str] = []

    if not content or not content.strip():
        errors.append("Bio content is empty")
        return ValidationResult(valid=False, errors=errors)

    stripped = content.strip()
    if "." not in stripped:
        errors.append("Bio missing intro paragraph (no complete sentence found)")

    if len(stripped.split()) < 10:
        errors.append("Bio too short to constitute an intro paragraph")

    return ValidationResult(valid=len(errors) == 0, errors=errors)


def validate_outreach(content: str) -> ValidationResult:
    """Validate an outreach message has a greeting and an ask."""
    errors: list[str] = []

    if not content or not content.strip():
        errors.append("Outreach message content is empty")
        return ValidationResult(valid=False, errors=errors)

    lower = content.lower()

    has_greeting = any(
        term in lower for term in ["dear ", "hi ", "hello "]
    )
    if not has_greeting:
        errors.append("Outreach message missing greeting (Dear/Hi/Hello)")

    has_ask = any(
        term in lower
        for term in ["interest", "conversation", "discuss", "welcome", "connect"]
    )
    if not has_ask:
        errors.append("Outreach message missing ask/call-to-action")

    return ValidationResult(valid=len(errors) == 0, errors=errors)


def validate_length(content: str, max_chars: int = 5000) -> ValidationResult:
    """Validate content does not exceed the maximum character limit."""
    if not content:
        return ValidationResult(valid=True, errors=[])

    if len(content) > max_chars:
        return ValidationResult(
            valid=False,
            errors=[f"Content exceeds maximum length ({len(content)}/{max_chars} chars)"],
        )
    return ValidationResult(valid=True, errors=[])


def validate_generated_label(engine: str) -> ValidationResult:
    """Validate that AI output is labelled with engine information."""
    valid_engines = {"template", "azure-openai", "azure_openai"}
    if not engine or engine.strip().lower() not in valid_engines:
        return ValidationResult(
            valid=False,
            errors=[f"Output must be labelled with a valid engine (got: '{engine}')"],
        )
    return ValidationResult(valid=True, errors=[])




# Maximum prompt length in characters
MAX_PROMPT_LENGTH = 4000

# AI content label prefix
AI_GENERATED_LABEL = "[AI-generated content]"

# Prompt injection markers to detect in output
_INJECTION_MARKERS = [
    "ignore previous instructions",
    "disregard all prior",
    "system prompt:",
    "you are now",
    "<|im_start|>",
    "override safety",
]


def validate_prompt_length(prompt: str, max_chars: int = MAX_PROMPT_LENGTH) -> ValidationResult:
    """Validate that a prompt does not exceed the maximum length."""
    if len(prompt) > max_chars:
        return ValidationResult(
            valid=False,
            errors=[f"Prompt exceeds maximum length ({len(prompt)}/{max_chars} chars)"],
        )
    return ValidationResult(valid=True, errors=[])


def validate_candidate_facts(content: str, candidate_name: str) -> ValidationResult:
    """Ensure candidate name appears in output (facts not invented)."""
    if not candidate_name:
        return ValidationResult(valid=True, errors=[])
    if candidate_name.lower() not in content.lower():
        return ValidationResult(
            valid=False,
            errors=[f"Candidate name '{candidate_name}' not found in generated output"],
        )
    return ValidationResult(valid=True, errors=[])


def validate_no_prompt_injection(content: str) -> ValidationResult:
    """Detect prompt injection patterns in generated output."""
    lower = content.lower()
    for marker in _INJECTION_MARKERS:
        if marker in lower:
            return ValidationResult(
                valid=False,
                errors=[f"Potential prompt injection detected in output: '{marker}'"],
            )
    return ValidationResult(valid=True, errors=[])


def label_ai_output(content: str, engine: str) -> str:
    """Prepend AI-generated label when content comes from a model."""
    if engine != "template" and not content.startswith(AI_GENERATED_LABEL):
        return AI_GENERATED_LABEL + "\n\n" + content
    return content


def validate_draft(
    content: str, draft_type: str, engine: str, max_chars: int = 5000,
    candidate_name: str = "",
) -> ValidationResult:
    """Run all applicable validations for a draft."""
    all_errors: list[str] = []

    length_result = validate_length(content, max_chars)
    all_errors.extend(length_result.errors)

    label_result = validate_generated_label(engine)
    all_errors.extend(label_result.errors)

    if draft_type == "board_cv":
        type_result = validate_board_cv(content)
    elif draft_type == "director_bio":
        type_result = validate_bio(content)
    elif draft_type == "outreach":
        type_result = validate_outreach(content)
    else:
        type_result = ValidationResult(
            valid=False, errors=[f"Unknown draft type: {draft_type}"]
        )

    all_errors.extend(type_result.errors)

    # Prompt injection check
    injection_result = validate_no_prompt_injection(content)
    all_errors.extend(injection_result.errors)

    # Candidate fact preservation
    if candidate_name:
        facts_result = validate_candidate_facts(content, candidate_name)
        all_errors.extend(facts_result.errors)

    return ValidationResult(valid=len(all_errors) == 0, errors=all_errors)
