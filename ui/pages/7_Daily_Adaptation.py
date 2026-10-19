"""Daily Adaptation page: tell the Adaptation Agent what happened today."""
import pandas as pd
import streamlit as st

from agents.adaptation_agent import INTENT_LABEL
from core.adaptation import ollama_models
from core.orchestrator import run_adaptation, run_validation_stage, undo_last_adaptation
from core.report import MEAL_ORDER, checks_table_df
from core.state import get_profile, init_state
from ui.components import disclaimer, empty_state, explain_card, metric_card, page_header
from ui.components import STRETCH, lazy_tabs
from ui.theme import COLORS, apply_theme

init_state()
apply_theme()
page_header("Daily Adaptation", "Tell the Adaptation Agent what happened - it updates the plan, recalculates and revalidates.", "🔄")

profile, plan = get_profile(), st.session_state["meal_plan"]
if not profile:
    empty_state("No profile yet", "Save your profile first.", "ui/pages/2_Profile.py", "Open Profile & Goals")
    st.stop()
if plan is None:
    empty_state("No meal plan yet", "Generate your 7-day plan, then come back to adapt it.", "ui/pages/4_Meal_Plan.py", "Open Meal Plan")
    st.stop()

EXAMPLES = ["I skipped lunch", "I ate out for dinner", "Make the plan cheaper", "I don't like paneer",
            "Make today high protein", "I have only 20 minutes to cook"]
st.session_state.setdefault("ad_text", "")


def _fill(text: str) -> None:
    st.session_state["ad_text"] = text


@st.cache_data(ttl=30, show_spinner=False)
def _ollama_status() -> list[str]:
    return ollama_models()


st.markdown("**Quick examples**")
cols = st.columns(3)
for i, ex in enumerate(EXAMPLES):
    cols[i % 3].button(ex, key=f"ex_{i}", on_click=_fill, args=(ex,), **STRETCH)

with st.form("adapt_form"):
    text = st.text_area("What happened?", key="ad_text", height=80,
                        placeholder="e.g. I skipped lunch · I ate out 900 kcal for dinner · I don't like paneer · Make the plan cheaper")
    c1, c2, c3 = st.columns(3)
    day = c1.selectbox("Which day is this about?", [d["day"] for d in plan["days"]], format_func=lambda n: f"Day {n}",
                       help="Used for 'today' and when your text doesn't name a day.")
    meal = c2.selectbox("Meal (optional)", ["Auto (from my text)", *MEAL_ORDER],
                        help="Helps for 'skipped' or 'ate out' when the meal isn't in your text.")
    models = _ollama_status()
    use_llm = c3.checkbox("Rephrase explanations with local Ollama", value=bool(models), disabled=not models,
                          help="Optional. Decisions are always rule-based; the model only rewrites the explanation.")
    c3.caption(f"Ollama detected: {models[0]}" if models else "Ollama not running - using built-in explanations.")
    submitted = st.form_submit_button("✨ Adapt my plan", type="primary", **STRETCH)

if submitted:
    if not text.strip():
        st.warning("Please describe what happened, or tap one of the examples.")
    else:
        with st.spinner("Adaptation Agent → Validator Agent..."):
            run_adaptation(profile, text.strip(), day, None if meal.startswith("Auto") else meal, use_llm)
        st.rerun()

hist = st.session_state["adaptation_history"]
res = st.session_state.get("last_adaptation")
prefs = st.session_state["prefs"]["dislikes"]

if res is None and hist:
    res = None
if res is None:
    st.info("No adaptation yet this session. Describe your day above and the plan will update.")
else:
    if not res["changed"]:
        st.warning(" ".join(res["messages"]) or "No change was made.")
    else:
        rep = res["report"]
        banner = {"passed": st.success, "passed_with_warnings": st.warning, "revision_required": st.error}[rep["overall_status"]]
        banner(f"Plan updated - revalidation: **{rep['overall_label']}** (score {rep['score']}/100).")
        understood = [f"**{INTENT_LABEL[a['intent']]}** → {'whole week' if a['scope'] == 'week' else 'Day ' + str(a['days'][0])}"
                      + (f" · {a['slot']}" if a.get("slot") else "") + (f" · ≤{a['minutes']} min" if a.get("minutes") else "")
                      for a in res["parsed"]["actions"]]
        st.markdown("**Understood as:** " + "  |  ".join(understood))
        for m in res["messages"]:
            st.caption(f"ℹ️ {m}")

        view = lazy_tabs(["🔁 Substitutions", "📊 Before / after", "🍽️ Updated meals", "💬 Why", "✅ Revalidation"], "ad_view")
        if view.startswith("🔁"):
            st.dataframe(pd.DataFrame([{"Day": c["day"], "Meal": c["slot"], "Before": c["from"], "After": c["to"],
                                        "Rule": c["rule_id"] or ("-" if c["kind"] in ("skipped", "ate_out") else "closest match"),
                                        "Cost Δ ₹": c["delta"]["cost_inr"], "Protein Δ g": c["delta"]["protein_g"], "kcal Δ": c["delta"]["calories"]}
                                       for c in res["changes"]]), hide_index=True, **STRETCH)
            for c in res["changes"]:
                if c.get("portion_changes"):
                    st.caption(f"Day {c['day']} portion changes: " + "; ".join(c["portion_changes"]))
        if view.startswith("📊"):
            b, a = res["before"], res["after"]
            n = a["days"]
            rows = [{"Nutrient": lab, "Before": f"{b[k]:,.0f}{unit}", "After": f"{a[k]:,.0f}{unit}", "Change": f"{a[k] - b[k]:+,.0f}{unit}",
                     "Target": (f"{targets_v * n:,.0f}{unit}" if (targets_v := st.session_state['targets'].get(tk)) else "-")}
                    for lab, k, tk, unit in [("Calories", "calories", "calories", " kcal"), ("Protein", "protein_g", "protein_g", " g"),
                                             ("Carbs", "carbs_g", "carbs_g", " g"), ("Fat", "fat_g", "fat_g", " g"),
                                             ("Fiber", "fiber_g", "fiber_g", " g"), ("Cost", "cost_inr", "budget", " ₹")]]
            rows[-1]["Target"] = f"≤ ₹{profile['budget_weekly_inr'] / 7 * n:,.0f}"
            st.caption(f"Totals over the {n} affected day(s): " + ", ".join(f"Day {d}" for d in res["affected_days"]))
            st.dataframe(pd.DataFrame(rows), hide_index=True, **STRETCH)
            if hist:
                prev = hist[-1]["plan_before"]["days"]
                st.markdown("**Calories and protein per day, before vs after**")
                st.bar_chart(pd.DataFrame({"Protein before": [d["totals"]["protein_g"] for d in prev],
                                           "Protein after": [d["totals"]["protein_g"] for d in plan["days"]]},
                                          index=[d["day"] for d in plan["days"]]), color=[COLORS["muted"], COLORS["green"]])
        if view.startswith("🍽️"):
            day_pick = st.radio("Show day", res["affected_days"], horizontal=True, format_func=lambda n: f"Day {n}")
            d = plan["days"][day_pick - 1]
            for slot in MEAL_ORDER:
                meal_ = d["meals"][slot]
                with st.container(border=True):
                    tag = " · ⏭️ skipped" if meal_.get("skipped") else (" · 🍴 ate out" if meal_.get("ate_out") else "")
                    st.markdown(f"**{'Evening snack' if slot == 'Snack' else slot}**{tag}  \n"
                                f"<span style='color:{COLORS['muted']};font-size:.85rem'>{meal_['totals']['calories']:.0f} kcal · "
                                f"{meal_['totals']['protein_g']:.0f} g protein · ₹{meal_['totals']['cost_inr']:.0f}</span>", unsafe_allow_html=True)
                    if meal_["items"]:
                        st.dataframe(pd.DataFrame([{"Food": i["food_name"], "Portion": f"{i['servings']:g} × {i['serving_label']}",
                                                    "kcal": i["calories"], "Protein": i["protein_g"], "Cost ₹": i["cost_inr"]} for i in meal_["items"]]),
                                     hide_index=True, **STRETCH)
            t = d["totals"]
            st.caption(f"Day {day_pick} totals: {t['calories']:.0f} kcal · {t['protein_g']:.0f} g protein · ₹{t['cost_inr']:.0f}")
        if view.startswith("💬"):
            explain_card(res["explanation"].replace("\n", " "))
            src = res["explanation_source"]
            st.caption("Explanation written by the **local Ollama model** (numbers verified unchanged)." if src == "ollama" else
                       "Explanation written by the **built-in rule engine**" + (" (Ollama was unavailable or its output was rejected)." if res["ollama_requested"] else "."))
            if src == "ollama":
                with st.expander("Rule-engine version"):
                    st.write(res["explanation_rules"])
        if view.startswith("✅"):
            st.dataframe(checks_table_df(rep), hide_index=True, **STRETCH)
            if rep["counts"]["fail"]:
                st.error("The adaptation left some checks failing. The Meal Planner can try to fix them (up to 2 revisions).")
                if st.button("🛠️ Ask the Meal Planner to fix remaining issues"):
                    with st.spinner("Validator → Meal Planner..."):
                        run_validation_stage(profile)
                    st.rerun()

    if hist:
        if st.button("↩️ Undo last adaptation"):
            undo_last_adaptation()
            st.rerun()

if prefs:
    st.write("")
    st.markdown("**Foods you asked to avoid** (applied to every new plan this session)")
    st.write(", ".join(prefs))
    if st.button("Clear food exclusions"):
        st.session_state["prefs"]["dislikes"] = []
        if st.session_state["execution_plan"]:
            st.session_state["execution_plan"]["constraints"]["excluded_foods"] = []
        st.rerun()

if hist:
    with st.expander(f"Adaptation history ({len(hist)})"):
        for i, h in enumerate(reversed(hist), 1):
            st.markdown(f"**{len(hist) - i + 1}.** “{h['feedback']}” - {len(h['changes'])} change(s), "
                        f"{h['before']['calories']:.0f}→{h['after']['calories']:.0f} kcal, ₹{h['before']['cost_inr']:.0f}→₹{h['after']['cost_inr']:.0f}, "
                        f"{h['validation_status'].replace('_', ' ')}")

st.write("")
disclaimer()
