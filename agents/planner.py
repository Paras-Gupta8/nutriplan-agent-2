"""Planner Agent - deterministic, rule-based. Converts a profile into a JSON execution plan."""
from __future__ import annotations

import pandas as pd

from core.meal_engine import (
    CUISINE_MAP, DIET_RULES, filter_foods, filter_funnel, load_foods, slot_pool_sizes,
)
from core.state import log_event

AGENT_NAME = "Planner Agent"
MIN_POOL_PER_SLOT = 3
TIGHT_DAILY_BUDGET_INR = 100

STEPS = [
    ("Calculate nutrition targets", "Nutrition Agent"),
    ("Filter restricted foods", "Meal Planner Agent"),
    ("Select suitable meal templates", "Meal Planner Agent"),
    ("Compose daily meals", "Meal Planner Agent"),
    ("Validate nutrition and budget", "Validator Agent"),
    ("Generate shopping list", "Meal Planner Agent"),
]


def build_constraints(profile: dict, dislikes=()) -> dict:
    """Translate profile choices into machine-usable constraints."""
    rules = DIET_RULES[profile["diet"]]
    excluded = sorted(set(profile["allergies"]) | set(rules["implicit_excluded_allergens"]))
    return {
        "diet": profile["diet"].lower(),
        "diet_rules": rules,
        "allergies": list(profile["allergies"]),
        "excluded_allergens": excluded,
        "budget_weekly_inr": profile["budget_weekly_inr"],
        "daily_budget_inr": round(profile["budget_weekly_inr"] / 7, 1),
        "cuisine": profile["cuisine"],
        "allowed_cuisines": CUISINE_MAP[profile["cuisine"]],
        "max_prep_time_min": profile["max_prep_time_min"],
        "goal": profile["goal"].lower(),
        "excluded_foods": sorted(set(dislikes or ())),
    }


def build_execution_plan(profile: dict, foods: pd.DataFrame, dislikes=None) -> dict:
    """Pure function: profile + food table -> execution plan (no logging, no Streamlit state)."""
    c = build_constraints(profile, dislikes or ())
    eligible = filter_foods(foods, c)
    pools = slot_pool_sizes(eligible)
    warnings = []
    for slot, n in pools.items():
        if n < MIN_POOL_PER_SLOT:
            warnings.append(f"Only {n} eligible food(s) for {slot}; the plan will be repetitive or incomplete.")
    if c["daily_budget_inr"] < TIGHT_DAILY_BUDGET_INR:
        warnings.append(f"Daily budget is only ₹{c['daily_budget_inr']:.0f}; meeting protein targets may be difficult.")

    rationale = [
        f"Goal '{profile['goal']}' selects the calorie adjustment and protein target in the Nutrition Agent.",
        rules_text(c),
        (f"Allergens excluded: {', '.join(c['excluded_allergens'])}." if c["excluded_allergens"]
         else "No allergens to exclude."),
        f"Cuisine '{profile['cuisine']}' also admits Pan-Indian dishes: {', '.join(c['allowed_cuisines'])}.",
        *([f"Foods you asked to avoid: {', '.join(c['excluded_foods'])}."] if c["excluded_foods"] else []),
        f"Dishes longer than {c['max_prep_time_min']} min of prep are excluded.",
        f"Weekly budget ₹{c['budget_weekly_inr']:,} gives about ₹{c['daily_budget_inr']:.0f} per day for the cost check.",
    ]
    return {
        "task": f"Generate a 7-day {c['diet']} {profile['cuisine']} meal plan",
        "steps": [s for s, _ in STEPS],
        "step_owners": {s: o for s, o in STEPS},
        "constraints": c,
        "feasibility": {
            "eligible_foods": len(eligible),
            "total_foods": len(foods),
            "slot_pools": pools,
            "filter_funnel": filter_funnel(foods, c),
            "warnings": warnings,
        },
        "rationale": rationale,
    }


def rules_text(c: dict) -> str:
    return c["diet_rules"]["description"]


def run_planner_agent(profile: dict, foods: pd.DataFrame | None = None, dislikes=None) -> dict:
    """Run the agent: build the plan and write a structured log entry."""
    foods = load_foods() if foods is None else foods
    plan = build_execution_plan(profile, foods, dislikes)
    f = plan["feasibility"]
    log_event(
        AGENT_NAME,
        "create_execution_plan",
        input_summary=f"{profile['goal']} | {profile['diet']} | {profile['cuisine']} | ₹{profile['budget_weekly_inr']}/wk | "
                      f"allergies={profile['allergies'] or 'none'} | ≤{profile['max_prep_time_min']} min",
        output_summary=f"{len(plan['steps'])}-step plan; {f['eligible_foods']}/{f['total_foods']} foods eligible",
        status="warning" if f["warnings"] else "success",
        details="\n".join(
            ["Filter funnel: " + " → ".join(f"{x['filter']} (-{x['removed']}, left {x['remaining']})" for x in f["filter_funnel"]),
             "Slot pools: " + ", ".join(f"{k}={v}" for k, v in f["slot_pools"].items())]
            + [f"WARNING: {w}" for w in f["warnings"]]
        ),
    )
    return plan
