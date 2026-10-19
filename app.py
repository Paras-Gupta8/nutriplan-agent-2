"""NutriPlan Agent - entry point.  Run with:  streamlit run app.py"""
import streamlit as st

st.set_page_config(page_title="NutriPlan Agent", page_icon="🥗", layout="wide")

from core.state import get_profile, init_state  # noqa: E402
from ui.theme import apply_theme  # noqa: E402

init_state()
apply_theme()

pages = [
    st.Page("ui/pages/1_Overview.py", title="Overview", icon="🏠", default=True),
    st.Page("ui/pages/2_Profile.py", title="Profile & Goals", icon="👤"),
    st.Page("ui/pages/3_Nutrition_Targets.py", title="Nutrition Targets", icon="🎯"),
    st.Page("ui/pages/4_Meal_Plan.py", title="Meal Plan", icon="🍽️"),
    st.Page("ui/pages/5_Validation.py", title="Validation", icon="✅"),
    st.Page("ui/pages/6_Shopping_List.py", title="Shopping List", icon="🛒"),
    st.Page("ui/pages/7_Daily_Adaptation.py", title="Daily Adaptation", icon="🔄"),
    st.Page("ui/pages/8_Agent_Log.py", title="Agent Log", icon="📜"),
    st.Page("ui/pages/9_Evaluation.py", title="Evaluation", icon="📈"),
]
nav = st.navigation(pages)

with st.sidebar:
    st.markdown("### 🥗 NutriPlan Agent")
    st.caption("Personalized, budget-aware Indian meal planning")
    profile = get_profile()
    val = st.session_state["validation"]
    st.markdown(f"**Profile:** {'✅ saved' if profile else '⚠️ not set'}")
    st.markdown(f"**Plan:** {val['report']['overall_label'] if val else ('generated' if st.session_state['meal_plan'] else 'none yet')}")
    if st.session_state["adaptation_history"]:
        st.markdown(f"**Adaptations:** {len(st.session_state['adaptation_history'])}")
    st.divider()
    if st.button("🧹 Reset session", help="Clear the profile, plan, log and preferences"):
        st.session_state.clear()
        st.rerun()
    st.caption("Wellness education tool - not medical advice.")

nav.run()
