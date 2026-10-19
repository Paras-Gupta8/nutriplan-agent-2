"""Pure-Python nutrition engine: BMR, TDEE, BMI, calorie/macro/fiber targets.

No Streamlit imports here, so everything is unit-testable.
"""
from __future__ import annotations

# ---------------------------------------------------------------- options
ACTIVITY_LEVELS = {
    "Sedentary (little or no exercise)": 1.2,
    "Lightly active (1-3 days/week)": 1.375,
    "Moderately active (3-5 days/week)": 1.55,
    "Very active (6-7 days/week)": 1.725,
    "Extra active (physical job + training)": 1.9,
}
GOALS = ["Fat loss", "Maintenance", "Muscle gain"]
DIET_PREFERENCES = ["Vegetarian", "Eggetarian", "Non-vegetarian", "Vegan", "Jain"]
CUISINES = ["North Indian", "South Indian", "Mixed Indian"]
SEX_OPTIONS = ["Male", "Female"]
# canonical token (as stored in foods.csv) -> display label
ALLERGENS = {
    "peanut": "Peanut",
    "tree_nuts": "Tree nuts",
    "dairy": "Dairy",
    "gluten": "Gluten",
    "egg": "Egg",
    "soy": "Soy",
    "fish": "Fish",
    "shellfish": "Shellfish",
}

PROTEIN_G_PER_KG = {"Fat loss": 1.6, "Maintenance": 1.2, "Muscle gain": 1.8}
FAT_CALORIE_SHARE = 0.25
MIN_CALORIES = {"Male": 1500, "Female": 1200}  # safety floor
KCAL_PER_G = {"protein": 4, "carbs": 4, "fat": 9}

DEMO_PROFILE = {
    "name": "Demo Student",
    "age": 22,
    "sex": "Male",
    "height_cm": 172.0,
    "weight_kg": 70.0,
    "activity_level": "Lightly active (1-3 days/week)",
    "goal": "Fat loss",
    "diet": "Vegetarian",
    "allergies": ["peanut"],
    "cuisine": "North Indian",
    "budget_weekly_inr": 1500,
    "max_prep_time_min": 30,
}


# ---------------------------------------------------------------- core maths
def calculate_bmr(age: int, sex: str, height_cm: float, weight_kg: float) -> float:
    """Mifflin-St Jeor: 10W + 6.25H - 5A + (5 male | -161 female)."""
    base = 10 * weight_kg + 6.25 * height_cm - 5 * age
    return base + (5 if sex == "Male" else -161)


def calculate_tdee(bmr: float, activity_level: str) -> float:
    return bmr * ACTIVITY_LEVELS[activity_level]


def calculate_bmi(height_cm: float, weight_kg: float) -> float:
    m = height_cm / 100
    return weight_kg / (m * m)


def bmi_category(bmi: float) -> str:
    """WHO adult categories."""
    if bmi < 18.5:
        return "Underweight"
    if bmi < 25:
        return "Normal"
    if bmi < 30:
        return "Overweight"
    return "Obese"


def goal_adjustment(goal: str, tdee: float) -> int:
    """Fat loss: -300..-500 (20% of TDEE, clamped). Gain: +200..+300 (10%, clamped)."""
    if goal == "Fat loss":
        return -int(round(min(500, max(300, 0.20 * tdee)), -1))
    if goal == "Muscle gain":
        return int(round(min(300, max(200, 0.10 * tdee)), -1))
    return 0


# ---------------------------------------------------------------- validation
def validate_profile(p: dict) -> tuple[list[str], list[str]]:
    """Return (errors, warnings). Errors block saving; warnings are advisory."""
    errors, warnings = [], []
    if not 18 <= p["age"] <= 80:
        errors.append("Age must be between 18 and 80 (tool is intended for healthy adults).")
    if not 120 <= p["height_cm"] <= 220:
        errors.append("Height must be between 120 and 220 cm.")
    if not 30 <= p["weight_kg"] <= 200:
        errors.append("Weight must be between 30 and 200 kg.")
    if p["budget_weekly_inr"] < 300:
        errors.append("Weekly budget must be at least ₹300 to plan 7 days of meals.")
    if p["diet"] == "Eggetarian" and "egg" in p["allergies"]:
        errors.append("Diet is Eggetarian but egg allergy is selected - please resolve this conflict.")
    if p["diet"] == "Vegan" and "dairy" in p["allergies"]:
        warnings.append("Vegan diets already exclude dairy; the dairy allergy is redundant.")
    if len(p["allergies"]) >= 4:
        warnings.append("Many allergens selected - the food pool may be small and the plan may be repetitive.")
    if not errors:
        bmi = calculate_bmi(p["height_cm"], p["weight_kg"])
        if bmi < 18.5 and p["goal"] == "Fat loss":
            warnings.append(f"BMI is {bmi:.1f} (underweight) - a fat-loss goal is not advisable. Consider Maintenance or consult a dietitian.")
        if p["max_prep_time_min"] < 15:
            warnings.append("Very short cooking time limits food choices; expect simpler meals.")
    return errors, warnings


# ---------------------------------------------------------------- targets
def compute_targets(p: dict) -> dict:
    """Compute all nutrition targets plus human-readable reasoning for each."""
    bmr = calculate_bmr(p["age"], p["sex"], p["height_cm"], p["weight_kg"])
    mult = ACTIVITY_LEVELS[p["activity_level"]]
    tdee = bmr * mult
    adj = goal_adjustment(p["goal"], tdee)
    raw_cal = tdee + adj
    floor = MIN_CALORIES[p["sex"]]
    calories = max(raw_cal, floor)
    floored = raw_cal < floor

    protein_g = round(PROTEIN_G_PER_KG[p["goal"]] * p["weight_kg"])
    fat_g = round(calories * FAT_CALORIE_SHARE / KCAL_PER_G["fat"])
    carbs_g = max(0, round((calories - protein_g * 4 - fat_g * 9) / 4))
    fiber_g = max(25, round(14 * calories / 1000))
    bmi = calculate_bmi(p["height_cm"], p["weight_kg"])

    cal_round = int(round(calories))
    pct = lambda g, k: round(g * KCAL_PER_G[k] * 100 / calories)
    macro_pct = {
        "protein": pct(protein_g, "protein"),
        "carbs": pct(carbs_g, "carbs"),
        "fat": pct(fat_g, "fat"),
    }

    if adj < 0:
        goal_txt = f"your goal is fat loss, so a {abs(adj)} kcal daily deficit (about 20% of TDEE, kept within 300-500 kcal) is applied"
    elif adj > 0:
        goal_txt = f"your goal is muscle gain, so a {adj} kcal daily surplus (about 10% of TDEE, kept within 200-300 kcal) is added"
    else:
        goal_txt = "your goal is maintenance, so calories match your TDEE"

    reasoning = {
        "bmr": f"BMR is {bmr:.0f} kcal/day (Mifflin-St Jeor: 10×{p['weight_kg']:g} + 6.25×{p['height_cm']:g} - 5×{p['age']} {'+ 5' if p['sex']=='Male' else '- 161'}). This is what your body burns at rest.",
        "tdee": f"TDEE is {tdee:.0f} kcal/day = BMR × {mult} for '{p['activity_level']}'.",
        "calories": f"Your calorie target is {cal_round:,} kcal/day because your estimated maintenance requirement is {tdee:,.0f} kcal and {goal_txt}."
        + (f" The target was raised to the safety minimum of {floor} kcal." if floored else ""),
        "protein": f"Protein target is {protein_g} g/day ({PROTEIN_G_PER_KG[p['goal']]} g per kg body weight) to support satiety and muscle retention for a {p['goal'].lower()} goal.",
        "fat": f"Fat target is {fat_g} g/day, about {int(FAT_CALORIE_SHARE*100)}% of calories, for hormone health and absorption of fat-soluble vitamins.",
        "carbs": f"Carbohydrate target is {carbs_g} g/day - the calories left after protein and fat - to fuel activity.",
        "fiber": f"Fiber target is {fiber_g} g/day (14 g per 1,000 kcal, minimum 25 g) for digestion and fullness.",
        "bmi": f"BMI is {bmi:.1f} ({bmi_category(bmi)}). BMI is a rough screening measure and ignores muscle mass.",
    }

    return {
        "bmr": round(bmr),
        "tdee": round(tdee),
        "activity_multiplier": mult,
        "goal_adjustment_kcal": adj,
        "calories": cal_round,
        "protein_g": protein_g,
        "carbs_g": carbs_g,
        "fat_g": fat_g,
        "fiber_g": fiber_g,
        "bmi": round(bmi, 1),
        "bmi_category": bmi_category(bmi),
        "macro_pct": macro_pct,
        "calorie_floor_applied": floored,
        "reasoning": reasoning,
    }
