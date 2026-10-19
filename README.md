# 🥗 NutriPlan Agent

**An agentic AI nutrition & diet assistant for personalized, budget-aware Indian meal planning.**

Five specialised agents and an orchestrator turn a user profile (goal, diet, allergies, cuisine, weekly budget, cooking time) into a
validated 7-day Indian meal plan, a shopping list and an explainable audit trail - and adapt the plan from daily feedback.
Everything runs locally: **no paid APIs, no internet, no database**. Ollama is optional and only rewrites explanations.

> ⚕️ Educational wellness tool - **not a medical device**. People with medical conditions should consult a doctor or registered dietitian.

## Run it

```bash
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt
streamlit run app.py
```

Press 🚀 **Run the Demo Scenario End-to-End** on the Overview page to test the complete workflow using a sample profile: a 22-year-old vegetarian student aiming for fat loss, with a weekly budget of ₹1,500, a peanut allergy, a North Indian food preference, and a maximum meal preparation time of 30 minutes. Within approximately one second, the app generates personalized nutrition targets, a 7-day meal plan, validation results, a shopping list, and a log of the process.
Tests: `pytest -q` (204 tests, ~20 s).

## The agents

| Agent | Responsibility | Method |
|---|---|---|
| **Planner** | Profile → JSON execution plan, food filters, feasibility check | Rules |
| **Nutrition** | BMR (Mifflin-St Jeor), TDEE, calorie target, protein/carbs/fat/fiber, with reasons | Pure Python |
| **Meal Planner** | 7 days from 22 templates, substitution, 3-stage portion scaling, cost; revises on feedback | Rules + templates |
| **Validator / Critic** | 9 weighted checks, 0-100 score, structured feedback; max 2 revision loops | Pure Python |
| **Adaptation** | Skipped meal, ate out, cheaper, dislike, higher protein, quicker meals; re-validates | Rules first; optional Ollama rewrites the explanation |

Workflow: `Profile → Planner → Nutrition → Meal Planner → Validator ⇄ Meal Planner (≤2) → Plan → Adaptation → Validator`.
Every step appends a structured entry (`timestamp, agent_name, action, input_summary, output_summary, status, details`) to the Agent Log.

## Pages

Overview (one-click demo, status, export center) · Profile & Goals · Nutrition Targets · Meal Plan · Validation · Shopping List ·
Daily Adaptation · Agent Log · Evaluation (six metrics + a reproducible benchmark).

## Data (all local)

`data/foods.csv` (100 Indian foods) · `data/meal_templates.json` (22 meal-day templates) · `data/substitution_rules.json` (45 rules) ·
`data/ingredient_map.json` (raw-ingredient quantities used by the shopping list).

## Exports

Meal plan CSV · shopping list CSV and JSON · validation report JSON · agent log JSON and CSV · benchmark JSON and CSV.

## Measured results (60-profile benchmark: 5 diets × 3 cuisines × 4 allergy sets, ₹1,500 budget)

| Metric | Result |
|---|---|
| Nutritional target accuracy | 3.8% mean calorie error · protein 98.6% of target · 100% of days within ±10% kcal |
| Constraint compliance (allergen, diet, cuisine/time, meals) | 100% of plans |
| Plans passing validation | 0% first pass → 76.7% after revision (85.7% of validator issues fixed) |
| Revisions required | 1.25 per plan |
| Plan generation time | ~40 ms (generate + validate + revise) |
| Budget compliance | 100% |
| Adaptation Agent | 360 requests, 0 crashes |

Run it yourself on the **Evaluation** page.

## Performance notes

Engine: ~30 ms for a complete plan. Streamlit pages build every `st.tabs` section on every run, so heavy sections use a lazy selector
(`ui.components.lazy_tabs`): Meal Plan renders in ~36 ms (was ~200 ms), Validation ~28 ms (was ~195 ms). The food database is loaded once
through `st.cache_data`.

## Project layout

```
app.py                       navigation, sidebar
core/    state · nutrition · meal_engine · validator · revision · adaptation · orchestrator · report · evaluation
agents/  planner · nutrition_agent · meal_planner · validator_agent · adaptation_agent
ui/      theme · components · pages/1_Overview … 9_Evaluation
data/    foods.csv · meal_templates.json · substitution_rules.json · ingredient_map.json
tests/   204 tests incl. test_final_acceptance.py (one test group per acceptance criterion)
```

## Limitations

- The food database is synthetic and approximate; nutrition varies by brand, preparation and portion.
- Prices are not live grocery prices; shopping quantities are approximate raw/as-bought estimates.
- Eating-out meals use a fixed estimate (editable by text, e.g. "ate out 900 kcal").
- Intended for healthy adults; not clinical diet therapy. No photo logging, no multi-user accounts, no persistence between sessions.
- Restricted profiles (vegan, Jain, South Indian with several allergies) have few eligible templates, so variety or protein can stay below target;
  these cases are surfaced as warnings rather than hidden.
- Tested on Streamlit 1.66; `requirements.txt` allows `>=1.40`.
