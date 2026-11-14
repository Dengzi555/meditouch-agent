"""Pharmacy graph state, independent of the legacy patient-facing graph."""
from typing import Literal, TypedDict

from meditouch.pharmacy_context import PharmacyWorkspace, PharmacyWorkflow


class PharmacyState(TypedDict, total=False):
    intent: Literal['find_drug', 'check_safety', 'emergency', 'chitchat']
    symptoms: list[str]
    medical_topics: list[str]
    catalog_discovered_names: list[str]
    denied_symptoms: list[str]
    target_drugs: list[str]
    history: list[dict]
    intake_status: str
    rag_attempts: int
    rag_source: str
    rag_evidence: list[dict]
    rag_excluded_drugs: list[str]
    retained_drugs: list[str]
    stockout_results: list[dict]
    unmatched_terms: list[str]
    combination_status: str
    user_query: str
    extracted_entities: dict
    customer_tags: list[str]
    inventory_results: list[dict]
    inventory_lookup_records: dict
    rag_recommended_drugs: list[str]
    has_tried_rag: bool
    rag_status: str
    safety_status: Literal['safe', 'unsafe', 'unknown']
    safety_reason: str
    excluded_drugs: list[str]
    excluded_reasons: dict[str, str]
    inventory_attempts: int
    safety_checks: list[dict]
    inventory_status: str
    inventory_truncated: bool
    answer: str
    outcome: str
    generation_status: str
    generation_branch: str
    staff_script: str
    ui_cards: list[dict]
    inventory_data_mode: Literal['real', 'mock']
    audience: str
    workspace: PharmacyWorkspace | None
    pharmacy_workflow: PharmacyWorkflow | None
    trace: list
    model_calls: int
    tool_calls: int
