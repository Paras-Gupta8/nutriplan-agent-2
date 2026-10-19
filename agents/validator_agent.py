"""Validator / Critic Agent - pure-Python checks, structured feedback, never calls an LLM."""
from __future__ import annotations

import pandas as pd

from core.meal_engine import load_foods
from core.state import log_event
from core.validator import OVERALL_LABEL, STATUS_ICON, validate_plan

AGENT_NAME = "Validator Agent"


def run_validator_agent(plan: dict, targets: dict, constraints: dict, attempt: int = 0, final: bool = False,
                        foods: pd.DataFrame | None = None, label: str | None = None) -> dict:
    foods = load_foods() if foods is None else foods
    report = validate_plan(plan, targets, constraints, foods)
    c = report["counts"]
    if report["overall_status"] == "passed":
        status = "success"
    elif report["overall_status"] == "passed_with_warnings":
        status = "warning"
    elif final:  # revisions exhausted: best available plan is shown with warnings
        status = "failed" if report["critical_failures"] else "warning"
    else:
        status = "revision_required"
    action = label or ("validate_plan" if attempt == 0 else f"revalidate_plan (after revision {attempt})")
    lines = [f"{STATUS_ICON[ch['status']]} {ch['name']}: {ch['message']}" for ch in report["checks"]]
    if report["feedback"]:
        lines += ["", "Feedback to Meal Planner:"] + [f"- {f['problem']} {f['suggestion']}" for f in report["feedback"]]
    log_event(
        AGENT_NAME, action,
        input_summary=f"{len(plan['days'])}-day plan (revision {plan.get('revision', 0)}) vs target {targets['calories']} kcal / {targets['protein_g']} g protein, "
                      f"₹{constraints['budget_weekly_inr']}/wk",
        output_summary=f"{OVERALL_LABEL[report['overall_status']]} - score {report['score']}/100; "
                       f"{c['pass']} pass, {c['warning']} warning, {c['fail']} fail"
                       + (f"; sending {len(report['feedback'])} issue(s) back for revision" if report["feedback"] and not final else ""),
        status=status, details="\n".join(lines),
    )
    return report
