"""Nutrition Agent - pure-Python calculation of calorie and macro targets with reasoning."""
from __future__ import annotations

from core.nutrition import compute_targets
from core.state import log_event

AGENT_NAME = "Nutrition Agent"


def build_targets(profile: dict) -> dict:
    """Pure function: profile -> targets + sanity warnings."""
    t = compute_targets(profile)
    warnings = []
    if t["calorie_floor_applied"]:
        warnings.append("Calorie target was raised to the safety minimum.")
    if t["bmi"] < 18.5 and profile["goal"] == "Fat loss":
        warnings.append("BMI indicates underweight; a fat-loss goal is not advisable.")
    if t["protein_g"] / profile["weight_kg"] > 2.0:
        warnings.append("Protein exceeds 2 g/kg; check suitability with a professional.")
    t["warnings"] = warnings
    return t


def run_nutrition_agent(profile: dict) -> dict:
    t = build_targets(profile)
    log_event(
        AGENT_NAME,
        "calculate_targets",
        input_summary=f"{profile['age']}y {profile['sex']}, {profile['height_cm']:g} cm, {profile['weight_kg']:g} kg, "
                      f"{profile['activity_level']}, goal={profile['goal']}",
        output_summary=f"BMR {t['bmr']} → TDEE {t['tdee']} → target {t['calories']} kcal; "
                       f"P {t['protein_g']} g, C {t['carbs_g']} g, F {t['fat_g']} g, fiber {t['fiber_g']} g",
        status="warning" if t["warnings"] else "success",
        details="\n".join([*t["reasoning"].values(), *[f"WARNING: {w}" for w in t["warnings"]]]),
    )
    return t
