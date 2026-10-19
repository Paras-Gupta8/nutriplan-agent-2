"""Pure-Python plan validator (no LLM). Returns per-check results, a 0-100 score and structured revision feedback."""
from __future__ import annotations

from collections import defaultdict

import pandas as pd

from core.meal_engine import SLOT_SHARE, constraint_masks

CAL_TOLERANCE = 0.10        # daily calories within ±10% of target
PROTEIN_MIN_RATIO = 0.90    # daily protein at least 90% of target
BUDGET_WARN_BAND = 0.05     # up to 5% over budget is a warning, beyond is a failure
MAX_MAIN_REPEATS = 2        # a main dish may appear at most twice per week
REQUIRED_MEALS = ["Breakfast", "Lunch", "Dinner"]   # a missing evening snack is only a warning

# id, display name, weight (sum = 100), critical? (critical failures cannot be shipped silently)
CHECKS = [
    ("calories", "Daily calories within ±10%", 18, False),
    ("protein", "Protein ≥ 90% of target", 18, False),
    ("allergens", "No allergen-containing foods", 15, True),
    ("diet", "Dietary rules respected", 14, True),
    ("budget", "Weekly budget", 10, True),
    ("variety", "Meal variety", 8, False),
    ("meals", "Required meals present", 7, True),
    ("fiber", "Minimum daily fiber", 5, False),
    ("cooking_cuisine", "Cooking time & cuisine", 5, True),
]
STATUS_ICON = {"pass": "✅", "warning": "⚠️", "fail": "❌"}
OVERALL_LABEL = {"passed": "Passed", "passed_with_warnings": "Passed with warnings", "revision_required": "Revision required"}


def _items(plan: dict):
    for d in plan["days"]:
        for slot, meal in d["meals"].items():
            for it in meal["items"]:
                yield d["day"], slot, it


def _user_event(d: dict) -> bool:
    """True when the user reported skipping or eating out on this day, so a deviation is expected."""
    return bool(d.get("skipped_meals")) or any(m.get("ate_out") for m in d["meals"].values())


def _day_status(issues: list[dict], n: int) -> tuple[str, float]:
    """Status and credit for a per-day check; warning-level issues (user-skipped meals) cost half credit."""
    fails = [i for i in issues if i["severity"] == "fail"]
    warns = [i for i in issues if i["severity"] == "warning"]
    status = "fail" if fails else ("warning" if warns else "pass")
    return status, (n - len(fails) - 0.5 * len(warns)) / n


def _check(cid: str, status: str, credit: float, message: str, issues: list[dict], details: list[dict] | None = None) -> dict:
    meta = {c[0]: c for c in CHECKS}[cid]
    return {"id": cid, "name": meta[1], "weight": meta[2], "critical": meta[3], "status": status,
            "credit": round(max(0.0, min(1.0, credit)), 3), "message": message, "issues": issues, "details": details or []}


def validate_plan(plan: dict, targets: dict, constraints: dict, foods: pd.DataFrame) -> dict:
    days = plan["days"]
    n = max(len(days), 1)
    foods = foods.set_index("food_id", drop=False)
    masks = {k: v.to_dict() for k, v in constraint_masks(foods, constraints).items()}   # dict lookups beat .loc per item
    results: dict[str, dict] = {}

    # ---- calories
    cal_t, issues, rows = targets["calories"], [], []
    for d in days:
        c = d["totals"]["calories"]
        dev = (c - cal_t) / cal_t
        ok = abs(dev) <= CAL_TOLERANCE
        skipped = _user_event(d)
        rows.append({"Day": d["day"], "Calories": c, "Target": cal_t, "Deviation": f"{dev:+.1%}",
                     "Result": "pass" if ok else ("warning (reported meal change)" if skipped else "fail")})
        if not ok:
            diff = abs(c - cal_t * (1 + (CAL_TOLERANCE if dev > 0 else -CAL_TOLERANCE)))
            issues.append({"check": "calories", "severity": "warning" if skipped else "fail", "day": d["day"], "slot": None,
                           "action": "rebalance_calories",
                           "problem": f"Day {d['day']} calories are {c:.0f} kcal ({dev:+.0%} vs target {cal_t})."
                                      + (" You reported skipping or eating out, so a deviation is expected." if skipped else ""),
                           "suggestion": f"{'Reduce' if dev > 0 else 'Increase'} portions on Day {d['day']} by about {diff:.0f} kcal."})
    status, credit = _day_status(issues, n)
    results["calories"] = _check("calories", status, credit,
                                 "All 7 days within ±10% of the calorie target." if not issues else
                                 f"{len(issues)} day(s) outside ±10%: " + ", ".join(f"Day {i['day']}" for i in issues), issues, rows)

    # ---- protein
    p_t, issues, rows = targets["protein_g"], [], []
    floor = PROTEIN_MIN_RATIO * p_t
    for d in days:
        p = d["totals"]["protein_g"]
        ok = p >= floor
        skipped = _user_event(d)
        rows.append({"Day": d["day"], "Protein (g)": p, "Minimum (g)": round(floor, 1), "% of target": f"{p / p_t:.0%}",
                     "Result": "pass" if ok else ("warning (reported meal change)" if skipped else "fail")})
        if not ok:
            issues.append({"check": "protein", "severity": "warning" if skipped else "fail", "day": d["day"], "slot": None,
                           "action": "increase_protein", "value": p,
                           "problem": f"Day {d['day']} protein is {floor - p:.1f} g below the minimum ({p:.0f} g vs {floor:.0f} g)."
                                      + (" You reported skipping or eating out, so a deviation is expected." if skipped else ""),
                           "suggestion": "Swap a low-protein side or snack for a protein-dense food such as roasted chana or soya.",
                           "shortfall_g": round(floor - p, 1)})
    status, credit = _day_status(issues, n)
    results["protein"] = _check("protein", status, credit,
                                f"All days provide ≥ {floor:.0f} g protein (90% of {p_t} g)." if not issues else
                                f"{len(issues)} day(s) below {floor:.0f} g: " + ", ".join(f"Day {i['day']} ({i['value']:.0f} g)" for i in issues),
                                issues, rows)

    # ---- allergens / diet / cooking+cuisine (item level)
    a_issues, d_issues, c_issues, total_items = [], [], [], 0
    for day, slot, it in _items(plan):
        if it.get("external"):      # eating-out estimate: not a database food, nothing to check
            continue
        total_items += 1
        fid = it["food_id"]
        if fid not in masks["diet"]:
            c_issues.append({"check": "cooking_cuisine", "severity": "fail", "day": day, "slot": slot, "action": "replace_item",
                             "food_id": fid, "food_name": it["food_name"], "problem": f"Day {day} {slot}: unknown food '{it['food_name']}'.",
                             "suggestion": "Replace with a food from the database."})
            continue
        def issue(check, msg):
            return {"check": check, "severity": "fail", "day": day, "slot": slot, "action": "replace_item", "food_id": fid,
                    "food_name": it["food_name"], "problem": f"Day {day} {slot}: {it['food_name']} {msg}",
                    "suggestion": f"Replace {it['food_name']} with a safe alternative of similar nutrition."}
        if not masks["allergen"][fid]:
            a_issues.append(issue("allergens", "contains an excluded allergen."))
        if not masks["diet"][fid]:
            d_issues.append(issue("diet", f"violates the {constraints['diet']} diet."))
        if not masks["cuisine"][fid]:
            c_issues.append(issue("cooking_cuisine", f"does not match the '{constraints['cuisine']}' cuisine preference."))
        if not masks["prep"][fid]:
            c_issues.append(issue("cooking_cuisine", f"needs {it['prep_time_min']} min of prep (limit {constraints['max_prep_time_min']} min)."))
    ex = ", ".join(sorted({i["food_name"] for i in a_issues})[:4])
    results["allergens"] = _check("allergens", "pass" if not a_issues else "fail", 0.0 if a_issues else 1.0,
                                  f"No excluded allergens ({', '.join(constraints['excluded_allergens']) or 'none selected'}) found in {total_items} items."
                                  if not a_issues else f"{len(a_issues)} item(s) contain excluded allergens: {ex}.", a_issues)
    ex = ", ".join(sorted({i["food_name"] for i in d_issues})[:4])
    results["diet"] = _check("diet", "pass" if not d_issues else "fail", 0.0 if d_issues else 1.0,
                             f"All foods comply with the {constraints['diet']} diet." if not d_issues else
                             f"{len(d_issues)} item(s) break the {constraints['diet']} diet: {ex}.", d_issues)
    results["cooking_cuisine"] = _check("cooking_cuisine", "pass" if not c_issues else "fail",
                                        1 - len(c_issues) / max(total_items, 1),
                                        f"Every dish fits '{constraints['cuisine']}' and ≤ {constraints['max_prep_time_min']} min prep."
                                        if not c_issues else f"{len(c_issues)} item(s) break cuisine or cooking-time limits.", c_issues)

    # ---- budget
    cost, budget = sum(d["totals"]["cost_inr"] for d in days), constraints["budget_weekly_inr"]
    over = cost - budget
    b_issues = []
    if over <= 0:
        status, credit, msg = "pass", 1.0, f"Weekly cost ₹{cost:,.0f} is within the ₹{budget:,} budget."
    elif over <= BUDGET_WARN_BAND * budget:
        status, credit, msg = "warning", 0.5, f"Weekly cost ₹{cost:,.0f} is ₹{over:,.0f} ({over / budget:.1%}) over the ₹{budget:,} budget."
    else:
        status, credit, msg = "fail", 0.0, f"Weekly cost ₹{cost:,.0f} exceeds the ₹{budget:,} budget by ₹{over:,.0f} ({over / budget:.0%})."
    if over > 0:
        b_issues.append({"check": "budget", "severity": "fail" if status == "fail" else "warning", "day": None, "slot": None,
                         "action": "reduce_cost", "problem": msg, "suggestion": "Replace high-cost foods (e.g. paneer, nuts) with cheaper protein sources like soya chunks or chana.",
                         "excess_inr": round(over, 1)})
    results["budget"] = _check("budget", status, credit, msg, b_issues,
                               [{"Day": d["day"], "Cost (₹)": d["totals"]["cost_inr"]} for d in days])

    # ---- variety
    occ = defaultdict(list)
    for d in days:
        for slot in SLOT_SHARE:
            for it in d["meals"].get(slot, {}).get("items", []):
                if it["role"] == "main":
                    occ[it["food_name"]].append((d["day"], slot))
    v_issues, warn_notes = [], []
    for dish, where in occ.items():
        for day, slot in sorted(where)[MAX_MAIN_REPEATS:]:
            v_issues.append({"check": "variety", "severity": "fail", "day": day, "slot": slot, "action": "replace_repeated_main",
                             "food_name": dish, "problem": f"{dish} is used {len(where)} times this week (limit {MAX_MAIN_REPEATS}); Day {day} {slot} is an extra repeat.",
                             "suggestion": f"Replace {dish} on Day {day} {slot} with a different dish of similar protein."})
        for slot in SLOT_SHARE:
            ds = sorted(d for d, s in where if s == slot)
            if any(b - a == 1 for a, b in zip(ds, ds[1:])):
                warn_notes.append(f"{dish} is the {slot.lower()} main on consecutive days.")
    distinct = max(len(occ), 1)
    bad_dishes = len({i["food_name"] for i in v_issues})
    if v_issues:
        status, msg = "fail", f"{bad_dishes} main dish(es) repeat more than {MAX_MAIN_REPEATS}×: " + ", ".join(sorted({i['food_name'] for i in v_issues})) + "."
    elif warn_notes:
        status, msg = "warning", "Variety is acceptable but " + warn_notes[0].lower() + (f" (+{len(warn_notes) - 1} more)" if len(warn_notes) > 1 else "")
    else:
        status, msg = "pass", f"{len(occ)} distinct main dishes; none repeats more than {MAX_MAIN_REPEATS}×."
    results["variety"] = _check("variety", status, min(0.7, 1 - bad_dishes / distinct) if v_issues else (0.9 if warn_notes else 1.0), msg, v_issues,
                                [{"Main dish": k, "Times used": len(v)} for k, v in sorted(occ.items(), key=lambda kv: -len(kv[1]))])

    # ---- required meals
    m_issues, present, total = [], 0, 0
    for d in days:
        for slot in SLOT_SHARE:
            total += 1
            if d["meals"].get(slot, {}).get("items") or slot in d.get("skipped_meals", []):
                present += 1
            else:
                crit = slot in REQUIRED_MEALS
                m_issues.append({"check": "meals", "severity": "fail" if crit else "warning", "day": d["day"], "slot": slot,
                                 "action": "fill_missing_meal", "problem": f"Day {d['day']} has no {slot.lower()}.",
                                 "suggestion": f"Add a protein-rich {slot.lower()} that fits the constraints."})
    hard = [i for i in m_issues if i["severity"] == "fail"]
    results["meals"] = _check("meals", "fail" if hard else ("warning" if m_issues else "pass"), present / max(total, 1),
                              "Breakfast, lunch, evening snack and dinner present every day." if not m_issues else
                              f"{len(m_issues)} meal(s) missing.", m_issues)

    # ---- fiber
    f_t, issues, rows = targets["fiber_g"], [], []
    for d in days:
        f = d["totals"]["fiber_g"]
        ok = f >= f_t
        skipped = _user_event(d)
        rows.append({"Day": d["day"], "Fiber (g)": f, "Minimum (g)": f_t,
                     "Result": "pass" if ok else ("warning (reported meal change)" if skipped else "fail")})
        if not ok:
            issues.append({"check": "fiber", "severity": "warning" if skipped else "fail", "day": d["day"], "slot": None,
                           "action": "increase_fiber",
                           "problem": f"Day {d['day']} fiber is {f:.0f} g, below the {f_t} g minimum."
                                      + (" You reported skipping or eating out, so a deviation is expected." if skipped else ""),
                           "suggestion": "Add a salad or vegetable side, or swap refined grains for millets or brown rice."})
    status, credit = _day_status(issues, n)
    results["fiber"] = _check("fiber", status, credit,
                              f"Every day meets the {f_t} g fiber minimum." if not issues else f"{len(issues)} day(s) below {f_t} g fiber.",
                              issues, rows)

    # ---- assemble
    checks = [results[c[0]] for c in CHECKS]
    score = round(sum(c["weight"] * c["credit"] for c in checks), 1)
    n_fail = sum(c["status"] == "fail" for c in checks)
    n_warn = sum(c["status"] == "warning" for c in checks)
    overall = "revision_required" if n_fail else ("passed_with_warnings" if n_warn else "passed")
    # budget overruns (even within the warning band) are revision-worthy; other warnings are advisory only
    issues_all = [i for c in checks for i in c["issues"] if i["severity"] == "fail" or i["check"] == "budget"]
    return {
        "overall_status": overall, "overall_label": OVERALL_LABEL[overall], "score": score,
        "counts": {"pass": len(checks) - n_fail - n_warn, "warning": n_warn, "fail": n_fail},
        "critical_failures": [c["id"] for c in checks if c["critical"] and c["status"] == "fail"],
        "checks": checks, "issues": issues_all,
        "feedback": [{k: v for k, v in i.items() if k in ("check", "day", "slot", "problem", "suggestion")} for i in issues_all],
    }
