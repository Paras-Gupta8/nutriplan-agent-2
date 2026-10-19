"""Profile & Goals page."""
import streamlit as st

from core.nutrition import (
    ACTIVITY_LEVELS, ALLERGENS, CUISINES, DEMO_PROFILE, DIET_PREFERENCES, GOALS, SEX_OPTIONS,
    compute_targets, validate_profile,
)
from core.state import get_profile, init_state, save_profile
from ui.components import disclaimer, explain_card, metric_card, page_header
from ui.components import STRETCH
from ui.theme import apply_theme

init_state()
apply_theme()
page_header("Profile & Goals", "Tell NutriPlan about you - every plan is built around these constraints.", "👤")

KEYS = {
    "name": "pf_name", "age": "pf_age", "sex": "pf_sex", "height_cm": "pf_height", "weight_kg": "pf_weight",
    "activity_level": "pf_activity", "goal": "pf_goal", "diet": "pf_diet", "allergies": "pf_allergies",
    "cuisine": "pf_cuisine", "budget_weekly_inr": "pf_budget", "max_prep_time_min": "pf_prep",
}


def _load_into_widgets(profile: dict) -> None:
    for field, key in KEYS.items():
        st.session_state[key] = profile[field]


# Seed widget values once per session: saved profile if present, else demo scenario.
if "pf_seeded" not in st.session_state:
    _load_into_widgets(get_profile() or DEMO_PROFILE)
    st.session_state["pf_seeded"] = True

top_l, top_r = st.columns([3, 1])
top_l.caption("Pre-filled with the demo scenario (22-year-old vegetarian student, fat loss, ₹1,500/week, peanut allergy).")
top_r.button("Reset to demo scenario", on_click=_load_into_widgets, args=(DEMO_PROFILE,), **STRETCH)

with st.form("profile_form"):
    st.subheader("Body & lifestyle")
    c1, c2, c3, c4 = st.columns(4)
    name = c1.text_input("Name (optional)", key=KEYS["name"])
    age = c2.number_input("Age (years)", 18, 80, step=1, key=KEYS["age"])
    sex = c3.selectbox("Sex (for BMR formula)", SEX_OPTIONS, key=KEYS["sex"])
    activity = c4.selectbox("Activity level", list(ACTIVITY_LEVELS), key=KEYS["activity_level"])
    c1, c2 = st.columns(2)
    height = c1.number_input("Height (cm)", 120.0, 220.0, step=1.0, key=KEYS["height_cm"])
    weight = c2.number_input("Weight (kg)", 30.0, 200.0, step=0.5, key=KEYS["weight_kg"])

    st.subheader("Goal & diet")
    c1, c2, c3 = st.columns(3)
    goal = c1.radio("Goal", GOALS, key=KEYS["goal"])
    diet = c2.selectbox("Dietary preference", DIET_PREFERENCES, key=KEYS["diet"])
    cuisine = c3.selectbox("Cuisine preference", CUISINES, key=KEYS["cuisine"])
    allergies = st.multiselect(
        "Allergies / foods to avoid", list(ALLERGENS), format_func=ALLERGENS.get, key=KEYS["allergies"],
        help="Any food containing these allergens is excluded from your plan.",
    )

    st.subheader("Budget & time")
    c1, c2 = st.columns(2)
    budget = c1.number_input("Weekly food budget (₹)", 300, 20000, step=100, key=KEYS["budget_weekly_inr"])
    prep = c2.slider("Maximum cooking time per dish (minutes)", 5, 60, step=5, key=KEYS["max_prep_time_min"])

    submitted = st.form_submit_button("💾 Save profile", type="primary", **STRETCH)

if submitted:
    profile = {
        "name": name.strip(), "age": int(age), "sex": sex, "height_cm": float(height), "weight_kg": float(weight),
        "activity_level": activity, "goal": goal, "diet": diet, "allergies": list(allergies),
        "cuisine": cuisine, "budget_weekly_inr": int(budget), "max_prep_time_min": int(prep),
    }
    errors, warnings = validate_profile(profile)
    if errors:
        for e in errors:
            st.error(e)
    else:
        save_profile(profile)
        st.success("Profile saved. Preview below - run the agents on the Nutrition Targets page for the full breakdown.")
        for w in warnings:
            st.warning(w)

profile = get_profile()
if profile:
    st.subheader("Quick targets preview")
    t = compute_targets(profile)
    cols = st.columns(4)
    with cols[0]: metric_card("Target calories", f"{t['calories']:,} kcal", f"TDEE {t['tdee']:,} kcal")
    with cols[1]: metric_card("Protein", f"{t['protein_g']} g", f"{t['macro_pct']['protein']}% of calories")
    with cols[2]: metric_card("Carbs / Fat", f"{t['carbs_g']} / {t['fat_g']} g", f"{t['macro_pct']['carbs']}% / {t['macro_pct']['fat']}%")
    with cols[3]: metric_card("BMI", f"{t['bmi']}", t["bmi_category"])
    st.write("")
    explain_card(t["reasoning"]["calories"])
    st.page_link("ui/pages/3_Nutrition_Targets.py", label="Run agents & see full breakdown", icon="🎯")
else:
    st.info("No profile saved yet. Review the values above and press **Save profile**.")

st.write("")
disclaimer()
