"""Nutrition Targets page."""
import pandas as pd
import streamlit as st

from core.orchestrator import run_planning_pipeline
from core.state import STAGES, get_profile, init_state
from ui.components import disclaimer, empty_state, explain_card, metric_card, page_header
from ui.components import STRETCH, lazy_tabs
from ui.theme import COLORS, apply_theme

init_state()
apply_theme()
page_header("Nutrition Targets", "Calories and macros calculated by the Nutrition Agent, with reasons.", "🎯")

profile = get_profile()
if not profile:
    empty_state("No profile yet", "Save your profile first so the agents know your goal and constraints.",
                "ui/pages/2_Profile.py", "Open Profile & Goals")
    st.stop()

has_results = st.session_state["targets"] is not None
left, right = st.columns([3, 1])
left.caption(f"Profile: {profile['age']}y {profile['sex']} · {profile['goal']} · {profile['diet']} · {profile['cuisine']}")
if right.button("🔄 Recalculate" if has_results else "▶ Run Planner & Nutrition agents", type="primary", **STRETCH):
    with st.spinner("Planner Agent → Nutrition Agent..."):
        ok = run_planning_pipeline(profile)
    if not ok:
        st.error("An agent failed. See the Agent Log for details.")
    st.rerun() if ok else None

t, plan = st.session_state["targets"], st.session_state["execution_plan"]
if t is None:
    st.info("Press **Run Planner & Nutrition agents** to compute your targets.")
    st.stop()

# workflow strip
stages = st.session_state["workflow_stages"]
icons = {"pending": "⚪", "running": "🟡", "done": "🟢", "warning": "🟠", "failed": "🔴"}
st.caption("Workflow: " + "  →  ".join(f"{icons[stages[s]]} {s}" for s in STAGES))

for w in t["warnings"]:
    st.warning(w)

view = lazy_tabs(["📊 Targets & macros", "💡 Why these numbers", "🧭 Execution plan"], "nt_view")

if view.startswith("📊"):
    c = st.columns(4)
    with c[0]: metric_card("BMR", f"{t['bmr']:,} kcal", "Resting energy burn")
    with c[1]: metric_card("TDEE", f"{t['tdee']:,} kcal", f"BMR × {t['activity_multiplier']}")
    with c[2]: metric_card("Target calories", f"{t['calories']:,} kcal",
                           f"{t['goal_adjustment_kcal']:+d} kcal vs TDEE" if t["goal_adjustment_kcal"] else "Equal to TDEE")
    with c[3]: metric_card("BMI", f"{t['bmi']}", t["bmi_category"])
    st.write("")
    c = st.columns(4)
    with c[0]: metric_card("Protein", f"{t['protein_g']} g", f"{t['macro_pct']['protein']}% of calories")
    with c[1]: metric_card("Carbohydrates", f"{t['carbs_g']} g", f"{t['macro_pct']['carbs']}% of calories")
    with c[2]: metric_card("Fat", f"{t['fat_g']} g", f"{t['macro_pct']['fat']}% of calories")
    with c[3]: metric_card("Fiber", f"{t['fiber_g']} g", "Minimum daily target")

    st.write("")
    g1, g2 = st.columns(2)
    with g1:
        st.markdown("**Calories from each macro**")
        kcal = pd.DataFrame({"kcal": [t["protein_g"] * 4, t["carbs_g"] * 4, t["fat_g"] * 9]},
                            index=["Protein", "Carbohydrates", "Fat"])
        st.bar_chart(kcal, color=COLORS["green"])
        for name, key in [("Protein", "protein"), ("Carbohydrates", "carbs"), ("Fat", "fat")]:
            st.progress(min(t["macro_pct"][key], 100) / 100, text=f"{name}: {t['macro_pct'][key]}% of calories")
    with g2:
        st.markdown("**Energy balance (kcal/day)**")
        energy = pd.DataFrame({"kcal": [t["bmr"], t["tdee"], t["calories"]]}, index=["BMR", "TDEE", "Target"])
        st.bar_chart(energy, color=COLORS["teal"])

if view.startswith("💡"):
    labels = {"bmr": "BMR", "tdee": "TDEE", "calories": "Calorie target", "protein": "Protein", "carbs": "Carbohydrates",
              "fat": "Fat", "fiber": "Fiber", "bmi": "BMI"}
    for key, label in labels.items():
        st.markdown(f"**{label}**")
        explain_card(t["reasoning"][key])
        st.write("")

if view.startswith("🧭"):
    st.markdown(f"**Task:** {plan['task']}")
    f = plan["feasibility"]
    st.markdown("**Steps**")
    st.dataframe(pd.DataFrame({"#": range(1, len(plan["steps"]) + 1), "Step": plan["steps"],
                               "Owner": [plan["step_owners"][s] for s in plan["steps"]]}),
                 hide_index=True, **STRETCH)
    for w in f["warnings"]:
        st.warning(w)
    a, b = st.columns(2)
    with a:
        st.markdown(f"**Food filter funnel** ({f['eligible_foods']} of {f['total_foods']} foods eligible)")
        st.dataframe(pd.DataFrame(f["filter_funnel"]).rename(columns=str.title), hide_index=True, **STRETCH)
    with b:
        st.markdown("**Eligible foods per meal slot**")
        st.bar_chart(pd.Series(f["slot_pools"], name="foods"), color=COLORS["green"])
    st.markdown("**Why the planner chose these constraints**")
    for r in plan["rationale"]:
        st.markdown(f"- {r}")
    with st.expander("Raw execution plan (JSON)"):
        st.json(plan)

st.write("")
disclaimer()
