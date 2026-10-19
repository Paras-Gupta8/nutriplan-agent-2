from pathlib import Path

from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parent.parent


def _app():
    return AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()


def test_home_renders():
    at = _app()
    assert not at.exception


def test_profile_save_flow():
    at = _app()
    at.switch_page("ui/pages/2_Profile.py").run()
    assert not at.exception
    assert at.session_state["profile"] is None
    at.button[1].click().run()  # form submit (button[0] is 'Reset to demo')
    assert not at.exception, [e.value for e in at.exception]
    p = at.session_state["profile"]
    assert p["goal"] == "Fat loss" and p["allergies"] == ["peanut"] and p["budget_weekly_inr"] == 1500
    assert at.session_state["agent_log"][-1]["action"] == "profile_saved"


def test_conflicting_profile_rejected():
    at = _app()
    at.switch_page("ui/pages/2_Profile.py").run()
    at.selectbox(key="pf_diet").set_value("Eggetarian")
    at.multiselect(key="pf_allergies").set_value(["egg"])
    at.button[1].click().run()
    assert at.session_state["profile"] is None
    assert any("Eggetarian" in e.value for e in at.error)
