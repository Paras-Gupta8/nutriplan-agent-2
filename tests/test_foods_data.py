import pandas as pd

from core.meal_engine import REQUIRED_COLUMNS, parse_foods

ALLOWED_ALLERGENS = {"none", "peanut", "tree_nuts", "dairy", "gluten", "egg", "soy", "fish", "shellfish"}


def test_foods_schema_and_size():
    df = parse_foods()
    assert len(df) == 100
    assert list(df.columns) == REQUIRED_COLUMNS
    assert df["food_id"].is_unique


def test_values_are_sane():
    df = parse_foods()
    assert (df["calories"] > 0).all() and (df["cost_inr"] > 0).all()
    # Atwater cross-check: calories within 30% of 4P+4C+9F (fiber/rounding tolerance)
    est = df["protein_g"]*4 + df["carbs_g"]*4 + df["fat_g"]*9
    diff = ((df["calories"] - est).abs() / df["calories"])
    assert (diff < 0.35).all(), df.loc[diff >= 0.35, ["food_name", "calories"]].assign(est=est)


def test_allergen_tokens_and_veg_flags():
    df = parse_foods()
    for toks in df["allergens"]:
        assert set(toks) <= ALLOWED_ALLERGENS
    # anything containing egg/fish/shellfish must be non-veg
    for _, r in df.iterrows():
        if {"egg", "fish", "shellfish"} & set(r["allergens"]):
            assert not r["is_veg"], r["food_name"]


def test_coverage_for_demo_case():
    df = parse_foods()
    pool = df[df["is_veg"] & ~df["allergens"].apply(lambda a: "peanut" in a)
              & (df["cuisine"].isin(["North Indian", "Pan-Indian"])) & (df["prep_time_min"] <= 30)]
    for mt in ["Breakfast", "Lunch", "Dinner", "Snack"]:
        assert pool["meal_type"].apply(lambda m: mt in m).sum() >= 8, mt
