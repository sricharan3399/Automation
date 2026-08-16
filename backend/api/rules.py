"""Validation rule catalogue and confidence policy."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, status

from backend.api.deps import require
from backend.auth.identity import Identity
from backend.auth.roles import Permission
from backend.configstore import get_config_store
from backend.recommendations.confidence import describe_policy
from backend.validation.registry import implemented_rule_ids

router = APIRouter(prefix="/rules", tags=["rules"])


@router.get("")
def list_rules() -> dict[str, Any]:
    store = get_config_store()
    implemented = implemented_rule_ids()
    rules = []
    for definition in store.rules():
        payload = definition.to_dict()
        payload["implemented"] = definition.id in implemented or definition.category == "CSV"
        payload["state"] = (
            "AWAITING APPROVED PROJECT THRESHOLD"
            if definition.awaiting_project_threshold
            else ("ENABLED" if definition.enabled else "DISABLED")
        )
        rules.append(payload)

    categories: dict[str, int] = {}
    for definition in store.rules():
        categories[definition.category] = categories.get(definition.category, 0) + 1

    return {
        "catalogue_version": store.rule_catalogue_version,
        "rule_version": store.rule_version_signature(),
        "categories": categories,
        "rules": rules,
        "summary": {
            "total": len(rules),
            "enabled": sum(1 for r in rules if r["enabled"]),
            "awaiting_project_threshold": sum(1 for r in rules if r["awaiting_project_threshold"]),
            "not_implemented": sum(1 for r in rules if not r["implemented"]),
        },
        "note": (
            "Rules whose threshold comes from the project ship disabled until an approved value is "
            "supplied. The platform does not invent safety-relevant thresholds."
        ),
    }


@router.get("/confidence-policy")
def confidence_policy() -> dict[str, Any]:
    return describe_policy()


@router.get("/{rule_id}")
def rule_detail(rule_id: str) -> dict[str, Any]:
    definition = get_config_store().rule(rule_id)
    if definition is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"No rule '{rule_id}'.")
    payload = definition.to_dict()
    payload["implemented"] = rule_id in implemented_rule_ids() or definition.category == "CSV"
    return payload


@router.post("/reload")
def reload_rules(identity: Identity = require(Permission.MANAGE_RULES)) -> dict[str, Any]:
    """Re-read the YAML configuration without restarting the service."""
    store = get_config_store()
    store.reload()
    return {
        "reloaded": True,
        "catalogue_version": store.rule_catalogue_version,
        "rule_count": len(store.rules()),
        "reloaded_by": identity.user,
    }
