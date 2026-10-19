"""Internal evaluation: metrics for the current session and a reproducible benchmark over many profiles."""
from __future__ import annotations

import itertools
import time
from statistics import mean

import pandas as pd

import core.adaptation as adaptation
from agents.planner import build_constraints
from core.meal_engine import generate_meal_plan
from core.nutrition import DEMO_PROFILE, compute_targets
from core.revision import revise_until_valid
from core.validator import validate_plan

DIETS = ["Vegetarian", "Eggetarian", "Non-vegetarian", "Vegan", "Jain"]
CUISINES = ["North Indian", "South Indian", "Mixed Indian"]
ALLERGY_SETS = [[], ["peanut"], ["dairy", "gluten"], ["soy", "tree_nuts"]]
BENCH_PHRASES = ["I skipped lunch", "I ate out for dinner", "Make the plan cheaper", "I don't like paneer",
                 "Make today high protein", "I have only 20 minutes to cook"]
HARD_CHECKS = ("allergens", "diet", "cooking_cuisine", "meals")      # constraints that must never be violated
BENCH_SIZES = {"demo": "Demo case only (1 profile)", "quick": "Quick (12 profiles)", "full": "Full matrix (60 profiles)"}


def benchmark_profiles(size: str) -> list[dict]:
    """Deterministic benchmark: the demo case, or diets x cuisines x allergy sets (all at the demo budget of ₹1,500)."""
    if size == "demo":
        return [dict(DEMO_PROFILE)]
    full = [{**DEMO_PROFILE, "diet": d, "cuisine": c, "allergies": list(a)} for d, c, a in itertools.product(DIETS, CUISINES, ALLERGY_SETS)]
    return full[::5] if size == "quick" else full


def _check(report: dict, cid: str) -> dict:
    return next(c for c in report["checks"] if c["id"] == cid)


def _days_ok(report: dict, cid: str) -> int:
    return sum(r["Result"] == "pass" for r in _check(report, cid)["details"])


def evaluate_profile(profile: dict, foods: pd.DataFrame, templates: list, rules: list, with_adaptation: bool = True) -> dict:
    t, c = compute_targets(profile), build_constraints(profile)
    t0 = time.perf_counter()
    plan = generate_meal_plan(t, c, foods, templates)
    gen_ms = (time.perf_counter() - t0) * 1000
    t1 = time.perf_counter()
    res = revise_until_valid(plan, t, c, foods)
    val_ms = (time.perf_counter() - t1) * 1000
    ini, fin, final_plan = res["initial_report"], res["report"], res["plan"]
    w = final_plan["weekly"]
    row = {
        "diet": profile["diet"], "cuisine": profile["cuisine"], "allergies": ", ".join(profile["allergies"]) or "none",
        "gen_ms": round(gen_ms, 1), "validate_revise_ms": round(val_ms, 1),
        "initial_status": ini["overall_status"], "final_status": fin["overall_status"], "score": fin["score"],
        "valid_first_pass": ini["counts"]["fail"] == 0, "valid_final": fin["counts"]["fail"] == 0,
        "revisions": res["revisions_used"], "issues_initial": len(ini["issues"]), "issues_final": len(fin["issues"]),
        "hard_constraints_ok": all(_check(fin, k)["status"] == "pass" for k in HARD_CHECKS),
        "critical_failures": len(fin["critical_failures"]),
        "within_budget": w["within_budget"], "within_budget_first_pass": plan["weekly"]["within_budget"], "weekly_cost_inr": w["cost_inr"],
        "cal_error_pct": round(abs(w["avg_calories"] - t["calories"]) / t["calories"] * 100, 2),
        "protein_pct_of_target": round(w["avg_protein_g"] / t["protein_g"] * 100, 1),
        "days_cal_ok_final": _days_ok(fin, "calories"), "days_cal_ok_initial": _days_ok(ini, "calories"),
        "days_protein_ok_final": _days_ok(fin, "protein"), "days_protein_ok_initial": _days_ok(ini, "protein"),
        "adaptations": [],
    }
    if with_adaptation:
        base_crit = set(fin["critical_failures"])
        for phrase in BENCH_PHRASES:
            try:
                parsed = adaptation.parse_feedback(phrase, foods, 3)
                new, changes, _, nd = adaptation.apply_feedback(final_plan, parsed["actions"], t, c, foods, rules)
                rep = validate_plan(new, t, {**c, "excluded_foods": nd}, foods)
                row["adaptations"].append({"phrase": phrase, "crashed": False, "changed": bool(changes), "fails": rep["counts"]["fail"],
                                           "new_critical": sorted(set(rep["critical_failures"]) - base_crit)})
            except Exception as exc:               # a crash is itself a measured result
                row["adaptations"].append({"phrase": phrase, "crashed": True, "changed": False, "fails": 0, "new_critical": [], "error": str(exc)})
    return row


def run_benchmark(profiles: list[dict], foods, templates, rules, with_adaptation: bool = True, progress=None) -> dict:
    t0 = time.perf_counter()
    rows = []
    for i, p in enumerate(profiles, 1):
        rows.append(evaluate_profile(p, foods, templates, rules, with_adaptation))
        if progress:
            progress(i / len(profiles))
    return {"rows": rows, "summary": summarize(rows), "seconds": round(time.perf_counter() - t0, 1), "with_adaptation": with_adaptation,
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S")}


def _pct(n: float, d: float) -> float:
    return round(100 * n / d, 1) if d else 0.0


def summarize(rows: list[dict]) -> dict:
    n = len(rows)
    if not n:
        return {}
    days = 7 * n
    fixed = sum(r["issues_initial"] - r["issues_final"] for r in rows)
    ad = [a for r in rows for a in r["adaptations"]]
    per_phrase = {}
    for ph in BENCH_PHRASES:
        items = [a for a in ad if a["phrase"] == ph]
        if items:
            per_phrase[ph] = {"runs": len(items), "changed_pct": _pct(sum(a["changed"] for a in items), len(items)),
                              "no_failed_checks_pct": _pct(sum(a["fails"] == 0 for a in items if a["changed"]), max(sum(a["changed"] for a in items), 1)),
                              "crashes": sum(a["crashed"] for a in items)}
    return {
        "profiles": n,
        "target_accuracy": {"mean_calorie_error_pct": round(mean(r["cal_error_pct"] for r in rows), 2),
                            "mean_protein_pct_of_target": round(mean(r["protein_pct_of_target"] for r in rows), 1),
                            "days_calories_within_10pct_final": _pct(sum(r["days_cal_ok_final"] for r in rows), days),
                            "days_calories_within_10pct_initial": _pct(sum(r["days_cal_ok_initial"] for r in rows), days),
                            "days_protein_ok_final": _pct(sum(r["days_protein_ok_final"] for r in rows), days),
                            "days_protein_ok_initial": _pct(sum(r["days_protein_ok_initial"] for r in rows), days)},
        "constraint_compliance": {"hard_constraints_pct": _pct(sum(r["hard_constraints_ok"] for r in rows), n),
                                  "no_critical_failures_pct": _pct(sum(r["critical_failures"] == 0 for r in rows), n)},
        "validation": {"first_pass_pct": _pct(sum(r["valid_first_pass"] for r in rows), n),
                       "after_revision_pct": _pct(sum(r["valid_final"] for r in rows), n),
                       "fully_passed_pct": _pct(sum(r["final_status"] == "passed" for r in rows), n),
                       "status_counts": {s: sum(r["final_status"] == s for r in rows) for s in ("passed", "passed_with_warnings", "revision_required")},
                       "mean_score": round(mean(r["score"] for r in rows), 1)},
        "revisions": {"mean": round(mean(r["revisions"] for r in rows), 2), "distribution": {k: sum(r["revisions"] == k for r in rows) for k in (0, 1, 2)},
                      "issues_initial": sum(r["issues_initial"] for r in rows), "issues_final": sum(r["issues_final"] for r in rows),
                      "issue_fix_rate_pct": _pct(fixed, sum(r["issues_initial"] for r in rows))},
        "timing_ms": {"mean_generation": round(mean(r["gen_ms"] for r in rows), 1),
                      "mean_validate_revise": round(mean(r["validate_revise_ms"] for r in rows), 1),
                      "mean_total": round(mean(r["gen_ms"] + r["validate_revise_ms"] for r in rows), 1),
                      "max_total": round(max(r["gen_ms"] + r["validate_revise_ms"] for r in rows), 1)},
        "budget": {"within_budget_final_pct": _pct(sum(r["within_budget"] for r in rows), n),
                   "within_budget_first_pass_pct": _pct(sum(r["within_budget_first_pass"] for r in rows), n),
                   "mean_weekly_cost_inr": round(mean(r["weekly_cost_inr"] for r in rows))},
        "adaptation": {"runs": len(ad), "crashes": sum(a["crashed"] for a in ad),
                       "changed_pct": _pct(sum(a["changed"] for a in ad), len(ad)),
                       "no_failed_checks_pct": _pct(sum(a["fails"] == 0 for a in ad if a["changed"]), max(sum(a["changed"] for a in ad), 1)),
                       "new_critical_failures": sum(bool(a["new_critical"]) for a in ad), "per_phrase": per_phrase} if ad else None,
    }


def summarize_session(runs: list[dict]) -> dict | None:
    """Metrics for plans generated in this browser session (from `eval_runs`)."""
    v = [r for r in runs if "valid_final" in r]
    if not runs:
        return None
    n = len(runs)
    return {
        "plans": n, "validated": len(v),
        "mean_generation_ms": round(mean(r["generation_ms"] for r in runs), 1),
        "mean_calorie_error_pct": round(mean(r["avg_calorie_error_pct"] for r in runs), 2),
        "mean_protein_pct_of_target": round(mean(r["avg_protein_pct_of_target"] for r in runs), 1),
        "first_pass_pct": _pct(sum(r["valid_first_pass"] for r in v), len(v)) if v else None,
        "after_revision_pct": _pct(sum(r["valid_final"] for r in v), len(v)) if v else None,
        "mean_revisions": round(mean(r["revisions"] for r in v), 2) if v else None,
        "budget_pct": _pct(sum(r["within_budget"] for r in runs), n),
        "constraint_pct": _pct(sum(r["critical_failures"] == 0 for r in v), len(v)) if v else None,
    }


def rows_to_df(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame([{"Diet": r["diet"], "Cuisine": r["cuisine"], "Allergies": r["allergies"],
                          "First pass": "✅" if r["valid_first_pass"] else "❌", "Final": r["final_status"].replace("_", " "),
                          "Score": r["score"], "Revisions": r["revisions"], "Cost ₹": r["weekly_cost_inr"],
                          "Calorie err %": r["cal_error_pct"], "Protein %": r["protein_pct_of_target"],
                          "Time ms": round(r["gen_ms"] + r["validate_revise_ms"], 1)} for r in rows])
