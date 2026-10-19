"""Nutrition Validation page."""
import pandas as pd
import streamlit as st

from core.orchestrator import run_validation_stage, run_workflow
from core.report import checks_table_df, validation_report_json
from core.state import get_profile, init_state
from core.validator import STATUS_ICON
from ui.components import disclaimer, empty_state, explain_card, metric_card, page_header
from ui.components import STRETCH, lazy_tabs
from ui.theme import COLORS, apply_theme

init_state()
apply_theme()
page_header("Nutrition Validation", "The Validator Agent checks every plan and sends feedback for up to two revisions.", "✅")

profile = get_profile()
if not profile:
    empty_state("No profile yet", "Save your profile first.", "ui/pages/2_Profile.py", "Open Profile & Goals")
    st.stop()

plan, val, targets = st.session_state["meal_plan"], st.session_state["validation"], st.session_state["targets"]
if plan is None:
    empty_state("No meal plan to validate", "Generate a 7-day plan first. Validation runs automatically afterwards.",
                "ui/pages/4_Meal_Plan.py", "Open Meal Plan")
    if st.button("▶ Generate plan and validate", type="primary"):
        with st.spinner("Running all agents..."):
            run_workflow(profile)
        st.rerun()
    st.stop()

if st.button("🔁 Re-run validation" if val else "▶ Run validation", type="primary"):
    with st.spinner("Validator Agent..."):
        run_validation_stage(profile)
    st.rerun()
if val is None:
    st.info("This plan has not been validated yet.")
    st.stop()

rep, hist = val["report"], val["history"]
banner = {"passed": st.success, "passed_with_warnings": st.warning, "revision_required": st.error}[rep["overall_status"]]
banner(f"**{rep['overall_label']}** - validation score {rep['score']} / 100 after {val['revisions_used']} revision(s).")
if rep["overall_status"] == "revision_required":
    st.caption("Revision attempts are exhausted; this is the best available plan, shown with its remaining issues.")

c = st.columns(4)
with c[0]: metric_card("Validation score", f"{rep['score']} / 100", rep["overall_label"])
with c[1]: metric_card("Checks", f"{rep['counts']['pass']} ✅  {rep['counts']['warning']} ⚠️  {rep['counts']['fail']} ❌", "Pass · warning · fail")
with c[2]: metric_card("Revisions used", f"{val['revisions_used']} of 2", f"Best plan = attempt {val['best_attempt']}")
with c[3]: metric_card("Issues", f"{len(val['initial_report']['issues'])} → {len(rep['issues'])}", "Initial → final")
st.progress(min(rep["score"] / 100, 1.0), text=f"Score {rep['score']}/100")
st.write("")

view = lazy_tabs(["🔎 Checks", "🔁 Revision history", "📊 Final nutrition comparison", "📥 Report"], "val_view")

if view.startswith("🔎"):
    st.dataframe(checks_table_df(rep), hide_index=True, **STRETCH)
    st.markdown("**Details by check**")
    for ch in rep["checks"]:
        with st.expander(f"{STATUS_ICON[ch['status']]} {ch['name']}"):
            explain_card(ch["message"])
            if ch["issues"]:
                st.markdown("**Exact reasons**")
                for i in ch["issues"]:
                    st.markdown(f"- {i['problem']}  \n  *Suggested fix:* {i['suggestion']}")
            if ch["details"]:
                st.dataframe(pd.DataFrame(ch["details"]), hide_index=True, **STRETCH)

if view.startswith("🔁"):
    st.dataframe(pd.DataFrame([{"Attempt": "Initial plan" if h["attempt"] == 0 else f"After revision {h['attempt']}",
                                "Result": h["label"], "Score": h["score"], "Pass": h["counts"]["pass"],
                                "Warning": h["counts"]["warning"], "Fail": h["counts"]["fail"]} for h in hist]),
                 hide_index=True, **STRETCH)
    if val["revisions_used"] == 0:
        st.success("The first plan passed validation, so no revision was needed.")
    for h in hist:
        if not h["actions"] and not h["issues"]:
            continue
        label = "Initial plan" if h["attempt"] == 0 else f"After revision {h['attempt']}"
        with st.expander(f"{label}: {h['label']} (score {h['score']})", expanded=h["attempt"] == 0):
            if h["issues"]:
                st.markdown("**Validator feedback → Meal Planner**")
                for i in h["issues"]:
                    st.markdown(f"- {i['problem']} *{i['suggestion']}*")
            if h["actions"]:
                st.markdown(f"**Meal Planner revision {h['attempt'] + 1} actions**")
                for a in h["actions"]:
                    st.markdown(f"- {a['description']}")

if view.startswith("📊"):
    ini, fin = val["initial_metrics"], val["final_metrics"]
    rows = []
    for label, key, tkey, unit in [("Calories (avg/day)", "avg_calories", "calories", "kcal"), ("Protein (avg/day)", "avg_protein_g", "protein_g", "g"),
                                   ("Carbs (avg/day)", "avg_carbs_g", "carbs_g", "g"), ("Fat (avg/day)", "avg_fat_g", "fat_g", "g"),
                                   ("Fiber (avg/day)", "avg_fiber_g", "fiber_g", "g")]:
        rows.append({"Nutrient": label, "Target": f"{targets[tkey]} {unit}", "Initial plan": f"{ini[key]:g} {unit}",
                     "Final plan": f"{fin[key]:g} {unit}", "Final vs target": f"{(fin[key] - targets[tkey]) / targets[tkey]:+.0%}"})
    b = profile["budget_weekly_inr"]
    rows.append({"Nutrient": "Weekly cost", "Target": f"≤ ₹{b:,}", "Initial plan": f"₹{ini['weekly_cost_inr']:,.0f}",
                 "Final plan": f"₹{fin['weekly_cost_inr']:,.0f}", "Final vs target": f"{(fin['weekly_cost_inr'] - b) / b:+.0%}"})
    st.dataframe(pd.DataFrame(rows), hide_index=True, **STRETCH)
    a, bcol = st.columns(2)
    days = [d["day"] for d in ini["per_day"]]
    with a:
        st.markdown("**Protein per day (g)**")
        st.line_chart(pd.DataFrame({"Initial": [d["protein_g"] for d in ini["per_day"]], "Final": [d["protein_g"] for d in fin["per_day"]],
                                    "90% minimum": 0.9 * targets["protein_g"]}, index=days),
                      color=[COLORS["muted"], COLORS["green"], COLORS["danger"]])
    with bcol:
        st.markdown("**Calories per day (kcal)**")
        st.line_chart(pd.DataFrame({"Initial": [d["calories"] for d in ini["per_day"]], "Final": [d["calories"] for d in fin["per_day"]],
                                    "Target": targets["calories"]}, index=days),
                      color=[COLORS["muted"], COLORS["green"], COLORS["teal"]])

if view.startswith("📥"):
    report_json = validation_report_json(val, targets, profile, plan)
    st.download_button("⬇️ Download validation report (JSON)", report_json, file_name="nutriplan_validation_report.json", mime="application/json")
    with st.expander("Preview"):
        st.code(report_json[:3000] + ("\n..." if len(report_json) > 3000 else ""), language="json")

st.write("")
disclaimer()
