"""Shopping List page: aggregated ingredients, estimated quantities and cost."""
import streamlit as st

from core.report import (
    GROUP_ORDER, dish_df, load_ingredient_map, shopping_csv, shopping_df, shopping_json, shopping_list,
)
from core.state import get_profile, init_state
from ui.components import STRETCH, disclaimer, empty_state, lazy_tabs, metric_card, page_header
from ui.theme import apply_theme

init_state()
apply_theme()
page_header("Shopping List", "Your plan turned into groceries - aggregated ingredients, quantities and estimated cost.", "🛒")

profile, plan = get_profile(), st.session_state["meal_plan"]
if not profile:
    empty_state("No profile yet", "Save your profile first.", "ui/pages/2_Profile.py", "Open Profile & Goals")
    st.stop()
if plan is None:
    empty_state("No meal plan yet", "Generate a 7-day plan and your shopping list appears here.", "ui/pages/4_Meal_Plan.py", "Open Meal Plan")
    st.stop()

all_days = [d["day"] for d in plan["days"]]
days = st.multiselect("Days to shop for", all_days, default=all_days, format_func=lambda n: f"Day {n}")
if not days:
    st.info("Select at least one day.")
    st.stop()

sl = shopping_list(plan, load_ingredient_map(), days)
budget_share = profile["budget_weekly_inr"] * len(days) / 7
c = st.columns(4)
with c[0]: metric_card("Estimated total", f"₹{sl['total_cost_inr']:,.0f}", f"for {len(days)} day(s)")
with c[1]: metric_card("Budget for these days", f"₹{budget_share:,.0f}", f"₹{profile['budget_weekly_inr']:,}/week")
with c[2]: metric_card("Distinct ingredients", str(len(sl["ingredients"])), f"{len(sl['dishes'])} different dishes")
with c[3]: metric_card("Cost vs budget", f"{sl['total_cost_inr'] / budget_share:.0%}", "within budget" if sl["total_cost_inr"] <= budget_share else "over budget")
st.write("")
st.progress(min(sl["total_cost_inr"] / budget_share, 1.0), text=f"₹{sl['total_cost_inr']:,.0f} of ₹{budget_share:,.0f}")
if sl["outside_meals"]:
    st.info(f"{len(sl['outside_meals'])} meal(s) you ate out are not groceries and are excluded "
            f"(estimated restaurant spend ₹{sum(m['cost_inr'] for m in sl['outside_meals']):,.0f}).")
skipped = sum(1 for d in plan["days"] if d["day"] in days for m in d["meals"].values() if m.get("skipped"))
if skipped:
    st.caption(f"{skipped} skipped meal(s) were removed from the list.")

view = lazy_tabs(["🧺 By shopping group", "📋 All ingredients", "🍛 By dish"], "sl_view")
df = shopping_df(sl)
if view.startswith("🧺"):
    for g in GROUP_ORDER:
        part = df[df["Group"] == g].drop(columns="Group")
        if part.empty:
            continue
        with st.expander(f"{g} · {len(part)} item(s) · ₹{part['Est. cost (₹)'].sum():,.0f}", expanded=True):
            st.dataframe(part, hide_index=True, **STRETCH)
elif view.startswith("📋"):
    st.dataframe(df, hide_index=True, height=480, **STRETCH)
else:
    st.dataframe(dish_df(sl), hide_index=True, **STRETCH)
    st.caption("Dish-level view uses the food database's own serving costs.")

st.write("")
d1, d2 = st.columns(2)
d1.download_button("⬇️ Download shopping list (CSV)", shopping_csv(sl), file_name="nutriplan_shopping_list.csv", mime="text/csv", **STRETCH)
d2.download_button("⬇️ Download shopping list (JSON)", shopping_json(sl), file_name="nutriplan_shopping_list.json", mime="application/json", **STRETCH)
st.caption("Quantities are approximate raw or as-bought amounts, and costs are estimates (no live grocery prices). "
           "A dish's cost is split across its ingredients by weight and price class, so ingredient costs add up to the plan cost.")
disclaimer()
