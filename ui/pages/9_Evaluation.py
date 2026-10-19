"""Internal evaluation view: session metrics and a reproducible benchmark."""
import json

import pandas as pd
import streamlit as st

from core.adaptation import load_rules
from core.evaluation import BENCH_SIZES, benchmark_profiles, rows_to_df, run_benchmark, summarize_session
from core.meal_engine import load_foods, load_templates
from core.state import init_state
from ui.components import STRETCH, disclaimer, empty_state, lazy_tabs, metric_card, page_header
from ui.theme import COLORS, apply_theme

init_state()
apply_theme()
page_header("Evaluation", "How accurate, compliant, fast and self-correcting the agents are.", "📈")

view = lazy_tabs(["🧪 Benchmark (many profiles)", "📌 This session"], "ev_view")


def cards(items):
    for i in range(0, len(items), 3):
        cols = st.columns(3)
        for col, (label, value, sub) in zip(cols, items[i:i + 3]):
            with col:
                metric_card(label, value, sub)
        st.write("")


if view.startswith("🧪"):
    st.write("Runs the full agent pipeline on many profiles (diet × cuisine × allergy set, all at the demo budget of ₹1,500) and measures the "
             "six evaluation metrics. Everything runs locally and is deterministic.")
    c1, c2 = st.columns([3, 2])
    size = c1.radio("Benchmark size", list(BENCH_SIZES), format_func=BENCH_SIZES.get, horizontal=True, index=1)
    with_ad = c2.checkbox("Also test the Adaptation Agent (6 requests per profile)", value=True)
    if st.button("▶ Run benchmark", type="primary"):
        bar = st.progress(0.0, text="Running agents...")
        st.session_state["benchmark"] = run_benchmark(benchmark_profiles(size), load_foods(), load_templates(), load_rules(), with_ad,
                                                      progress=lambda f: bar.progress(f, text=f"Running agents... {f:.0%}"))
        bar.empty()
    bm = st.session_state.get("benchmark")
    if not bm:
        empty_state("No benchmark run yet", "Choose a size and press **Run benchmark**. The quick run takes a few seconds.")
    else:
        s = bm["summary"]
        st.caption(f"{s['profiles']} profile(s) evaluated in {bm['seconds']} s · {bm['generated_at']}")
        ta, cc, va, rv, tm, bd = s["target_accuracy"], s["constraint_compliance"], s["validation"], s["revisions"], s["timing_ms"], s["budget"]
        cards([
            ("Nutritional target accuracy", f"{ta['mean_calorie_error_pct']}% kcal error",
             f"protein {ta['mean_protein_pct_of_target']}% of target · {ta['days_calories_within_10pct_final']}% of days within ±10% kcal"),
            ("Constraint compliance", f"{cc['hard_constraints_pct']}%", f"plans with no allergen/diet/cuisine/prep/meal violation · {cc['no_critical_failures_pct']}% no critical failure"),
            ("Plans passing validation", f"{va['after_revision_pct']}%", f"after revision (first pass {va['first_pass_pct']}%) · {va['fully_passed_pct']}% fully clean"),
            ("Revisions required", f"{rv['mean']} per plan", f"{rv['distribution'][0]}×0 · {rv['distribution'][1]}×1 · {rv['distribution'][2]}×2 · {rv['issue_fix_rate_pct']}% of issues fixed"),
            ("Average plan generation time", f"{tm['mean_total']} ms", f"generate {tm['mean_generation']} ms + validate/revise {tm['mean_validate_revise']} ms · max {tm['max_total']} ms"),
            ("Budget compliance", f"{bd['within_budget_final_pct']}%", f"within budget after revision (first pass {bd['within_budget_first_pass_pct']}%) · mean ₹{bd['mean_weekly_cost_inr']:,}/wk"),
        ])
        left, right = st.columns(2)
        with left:
            st.markdown("**Final validation status**")
            st.bar_chart(pd.Series({k.replace("_", " ").title(): v for k, v in va["status_counts"].items()}, name="plans"), color=COLORS["green"])
        with right:
            st.markdown("**Revisions used**")
            st.bar_chart(pd.Series({f"{k} revision(s)": v for k, v in rv["distribution"].items()}, name="plans"), color=COLORS["teal"])
        if s["adaptation"]:
            a = s["adaptation"]
            st.markdown("**Adaptation quality**")
            st.caption(f"{a['runs']} adaptation runs · {a['crashes']} crashes · {a['changed_pct']}% changed the plan · "
                       f"{a['no_failed_checks_pct']}% of changed plans had no failed check · {a['new_critical_failures']} introduced a new critical failure")
            st.dataframe(pd.DataFrame([{"Request": k, "Runs": v["runs"], "Changed plan %": v["changed_pct"],
                                        "No failed checks %": v["no_failed_checks_pct"], "Crashes": v["crashes"]} for k, v in a["per_phrase"].items()]),
                         hide_index=True, **STRETCH)
        st.markdown("**Per-profile results**")
        df = rows_to_df(bm["rows"])
        st.dataframe(df, hide_index=True, height=360, **STRETCH)
        d1, d2 = st.columns(2)
        d1.download_button("⬇️ Download benchmark (JSON)", json.dumps({"summary": s, "rows": bm["rows"]}, indent=2, default=str),
                           file_name="nutriplan_benchmark.json", mime="application/json", **STRETCH)
        d2.download_button("⬇️ Download per-profile table (CSV)", df.to_csv(index=False), file_name="nutriplan_benchmark.csv", mime="text/csv", **STRETCH)
        with st.expander("How each metric is measured"):
            st.markdown(
                "- **Nutritional target accuracy:** mean absolute error between the plan's average daily calories and the target, average protein as % of target, "
                "and the share of days within ±10% calories.\n"
                "- **Constraint compliance:** share of plans where the allergen, diet, cuisine/cooking-time and required-meal checks all pass.\n"
                "- **Plans passing validation:** share with zero failed checks, on the first attempt and after up to two revisions.\n"
                "- **Revisions required:** revisions used per plan, and the share of Validator issues the Meal Planner fixed.\n"
                "- **Average plan generation time:** wall-clock time to generate the plan plus validate and revise it.\n"
                "- **Budget compliance:** share of plans whose estimated weekly cost is within the ₹1,500 budget.")
else:
    runs = st.session_state["eval_runs"]
    ss = summarize_session(runs)
    if not ss:
        empty_state("No plans generated yet", "Generate a meal plan and its metrics are recorded here.", "ui/pages/4_Meal_Plan.py", "Open Meal Plan")
    else:
        cards([
            ("Plans generated", str(ss["plans"]), f"{ss['validated']} validated"),
            ("Avg generation time", f"{ss['mean_generation_ms']} ms", "Meal Planner only"),
            ("Nutritional accuracy", f"{ss['mean_calorie_error_pct']}% kcal error", f"protein {ss['mean_protein_pct_of_target']}% of target"),
            ("Passing validation", f"{ss['after_revision_pct']}%" if ss["validated"] else "-", f"first pass {ss['first_pass_pct']}%" if ss["validated"] else "not validated yet"),
            ("Avg revisions", f"{ss['mean_revisions']}" if ss["validated"] else "-", "per plan"),
            ("Budget compliance", f"{ss['budget_pct']}%", f"constraint compliance {ss['constraint_pct']}%" if ss["validated"] else ""),
        ])
        st.dataframe(pd.DataFrame(runs), hide_index=True, **STRETCH)
        st.caption(f"Adaptations applied this session: {len(st.session_state['adaptation_history'])}")
st.write("")
disclaimer()
