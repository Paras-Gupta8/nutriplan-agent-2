"""Overview / Home dashboard."""
import pandas as pd
import streamlit as st

from core.meal_engine import food_db_summary, load_foods
from core.orchestrator import run_demo_scenario, run_workflow
from core.report import agent_log_json, load_ingredient_map, meal_plan_csv, shopping_csv, shopping_json, shopping_list, validation_report_json
from core.state import get_profile, init_state
from ui.components import STATUS_EMOJI, STRETCH, disclaimer, empty_state, explain_card, metric_card, page_header, stage_tracker
from ui.theme import COLORS, apply_theme

init_state()
apply_theme()
page_header("NutriPlan Agent", "Agentic AI nutrition assistant for personalized, budget-aware Indian meal planning - five agents, one explainable workflow.", "🥗")

ss = st.session_state
profile, targets, plan, val = get_profile(), ss["targets"], ss["meal_plan"], ss["validation"]
db = food_db_summary(load_foods())

# ---------------------------------------------------------------- headline cards
c = st.columns(4)
with c[0]:
    metric_card("Profile", "Saved ✅" if profile else "Not set", f"{profile['goal']} · {profile['diet']}" if profile else "Create one or run the demo")
with c[1]:
    metric_card("Daily target", f"{targets['calories']:,} kcal" if targets else "-", f"{targets['protein_g']} g protein · {targets['fiber_g']} g fiber" if targets else "Run the agents to compute")
with c[2]:
    metric_card("Plan status", val["report"]["overall_label"] if val else ("Not validated" if plan else "No plan yet"),
                f"score {val['report']['score']}/100 · {val['revisions_used']} revision(s)" if val else "Generate a 7-day plan")
with c[3]:
    metric_card("Weekly cost", f"₹{plan['weekly']['cost_inr']:,.0f}" if plan else "-",
                f"of ₹{profile['budget_weekly_inr']:,} budget" if profile else f"{db['items']} foods in database")
if plan and profile:
    ratio = plan["weekly"]["cost_inr"] / profile["budget_weekly_inr"]
    st.progress(min(ratio, 1.0), text=f"Budget used: {ratio:.0%}")

st.write("")
st.markdown("**Agent workflow**")
stage_tracker(ss["workflow_stages"])

# ---------------------------------------------------------------- quick actions
st.write("")
st.subheader("Quick actions")
a1, a2 = st.columns(2)
if a1.button("🚀 Run the demo scenario end-to-end", type="primary", **STRETCH,
             help="22-year-old vegetarian student · fat loss · ₹1,500/week · peanut allergy · North Indian · ≤30 min"):
    with st.spinner("Planner → Nutrition → Meal Planner → Validator..."):
        run_demo_scenario()
    st.rerun()
if profile and a2.button("🔄 Regenerate my plan" if plan else "▶ Generate my plan", **STRETCH):
    with st.spinner("Running agents..."):
        run_workflow(profile, (plan["variation"] + 1) if plan else 0)
    st.rerun()
links = [("ui/pages/2_Profile.py", "Profile & Goals", "👤"), ("ui/pages/3_Nutrition_Targets.py", "Nutrition Targets", "🎯"),
         ("ui/pages/4_Meal_Plan.py", "Meal Plan", "🍽️"), ("ui/pages/5_Validation.py", "Validation", "✅"),
         ("ui/pages/6_Shopping_List.py", "Shopping List", "🛒"), ("ui/pages/7_Daily_Adaptation.py", "Daily Adaptation", "🔄"),
         ("ui/pages/8_Agent_Log.py", "Agent Log", "📜"), ("ui/pages/9_Evaluation.py", "Evaluation", "📈")]
cols = st.columns(4)
for i, (path, label, icon) in enumerate(links):
    cols[i % 4].page_link(path, label=label, icon=icon)

# ---------------------------------------------------------------- profile and targets
st.write("")
left, right = st.columns(2)
with left:
    st.subheader("Your profile")
    if profile:
        st.dataframe(pd.DataFrame({
            "Field": ["Age / sex", "Height / weight", "Activity", "Goal", "Diet", "Allergies", "Cuisine", "Weekly budget", "Max cooking time"],
            "Value": [f"{profile['age']} / {profile['sex']}", f"{profile['height_cm']:g} cm / {profile['weight_kg']:g} kg", profile["activity_level"],
                      profile["goal"], profile["diet"], ", ".join(profile["allergies"]) or "None", profile["cuisine"],
                      f"₹{profile['budget_weekly_inr']:,}", f"{profile['max_prep_time_min']} min"]}), hide_index=True, **STRETCH)
        if ss["prefs"]["dislikes"]:
            st.caption("Avoiding: " + ", ".join(ss["prefs"]["dislikes"]))
    else:
        empty_state("Get started", "Create your profile, or press the demo button above to see everything at once.", "ui/pages/2_Profile.py", "Open Profile & Goals")
with right:
    st.subheader("Goal and targets")
    if targets:
        for name, key in [("Protein", "protein"), ("Carbohydrates", "carbs"), ("Fat", "fat")]:
            g = targets[f"{key}_g"]
            st.progress(min(targets["macro_pct"][key], 100) / 100, text=f"{name}: {g} g ({targets['macro_pct'][key]}% of calories)")
        explain_card(targets["reasoning"]["calories"])
    else:
        st.info("Targets appear after the Planner and Nutrition agents run.")

# ---------------------------------------------------------------- plan at a glance
if plan:
    st.write("")
    st.subheader("Plan at a glance")
    w = plan["weekly"]
    m = st.columns(4)
    with m[0]: metric_card("Avg calories", f"{w['avg_calories']:,} kcal", f"target {targets['calories']:,}")
    with m[1]: metric_card("Avg protein", f"{w['avg_protein_g']} g", f"target {targets['protein_g']} g")
    with m[2]: metric_card("Avg fiber", f"{w['avg_fiber_g']} g", f"minimum {targets['fiber_g']} g")
    with m[3]: metric_card("Adaptations", str(len(ss["adaptation_history"])), "applied this session")
    if st.toggle("Show weekly chart", value=False, help="Charts are drawn only when requested to keep the page fast."):
        days = [d["day"] for d in plan["days"]]
        st.line_chart(pd.DataFrame({"Calories": [d["totals"]["calories"] for d in plan["days"]], "Target": targets["calories"]}, index=days),
                      color=[COLORS["green"], COLORS["muted"]])
    if val and val["report"]["feedback"]:
        with st.expander(f"⚠️ {len(val['report']['feedback'])} open validation issue(s)"):
            for f in val["report"]["feedback"]:
                st.markdown(f"- {f['problem']}")

# ---------------------------------------------------------------- recent activity
log = ss["agent_log"]
if log:
    st.write("")
    st.subheader("Latest agent activity")
    st.dataframe(pd.DataFrame([{"Time": e["timestamp"][11:], "Agent": e["agent_name"], "Action": e["action"],
                                "Status": f"{STATUS_EMOJI.get(e['status'], '')} {e['status'].replace('_', ' ')}", "Result": e["output_summary"]}
                               for e in reversed(log[-6:])]), hide_index=True, **STRETCH)

# ---------------------------------------------------------------- export center
st.write("")
st.subheader("Export center")
x = st.columns(5)
sl = shopping_list(plan, load_ingredient_map()) if plan else None
x[0].download_button("Meal plan (CSV)", meal_plan_csv(plan) if plan else "", "nutriplan_meal_plan.csv", "text/csv", disabled=not plan, **STRETCH)
x[1].download_button("Shopping list (CSV)", shopping_csv(sl) if sl else "", "nutriplan_shopping_list.csv", "text/csv", disabled=not plan, **STRETCH)
x[2].download_button("Shopping list (JSON)", shopping_json(sl) if sl else "", "nutriplan_shopping_list.json", "application/json", disabled=not plan, **STRETCH)
x[3].download_button("Validation (JSON)", validation_report_json(val, targets, profile, plan) if val else "", "nutriplan_validation_report.json",
                     "application/json", disabled=not val, **STRETCH)
x[4].download_button("Agent log (JSON)", agent_log_json(log) if log else "", "nutriplan_agent_log.json", "application/json", disabled=not log, **STRETCH)

# ---------------------------------------------------------------- how it works
st.write("")
with st.expander("How NutriPlan Agent works"):
    st.graphviz_chart('''digraph G { rankdir=LR; bgcolor="transparent";
      node [shape=box, style="rounded,filled", fillcolor="#DCFCE7", color="#16A34A", fontname="Helvetica", fontsize=11];
      edge [color="#5B7A72", fontname="Helvetica", fontsize=9];
      U [label="Profile · goal\\nbudget · restrictions", fillcolor="#F1F5F4", color="#5B7A72"];
      P [label="Planner"]; N [label="Nutrition"]; M [label="Meal Planner"]; V [label="Validator", fillcolor="#CCFBF1", color="#0D9488"];
      A [label="Adaptation"]; O [label="Plan · dashboard\\nshopping list · log", fillcolor="#F1F5F4", color="#5B7A72"];
      U -> P -> N -> M -> V; V -> M [label="revise (max 2)", style=dashed]; V -> O [label="valid"]; O -> A [label="daily feedback"]; A -> V [label="re-check", style=dashed]; }''')
    st.dataframe(pd.DataFrame({
        "Agent": ["Planner", "Nutrition", "Meal Planner", "Validator / Critic", "Adaptation"],
        "Does": ["Turns the profile into a JSON execution plan and food filters", "BMR, TDEE, calories, macros and fiber with reasons",
                 "Builds 7 days from templates with portion scaling; revises on feedback", "Checks calories, protein, allergens, diet, budget, variety, meals, fiber, cuisine/time",
                 "Handles skipped meals, eating out, dislikes, cheaper, higher protein and quicker meals"],
        "Method": ["Rules", "Pure Python (Mifflin-St Jeor)", "Rules + templates", "Pure Python checks", "Rules first; optional Ollama only rewrites the explanation"]}),
        hide_index=True, **STRETCH)
    st.caption(f"Local data only: {db['items']} Indian foods ({db['vegetarian']} vegetarian), 22 meal-day templates, 45 substitution rules. "
               "No paid APIs and no internet required.")
disclaimer()
