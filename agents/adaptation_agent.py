"""Adaptation Agent - turns daily feedback into plan changes (rules first; optional Ollama only rewrites the explanation)."""
from __future__ import annotations

import pandas as pd

from agents.validator_agent import run_validator_agent
from core.adaptation import (
    apply_feedback, build_explanation, friendly_explanation, load_rules, parse_feedback, scope_totals,
)
from core.meal_engine import load_foods
from core.state import log_event

AGENT_NAME = "Adaptation Agent"
INTENT_LABEL = {"skipped": "Skipped meal", "ate_out": "Ate out", "cheaper": "Make cheaper", "dislike": "Dislike a food",
                "protein": "Higher protein", "quick": "Quicker meals"}


def run_adaptation_agent(text: str, plan: dict, targets: dict, constraints: dict, ui_day: int = 1, ui_slot: str | None = None,
                         use_ollama: bool = False, foods: pd.DataFrame | None = None, rules: list | None = None) -> dict:
    foods = load_foods() if foods is None else foods
    rules = load_rules() if rules is None else rules
    parsed = parse_feedback(text, foods, ui_day, ui_slot)
    understood = "; ".join(
        f"{INTENT_LABEL[a['intent']]} → {'whole week' if a['scope'] == 'week' else 'Day ' + str(a['days'][0])}"
        + (f", {a['slot'].lower()}" if a.get("slot") else "") + (f", {a['minutes']} min" if a.get("minutes") else "")
        + (f", foods: {', '.join(a['foods'][:3])}{'…' if len(a['foods']) > 3 else ''}" if a["intent"] == "dislike" else "")
        for a in parsed["actions"]) or "no recognised request"
    log_event(AGENT_NAME, "classify_feedback", input_summary=f"“{text}” (selected Day {ui_day})", output_summary=f"Rule-based classification: {understood}",
              status="warning" if parsed["unrecognized"] else "success",
              details="Rules used (no LLM). " + "; ".join(parsed["notes"]) if parsed["notes"] else "Rules used (no LLM).")
    base = {"feedback": text, "parsed": parsed, "changes": [], "messages": [], "changed": False, "new_dislikes": [], "ollama_requested": use_ollama}
    if parsed["unrecognized"]:
        base["messages"] = ["I didn't recognise that request. Try: “I skipped lunch”, “I ate out for dinner”, “Make the plan cheaper”, "
                            "“I don't like paneer”, “Make today high protein” or “I have only 20 minutes to cook”."]
        return {**base, "status": "warning"}

    before_plan = plan
    new_plan, changes, messages, new_dislikes = apply_feedback(plan, parsed["actions"], targets, constraints, foods, rules)
    affected = sorted({c["day"] for c in changes} or {d for a in parsed["actions"] for d in a["days"]})
    base.update(messages=messages + parsed["notes"], changes=changes, new_dislikes=new_dislikes)
    if not changes:
        log_event(AGENT_NAME, "apply_substitutions", input_summary=understood, output_summary="No change made",
                  status="warning", details="\n".join(messages))
        return {**base, "status": "warning", "explanation": build_explanation([], messages, scope_totals(plan, affected), scope_totals(plan, affected)),
                "explanation_source": "rules"}

    before, after = scope_totals(before_plan, affected), scope_totals(new_plan, affected)
    log_event(AGENT_NAME, "apply_substitutions", input_summary=understood,
              output_summary=f"{len(changes)} change(s) on day(s) {', '.join(map(str, affected))}; "
                             f"{after['calories'] - before['calories']:+.0f} kcal, {after['protein_g'] - before['protein_g']:+.0f} g protein, "
                             f"{after['cost_inr'] - before['cost_inr']:+.0f} ₹",
              status="success", details="\n".join(f"- {c['kind']}: Day {c['day']} {c['slot']}: {c['from']} → {c['to']}"
                                                   + (f" [{c['rule_id']}]" if c["rule_id"] else " [fallback: closest nutrition]") for c in changes))
    report = run_validator_agent(new_plan, targets, constraints, attempt=0, final=True, foods=foods,
                                 label="revalidate_plan (after adaptation)")
    new_plan["validation_status"] = report["overall_status"]
    text_rules = build_explanation(changes, messages, before, after)
    explanation, source = friendly_explanation(text_rules, use_ollama)
    log_event(AGENT_NAME, "explain_changes", input_summary=f"{len(changes)} change(s)",
              output_summary=f"Explanation written by {'local Ollama model' if source == 'ollama' else 'built-in rules'}"
                             + (" (Ollama unavailable or rejected - fell back to rules)" if use_ollama and source == "rules" else ""),
              status="success" if source == "ollama" or not use_ollama else "warning", details=explanation)
    return {**base, "status": "success" if report["counts"]["fail"] == 0 else "warning", "changed": True, "new_plan": new_plan,
            "report": report, "before": before, "after": after, "affected_days": affected,
            "explanation": explanation, "explanation_rules": text_rules, "explanation_source": source}
