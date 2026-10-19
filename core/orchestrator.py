"""Workflow controller. Phase 3 covers Planner -> Nutrition -> Meal Planner -> Validator (+revision loop); later phases extend the chain."""
from __future__ import annotations

from time import perf_counter

import streamlit as st

from copy import deepcopy

from agents.adaptation_agent import run_adaptation_agent
from agents.meal_planner import run_meal_planner_agent, run_meal_planner_revision
from agents.nutrition_agent import run_nutrition_agent
from agents.planner import run_planner_agent
from agents.validator_agent import run_validator_agent
from core.meal_engine import load_foods
from core.revision import MAX_REVISIONS, plan_metrics, revise_until_valid
from core.nutrition import DEMO_PROFILE
from core.state import log_event, save_profile, set_stage

AGENT_NAME = "Orchestrator"


def run_planning_pipeline(profile: dict) -> bool:
    """Run Planner then Nutrition; store results in session state. Returns True on success."""
    t0 = perf_counter()
    log_event(AGENT_NAME, "workflow_started", input_summary="Profile received",
              output_summary="Dispatching Planner Agent → Nutrition Agent")
    try:
        set_stage("Planner", "running")
        plan = run_planner_agent(profile, dislikes=st.session_state["prefs"]["dislikes"])
        st.session_state["execution_plan"] = plan
        set_stage("Planner", "warning" if plan["feasibility"]["warnings"] else "done")

        set_stage("Nutrition", "running")
        targets = run_nutrition_agent(profile)
        st.session_state["targets"] = targets
        set_stage("Nutrition", "warning" if targets["warnings"] else "done")
    except Exception as exc:  # the app must never crash on an agent failure
        for stage, status in st.session_state["workflow_stages"].items():
            if status == "running":
                set_stage(stage, "failed")
        log_event(AGENT_NAME, "workflow_failed", input_summary="Planning pipeline",
                  output_summary=f"{type(exc).__name__}: {exc}", status="failed")
        return False

    st.session_state["workflow_status"] = "targets_ready"
    log_event(AGENT_NAME, "planning_pipeline_complete", input_summary="Planner + Nutrition agents finished",
              output_summary=f"Execution plan and targets stored ({(perf_counter() - t0) * 1000:.0f} ms). Next: Meal Planner Agent.")
    return True


def run_meal_planner_stage(profile: dict, variation: int = 0) -> bool:
    """Run only the Meal Planner (targets and execution plan must already exist)."""
    ss = st.session_state
    if ss["targets"] is None or ss["execution_plan"] is None:
        return False
    try:
        set_stage("Meal Planner", "running")
        plan = run_meal_planner_agent(ss["targets"], ss["execution_plan"], variation)
        if not plan["days"]:
            set_stage("Meal Planner", "failed")
            return False
        ss["meal_plan"] = plan
        ss["validation"] = None  # any earlier validation belongs to the old plan
        ss["workflow_status"] = "plan_generated"
        set_stage("Meal Planner", "warning" if plan["warnings"] else "done")
        set_stage("Validator", "pending")
        w = plan["weekly"]
        t = ss["targets"]
        ss["eval_runs"].append({
            "timestamp": plan["generated_at"], "generation_ms": plan["generation_ms"], "variation": variation,
            "avg_calorie_error_pct": round(abs(w["avg_calories"] - t["calories"]) / t["calories"] * 100, 1),
            "avg_protein_pct_of_target": round(w["avg_protein_g"] / t["protein_g"] * 100, 1),
            "weekly_cost_inr": w["cost_inr"], "within_budget": w["within_budget"], "revisions": 0,
        })
        log_event(AGENT_NAME, "meal_planner_stage_complete", input_summary=f"variation {variation}",
                  output_summary=f"Plan stored ({plan['generation_ms']} ms). Next: Validator Agent.")
        return True
    except Exception as exc:
        set_stage("Meal Planner", "failed")
        log_event(AGENT_NAME, "workflow_failed", input_summary="Meal planner stage",
                  output_summary=f"{type(exc).__name__}: {exc}", status="failed")
        return False


def run_validation_stage(profile: dict | None = None) -> bool:
    """Validate the current plan; on failures run up to MAX_REVISIONS Meal Planner revisions. Stores the best plan."""
    ss = st.session_state
    plan, targets, ep = ss["meal_plan"], ss["targets"], ss["execution_plan"]
    if plan is None or targets is None or ep is None:
        return False
    constraints, foods, t0 = ep["constraints"], load_foods(), perf_counter()
    try:
        set_stage("Validator", "running")
        result = revise_until_valid(
            plan, targets, constraints, foods, MAX_REVISIONS,
            validate_fn=lambda p, a, final: run_validator_agent(p, targets, constraints, a, final, foods),
            revise_fn=lambda p, rep, a: run_meal_planner_revision(p, rep, targets, constraints, a, foods),
        )
    except Exception as exc:
        set_stage("Validator", "failed")
        log_event(AGENT_NAME, "workflow_failed", input_summary="Validation stage",
                  output_summary=f"{type(exc).__name__}: {exc}", status="failed")
        return False

    report, hist = result["report"], result["history"]
    ss["meal_plan"] = result["plan"]
    ss["validation"] = {
        "report": report, "initial_report": result["initial_report"], "history": hist,
        "revisions_used": result["revisions_used"], "best_attempt": result["best_attempt"],
        "final_status": report["overall_status"], "validated_at": result["plan"].get("generated_at"),
        "initial_metrics": hist[0]["metrics"], "final_metrics": plan_metrics(result["plan"]),
        "duration_ms": round((perf_counter() - t0) * 1000, 1),
    }
    ss["workflow_status"] = "validated"
    set_stage("Validator", "done" if report["overall_status"] == "passed" else "warning")
    if ss["eval_runs"]:
        init = result["initial_report"]
        ss["eval_runs"][-1].update({
            "validation_status": report["overall_status"], "score": report["score"], "revisions": result["revisions_used"],
            "valid_first_pass": init["counts"]["fail"] == 0, "valid_final": report["counts"]["fail"] == 0,
            "issues_initial": len(init["issues"]), "issues_final": len(report["issues"]),
            "critical_failures": len(report["critical_failures"]),
            "budget_ok": next(c for c in report["checks"] if c["id"] == "budget")["status"] != "fail",
            "within_budget": result["plan"]["weekly"]["within_budget"],
            "weekly_cost_inr": result["plan"]["weekly"]["cost_inr"],
            "avg_calorie_error_pct": round(abs(result["plan"]["weekly"]["avg_calories"] - targets["calories"]) / targets["calories"] * 100, 1),
            "avg_protein_pct_of_target": round(result["plan"]["weekly"]["avg_protein_g"] / targets["protein_g"] * 100, 1),
        })
    log_event(AGENT_NAME, "validation_complete", input_summary=f"Max {MAX_REVISIONS} revisions allowed",
              output_summary=f"{report['overall_label']} - score {report['score']}/100 after {result['revisions_used']} revision(s) "
                             f"({len(result['initial_report']['issues'])} → {len(report['issues'])} issue(s)); {ss['validation']['duration_ms']} ms",
              status="success" if report["overall_status"] == "passed" else "warning",
              details="Best available plan is displayed." + (" Remaining issues are shown as warnings." if report["issues"] else ""))
    return True


def run_workflow(profile: dict, variation: int = 0) -> bool:
    """Full chain so far: Planner -> Nutrition -> Meal Planner -> Validator (+revisions). Reuses existing targets unless missing."""
    if st.session_state["targets"] is None or st.session_state["execution_plan"] is None:
        if not run_planning_pipeline(profile):
            return False
    return run_meal_planner_stage(profile, variation) and run_validation_stage(profile)


def _single_attempt_validation(plan: dict, report: dict, source: str) -> dict:
    metrics = plan_metrics(plan)
    return {"report": report, "initial_report": report, "history": [{"attempt": 0, "status": report["overall_status"], "label": report["overall_label"],
            "score": report["score"], "counts": report["counts"], "issues": report["feedback"], "actions": [], "metrics": metrics}],
            "revisions_used": 0, "best_attempt": 0, "final_status": report["overall_status"], "validated_at": plan.get("generated_at"),
            "initial_metrics": metrics, "final_metrics": metrics, "duration_ms": 0.0, "source": source}


def run_adaptation(profile: dict, text: str, day: int = 1, slot: str | None = None, use_ollama: bool = False) -> dict:
    """Adaptation stage: classify feedback, change the plan, revalidate once, store the result (with an undo snapshot)."""
    ss = st.session_state
    plan, targets, ep = ss["meal_plan"], ss["targets"], ss["execution_plan"]
    if plan is None or targets is None or ep is None:
        return {"status": "failed", "changed": False, "messages": ["Generate a meal plan first."], "changes": []}
    constraints = {**ep["constraints"], "excluded_foods": sorted(set(ep["constraints"].get("excluded_foods", [])) | set(ss["prefs"]["dislikes"]))}
    set_stage("Adaptation", "running")
    try:
        result = run_adaptation_agent(text, plan, targets, constraints, day, slot, use_ollama)
    except Exception as exc:     # the app must never break on feedback
        set_stage("Adaptation", "failed")
        log_event(AGENT_NAME, "workflow_failed", input_summary=f"Adaptation: “{text}”", output_summary=f"{type(exc).__name__}: {exc}", status="failed")
        return {"status": "failed", "changed": False, "messages": [f"Something went wrong while adapting the plan ({type(exc).__name__}). Your plan is unchanged."], "changes": []}
    if not result["changed"]:
        set_stage("Adaptation", "warning")
        ss["last_adaptation"] = result
        return result
    snapshot = {"plan_before": deepcopy(plan), "validation_before": deepcopy(ss["validation"])}
    ss["meal_plan"] = result["new_plan"]
    ss["validation"] = _single_attempt_validation(result["new_plan"], result["report"], "adaptation")
    if result["new_dislikes"]:
        ss["prefs"]["dislikes"] = sorted(set(ss["prefs"]["dislikes"]) | set(result["new_dislikes"]))
        ss["execution_plan"]["constraints"]["excluded_foods"] = list(ss["prefs"]["dislikes"])
    ss["adaptation_history"].append({
        "timestamp": log_timestamp(), "feedback": text, "intents": [a["intent"] for a in result["parsed"]["actions"]],
        "changes": result["changes"], "explanation": result["explanation"], "explanation_source": result["explanation_source"],
        "before": result["before"], "after": result["after"], "validation_status": result["report"]["overall_status"], **snapshot})
    ss["last_adaptation"] = result
    ss["workflow_status"] = "adapted"
    set_stage("Adaptation", "done" if result["report"]["overall_status"] == "passed" else "warning")
    set_stage("Validator", "done" if result["report"]["overall_status"] == "passed" else "warning")
    log_event(AGENT_NAME, "adaptation_complete", input_summary=f"“{text}”",
              output_summary=f"Plan updated; revalidation: {result['report']['overall_label']} (score {result['report']['score']}/100)",
              status="success" if result["report"]["overall_status"] == "passed" else "warning")
    return result


def undo_last_adaptation() -> bool:
    ss = st.session_state
    if not ss["adaptation_history"]:
        return False
    last = ss["adaptation_history"].pop()
    ss["meal_plan"], ss["validation"] = last["plan_before"], last["validation_before"]
    ss["last_adaptation"] = None
    set_stage("Adaptation", "pending")
    log_event(AGENT_NAME, "undo_adaptation", input_summary=f"“{last['feedback']}”", output_summary="Previous plan restored", status="success")
    return True


def log_timestamp() -> str:
    from datetime import datetime
    return datetime.now().isoformat(timespec="seconds")


def run_demo_scenario() -> bool:
    """One click: load the demo student (22y, vegetarian, fat loss, ₹1,500/wk, peanut allergy, North Indian, <=30 min) and run every agent."""
    ss = st.session_state
    profile = dict(DEMO_PROFILE)
    ss["prefs"]["dislikes"] = []
    save_profile(profile)
    ss.pop("pf_seeded", None)      # let the Profile form re-seed from the saved profile
    return run_workflow(profile)
