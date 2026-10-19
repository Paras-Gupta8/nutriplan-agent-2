"""Meal Planner Agent - deterministic 7-day plan generation from templates (no LLM)."""
from __future__ import annotations

from time import perf_counter

import pandas as pd

from core.meal_engine import generate_meal_plan, load_foods, load_templates
from core.revision import MAX_REVISIONS, revise_plan
from core.state import log_event

AGENT_NAME = "Meal Planner Agent"


def run_meal_planner_agent(targets: dict, execution_plan: dict, variation: int = 0,
                           foods: pd.DataFrame | None = None, templates: list | None = None) -> dict:
    foods = load_foods() if foods is None else foods
    templates = load_templates() if templates is None else templates
    c = execution_plan["constraints"]

    t0 = perf_counter()
    plan = generate_meal_plan(targets, c, foods, templates, variation)
    plan["generation_ms"] = round((perf_counter() - t0) * 1000, 1)

    if not plan["days"]:
        log_event(AGENT_NAME, "generate_meal_plan",
                  input_summary=f"{targets['calories']} kcal target, {c['diet']}, {c['cuisine']}",
                  output_summary="No plan could be generated", status="failed", details="\n".join(plan["warnings"]))
        return plan

    w = plan["weekly"]
    subs = plan["substitutions"]
    sub_txt = "; ".join(f"Day {s['day']} {s['slot']}: {s['original']} → {s['replacement']} ({s['reason']})" for s in subs[:12])
    log_event(
        AGENT_NAME,
        "generate_meal_plan",
        input_summary=f"Target {targets['calories']} kcal / {targets['protein_g']} g protein | {c['diet']} | {c['cuisine']} | "
                      f"₹{c['budget_weekly_inr']}/wk | {plan['eligible_foods']} eligible foods | variation {variation}",
        output_summary=f"7 days from {len({d['template_id'] for d in plan['days']})} templates; avg {w['avg_calories']} kcal, "
                       f"{w['avg_protein_g']} g protein; ₹{w['cost_inr']:.0f} of ₹{w['budget_inr']}; {len(subs)} substitution(s)",
        status="warning" if plan["warnings"] else "success",
        details="\n".join([
            f"Generated in {plan['generation_ms']} ms",
            *[f"Day {d['day']}: {d['rationale']}" for d in plan["days"]],
            *(["Substitutions: " + sub_txt] if subs else []),
            *[f"WARNING: {x}" for x in plan["warnings"]],
        ]),
    )
    return plan


def run_meal_planner_revision(plan: dict, report: dict, targets: dict, constraints: dict, attempt: int,
                              foods: pd.DataFrame | None = None) -> tuple[dict, list[dict]]:
    """Revise the plan using the Validator's structured feedback (deterministic, no LLM)."""
    foods = load_foods() if foods is None else foods
    new_plan, actions = revise_plan(plan, report["issues"], targets, constraints, foods, attempt)
    checks = sorted({i["check"] for i in report["issues"]})
    log_event(
        AGENT_NAME, f"revise_meal_plan (attempt {attempt}/{MAX_REVISIONS})",
        input_summary=f"{len(report['issues'])} issue(s) from Validator in: {', '.join(checks)}",
        output_summary=f"{len(actions)} change(s) applied; weekly cost ₹{new_plan['weekly']['cost_inr']:.0f}, "
                       f"avg {new_plan['weekly']['avg_protein_g']} g protein",
        status="success" if actions else "warning",
        details="\n".join([f"Feedback: {i['problem']}" for i in report["issues"]] + [""] + [f"- {a['description']}" for a in actions]
                          if actions else ["No applicable fix was found for the reported issues."]),
    )
    return new_plan, actions
