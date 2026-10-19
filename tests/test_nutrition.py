import pytest

from core.nutrition import (
    DEMO_PROFILE, calculate_bmi, calculate_bmr, calculate_tdee, compute_targets, validate_profile,
)


def test_bmr_male_and_female():
    assert calculate_bmr(22, "Male", 172, 70) == pytest.approx(10*70 + 6.25*172 - 5*22 + 5)
    assert calculate_bmr(22, "Female", 160, 55) == pytest.approx(10*55 + 6.25*160 - 5*22 - 161)


def test_tdee_multiplier():
    assert calculate_tdee(1600, "Sedentary (little or no exercise)") == pytest.approx(1920)


def test_bmi():
    assert calculate_bmi(170, 65) == pytest.approx(22.49, abs=0.01)


def test_demo_targets_fat_loss():
    t = compute_targets(DEMO_PROFILE)
    assert t["goal_adjustment_kcal"] in range(-500, -299)
    assert t["calories"] == t["tdee"] + t["goal_adjustment_kcal"] or abs(t["calories"] - (t["tdee"] + t["goal_adjustment_kcal"])) <= 1
    assert t["protein_g"] == round(1.6 * 70)
    # macros reconstruct to the calorie target (within rounding)
    kcal = t["protein_g"]*4 + t["carbs_g"]*4 + t["fat_g"]*9
    assert abs(kcal - t["calories"]) <= 10
    assert t["fiber_g"] >= 25


def test_muscle_gain_surplus_and_maintenance():
    gain = compute_targets({**DEMO_PROFILE, "goal": "Muscle gain"})
    assert 200 <= gain["goal_adjustment_kcal"] <= 300
    maint = compute_targets({**DEMO_PROFILE, "goal": "Maintenance"})
    assert maint["goal_adjustment_kcal"] == 0 and maint["calories"] == maint["tdee"]


def test_calorie_floor():
    tiny = {**DEMO_PROFILE, "sex": "Female", "weight_kg": 40, "height_cm": 140, "age": 70,
            "activity_level": "Sedentary (little or no exercise)"}
    t = compute_targets(tiny)
    assert t["calories"] >= 1200


def test_profile_validation():
    errors, _ = validate_profile({**DEMO_PROFILE, "age": 15})
    assert errors
    errors, _ = validate_profile({**DEMO_PROFILE, "diet": "Eggetarian", "allergies": ["egg"]})
    assert errors
    errors, warnings = validate_profile({**DEMO_PROFILE, "diet": "Vegan", "allergies": ["dairy"]})
    assert not errors and warnings
    assert validate_profile(DEMO_PROFILE) == ([], [])
