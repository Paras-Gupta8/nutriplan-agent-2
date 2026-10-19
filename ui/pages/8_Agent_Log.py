"""Agent Log page: chronological audit trail of every agent decision."""
import pandas as pd
import streamlit as st

from core.report import agent_log_csv, agent_log_json
from core.state import init_state
from ui.components import STATUS_EMOJI, STRETCH, empty_state, metric_card, page_header, stage_tracker, status_badge
from ui.theme import COLORS, apply_theme

init_state()
apply_theme()
page_header("Agent Log", "Chronological audit trail of every agent decision - what each agent saw, did and decided.", "📜")

log = st.session_state["agent_log"]
if not log:
    empty_state("No agent activity yet", "Save a profile and run the agents to see their activity here.", "ui/pages/2_Profile.py", "Open Profile & Goals")
    st.stop()

stage_tracker(st.session_state["workflow_stages"])
st.write("")
counts = pd.Series([e["status"] for e in log]).value_counts()
c = st.columns(5)
with c[0]: metric_card("Entries", str(len(log)), f"{len({e['agent_name'] for e in log})} agents")
for col, s in zip(c[1:], ["success", "warning", "revision_required", "failed"]):
    with col: metric_card(s.replace("_", " ").title(), str(int(counts.get(s, 0))))

st.write("")
agents = sorted({e["agent_name"] for e in log})
f1, f2, f3 = st.columns([2, 2, 3])
sel_agents = f1.multiselect("Agent", agents, default=agents)
sel_status = f2.multiselect("Status", ["success", "warning", "revision_required", "failed"], default=["success", "warning", "revision_required", "failed"])
query = f3.text_input("Search", placeholder="e.g. protein, paneer, Day 3, revision").strip().lower()
newest_first = st.toggle("Newest first", value=False)

rows = [e for e in log if e["agent_name"] in sel_agents and e["status"] in sel_status
        and (not query or query in " ".join(str(v) for v in e.values()).lower())]
if newest_first:
    rows = rows[::-1]
st.caption(f"Showing {len(rows)} of {len(log)} entries" + (" (newest first)" if newest_first else " (chronological)"))

st.dataframe(pd.DataFrame([{"Time": e["timestamp"][11:], "Agent": e["agent_name"], "Action": e["action"],
                            "Status": f"{STATUS_EMOJI.get(e['status'], '')} {e['status'].replace('_', ' ')}",
                            "Input": e["input_summary"], "Output": e["output_summary"]} for e in rows]),
             hide_index=True, height=340, **STRETCH)

with st.expander("Activity by agent"):
    st.bar_chart(pd.Series(pd.Series([e["agent_name"] for e in log]).value_counts(), name="entries"), color=COLORS["teal"])

st.subheader("Details")
for i, e in enumerate(rows, 1):
    with st.expander(f"{STATUS_EMOJI.get(e['status'], '')} {e['timestamp'][11:]} · {e['agent_name']} · {e['action']}"):
        st.markdown(status_badge(e["status"]), unsafe_allow_html=True)
        st.markdown(f"**Input:** {e['input_summary']}")
        st.markdown(f"**Output:** {e['output_summary']}")
        if e["details"]:
            st.code(e["details"], language=None)

d1, d2 = st.columns(2)
d1.download_button("⬇️ Download agent log (JSON)", agent_log_json(log), file_name="agent_log.json", mime="application/json", **STRETCH)
d2.download_button("⬇️ Download agent log (CSV)", agent_log_csv(log), file_name="agent_log.csv", mime="text/csv", **STRETCH)
