"""Central st.session_state management and structured agent logging."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime

import streamlit as st

VALID_STATUSES = {"success", "warning", "revision_required", "failed"}
STAGES = ["Planner", "Nutrition", "Meal Planner", "Validator", "Adaptation"]
STAGE_STATUSES = {"pending", "running", "done", "warning", "failed"}

DEFAULT_STATE = {
    "profile": None,            # saved user profile dict
    "targets": None,            # Nutrition Agent output
    "execution_plan": None,     # Planner Agent output
    "meal_plan": None,          # Meal Planner Agent output
    "validation": None,         # Validator Agent output
    "adaptation_history": [],   # Adaptation Agent records
    "agent_log": [],            # chronological structured log
    "workflow_status": "profile_pending",
    "workflow_stages": {st_: "pending" for st_ in STAGES},
    "eval_runs": [],            # per-generation metrics for the evaluation view
    "prefs": {"dislikes": []},
    "last_adaptation": None,    # latest Adaptation Agent result shown on the Daily Adaptation page  # standing food exclusions learned from daily feedback
}


def init_state() -> None:
    """Create any missing keys. Safe to call on every page."""
    for key, default in DEFAULT_STATE.items():
        if key not in st.session_state:
            st.session_state[key] = deepcopy(default)


def set_stage(stage: str, status: str) -> None:
    """Update one orchestrator stage (pending | running | done | warning | failed)."""
    if stage in STAGES and status in STAGE_STATUSES:
        st.session_state["workflow_stages"] = {**st.session_state["workflow_stages"], stage: status}


def get_profile() -> dict | None:
    return st.session_state.get("profile")


def log_event(
    agent_name: str,
    action: str,
    input_summary: str = "",
    output_summary: str = "",
    status: str = "success",
    details: str = "",
) -> dict:
    """Append one structured entry to the agent activity log."""
    if status not in VALID_STATUSES:
        status = "warning"
    entry = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "agent_name": agent_name,
        "action": action,
        "input_summary": input_summary,
        "output_summary": output_summary,
        "status": status,
        "details": details,
    }
    st.session_state["agent_log"].append(entry)
    return entry


def reset_plan_state() -> None:
    """Clear everything derived from the profile (used when the profile changes)."""
    for key in ("targets", "execution_plan", "meal_plan", "validation"):
        st.session_state[key] = None
    st.session_state["adaptation_history"] = []
    st.session_state["last_adaptation"] = None
    st.session_state["workflow_stages"] = {s_: "pending" for s_ in STAGES}


def save_profile(profile: dict) -> None:
    """Persist profile, invalidate stale downstream results, and log it."""
    had_plan = st.session_state.get("meal_plan") is not None
    st.session_state["profile"] = profile
    reset_plan_state()
    st.session_state["workflow_status"] = "profile_saved"
    log_event(
        "Orchestrator",
        "profile_saved",
        input_summary=f"{profile['age']}y {profile['sex']}, {profile['goal']}, {profile['diet']}",
        output_summary="Profile stored in session state; downstream results cleared."
        if had_plan else "Profile stored in session state.",
        status="success",
        details=f"allergies={profile['allergies']}, budget=₹{profile['budget_weekly_inr']}/week, "
                f"cuisine={profile['cuisine']}, max_prep={profile['max_prep_time_min']} min",
    )
