#!/usr/bin/env python3
"""Traceable geometry-delta accounting for certified solver twins."""

from __future__ import annotations

import math


DELTA_SCHEMA = "repairmach.geometry-deltas/1.0"


def _component_map(inventory: dict) -> dict[str, dict]:
    return {item["id"]: item for item in inventory.get("components", [])}


def _bbox_delta(before: dict, after: dict, cref: float) -> dict:
    before_box = before.get("bbox", {})
    after_box = after.get("bbox", {})
    before_min = before_box.get("min", [0.0, 0.0, 0.0])
    before_max = before_box.get("max", [0.0, 0.0, 0.0])
    after_min = after_box.get("min", [0.0, 0.0, 0.0])
    after_max = after_box.get("max", [0.0, 0.0, 0.0])
    changes = [abs(float(a) - float(b)) for a, b in zip(before_min + before_max, after_min + after_max)]
    maximum = max(changes, default=0.0)
    return {
        "max_coordinate_delta": maximum,
        "max_coordinate_delta_over_cref": maximum / cref if cref > 0.0 else None,
        "before": before_box,
        "after": after_box,
    }


def compute_geometry_deltas(
    before_inventory: dict,
    after_inventories: dict[str, dict],
    actions: list[dict],
    reference: dict,
    exclusions: list[dict] | None = None,
) -> dict:
    """Record measurable deltas without inventing unavailable quantities."""
    cref = float(reference["cref"])
    before = _component_map(before_inventory)
    twins: dict[str, dict] = {}
    for twin_name, inventory in after_inventories.items():
        after = _component_map(inventory)
        component_deltas = []
        for geom_id in sorted(set(before) | set(after)):
            if geom_id not in before or geom_id not in after:
                component_deltas.append({
                    "geom_id": geom_id,
                    "name": (before.get(geom_id) or after.get(geom_id) or {}).get("name"),
                    "presence_changed": True,
                })
                continue
            delta = _bbox_delta(before[geom_id], after[geom_id], cref)
            delta.update({
                "geom_id": geom_id,
                "name": before[geom_id].get("name"),
                "presence_changed": False,
            })
            component_deltas.append(delta)
        twins[twin_name] = {
            "component_count_before": len(before),
            "component_count_after": len(after),
            "connected_region_delta": "not_available_from_vsp_api_inventory",
            "surface_count_before": sum((item.get("total_surfaces") or 0) for item in before.values()),
            "surface_count_after": sum((item.get("total_surfaces") or 0) for item in after.values()),
            "components": component_deltas,
            "max_bbox_displacement_over_cref": max(
                (
                    float(item.get("max_coordinate_delta_over_cref") or 0.0)
                    for item in component_deltas
                ),
                default=0.0,
            ),
        }

    parameter_actions = [item for item in actions if item.get("action") == "set_parameter"]
    set_actions = [item for item in actions if item.get("action") == "set_assignment"]
    estimated_area_delta = sum(
        float(item.get("area_delta_fraction_estimate", 0.0)) for item in parameter_actions
    )
    max_parameter_displacement = max(
        (float(item.get("normalized_delta", 0.0)) for item in parameter_actions),
        default=0.0,
    )
    return {
        "schema": DELTA_SCHEMA,
        "reference": reference,
        "summary": {
            "changed_openvsp_parameters": len(parameter_actions),
            "changed_set_assignments": len(set_actions),
            "estimated_delta_s_over_sref": estimated_area_delta,
            "delta_v_over_v": None,
            "delta_b_over_b": None,
            "delta_c_over_c": None,
            "cg_shift_over_cref": 0.0,
            "max_parameter_displacement_over_cref": max_parameter_displacement,
            "changed_vertices": None,
            "changed_panel_fraction": None,
            "added_faces": None,
            "removed_faces": None,
            "note": (
                "Unavailable values are explicitly null. They must be populated from a TRI/degen "
                "comparison before being used as acceptance evidence."
            ),
        },
        "actions": actions,
        "exclusions": exclusions or [],
        "twins": twins,
    }


def delta_within_policy(delta: dict, policy: dict) -> tuple[bool, list[str]]:
    limits = policy["regularization"]["budgets"]
    summary = delta["summary"]
    errors: list[str] = []
    checks = (
        ("estimated_delta_s_over_sref", "max_area_delta_fraction"),
        ("max_parameter_displacement_over_cref", "max_displacement_over_cref"),
    )
    for field, limit_field in checks:
        value = summary.get(field)
        limit = float(limits[limit_field])
        if value is not None and (not math.isfinite(float(value)) or float(value) > limit):
            errors.append(f"{field}={value} превышает {limit_field}={limit}")
    return not errors, errors
