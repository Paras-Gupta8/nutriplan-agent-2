"""Rule-based daily adaptation. Decisions are deterministic; an LLM (Ollama) may only re-phrase the explanation."""
from __future__ import annotations

import json
import os
import re
import urllib.request
from collections import Counter

import pandas as pd
import streamlit as st

from core.meal_engine import (
    DATA_DIR, FIXED_CATEGORIES, _adjust_calories, _finalize_day, _improve_protein, _make_item, _mains, _sum,
    filter_foods, summarize_plan,
)
from core.revision import _day_from_plan, _group, _rescale

RULES_PATH = DATA_DIR / "substitution_rules.json"
SLOT_ORDER = ["Breakfast", "Lunch", "Snack", "Dinner"]
INTENT_KINDS = {"dislike": ["swap"], "cheaper": ["cost", "swap"], "quick": ["time", "swap"], "protein": ["protein", "swap"]}
RECOVER_SHARE = 0.7           # share of a skipped meal's calories/protein recovered in later meals
CHEAPER_TARGET_SHARE = 0.12   # "make it cheaper" aims to save about 12% of the scope's cost
PROTEIN_BOOST = 1.25          # "high protein" aims for 125% of the daily protein target
DEFAULT_QUICK_MINUTES = 15
# kcal, protein, carbs, fat, fiber, ₹ for a typical restaurant meal (editable via "ate out 900 kcal")
EATING_OUT_ESTIMATE = {
    "Breakfast": (450, 14, 60, 16, 4, 120), "Lunch": (750, 25, 95, 28, 7, 200),
    "Snack": (300, 8, 38, 13, 2, 80), "Dinner": (800, 28, 100, 30, 7, 250),
}


# ------------------------------------------------------------------ rules
def parse_rules(path=RULES_PATH) -> list[dict]:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)["rules"]


@st.cache_data(show_spinner=False)
def load_rules() -> list[dict]:
    return parse_rules()


def _rule_hit(rule: dict, name: str) -> bool:
    low = name.lower()
    return name in rule.get("names", []) or any(k in low for k in rule.get("keywords", []))


# ------------------------------------------------------------------ feedback classification
_SLOTS = [("evening snack", "Snack"), ("breakfast", "Breakfast"), ("lunch", "Lunch"), ("dinner", "Dinner"),
          ("supper", "Dinner"), ("snacks", "Snack"), ("snack", "Snack")]
_RX = {
    "skipped": re.compile(r"\b(skip(?:ped|ping)?|miss(?:ed|ing)|didn'?t\s+(?:have|eat)|did\s+not\s+(?:have|eat)|haven'?t\s+(?:had|eaten))\b"),
    "ate_out": re.compile(r"\b(ate\s+out|eat(?:ing)?\s+out|dined\s+out|dining\s+out|restaurant|takeaway|take-away|zomato|swiggy|ordered|outside\s+food|canteen|party)\b"),
    "cheaper": re.compile(r"\b(cheap(?:er)?|less\s+expensive|more\s+affordable|reduce\s+(?:the\s+)?cost|lower\s+(?:the\s+)?cost|save\s+money|too\s+expensive|expensive|budget[- ]friendly|cut\s+costs?)\b"),
    "quick": re.compile(r"\b(quick(?:er)?|fast(?:er)?|short\s+on\s+time|in\s+a\s+hurry|less\s+time|\d{1,2}\s*(?:min|mins|minute|minutes))\b"),
    "protein": re.compile(r"\b(high[- ]protein|more\s+protein|protein[- ]rich|boost\s+protein|increase\s+protein|extra\s+protein|protein)\b"),
    "dislike": re.compile(r"\b(?:(?:don'?t|do\s+not|dont|doesn'?t)\s+(?:really\s+)?(?:like|want|eat|enjoy)|dislike|hate|sick\s+of|tired\s+of|no\s+more|remove|without|avoid|replace|swap|allergic\s+to)\s+(.+)"),
}
_STOP = {"the", "and", "any", "all", "not", "like", "don", "dont", "make", "it", "me", "my", "today", "tomorrow", "day", "days", "week",
         "plan", "please", "anymore", "really", "much", "food", "foods", "dish", "dishes", "meal", "meals", "cheaper", "more", "less",
         "with", "from", "for", "this", "that", "them", "want", "eat", "enjoy", "also", "some", "tonight", "again"}
_WEEK = re.compile(r"\b(whole|entire|all)\s+(week|plan|days)\b|\bweekly\b|\bthe\s+(week|plan)\b|\bmy\s+plan\b|\bwhole\s+plan\b")


def _norm(tok: str) -> str:
    tok = tok.lower()
    if tok == "soya":
        return "soy"
    return tok[:-1] if len(tok) > 3 and tok.endswith("s") and not tok.endswith("ss") else tok


def _name_tokens(foods: pd.DataFrame) -> dict[str, set[str]]:
    return {n: {_norm(t) for t in re.findall(r"[A-Za-z]+", n)} for n in foods["food_name"]}


def resolve_foods(phrase: str, tokens: dict[str, set[str]]) -> list[str]:
    words = {_norm(w) for w in re.findall(r"[a-z]+", phrase.lower()) if w not in _STOP and len(w) >= 3}
    return sorted(n for n, toks in tokens.items() if words & toks)


def parse_feedback(text: str, foods: pd.DataFrame, ui_day: int = 1, ui_slot: str | None = None) -> dict:
    """Classify free-text feedback into actions (intent + day scope + meal + foods). Pure rules, no LLM."""
    tokens = _name_tokens(foods)
    clauses = [c.strip() for c in re.split(r"\s*(?:,|;|\.|!|\band\b|\bbut\b|\bthen\b|\balso\b|&)\s*", text.lower()) if c.strip()]
    actions, notes, carry_days = [], [], None
    for cl in clauses:
        days = None
        if _WEEK.search(cl):
            days = list(range(1, 8))
        elif (m := re.search(r"\bday\s*(\d)\b", cl)) and 1 <= int(m.group(1)) <= 7:
            days = [int(m.group(1))]
        elif "tomorrow" in cl:
            days = [min(ui_day + 1, 7)]
        elif re.search(r"\b(today|tonight)\b", cl):
            days = [ui_day]
        carry_days = days or carry_days
        slot = next((s for w, s in _SLOTS if re.search(rf"\b{w}\b", cl)), None)
        intents = []
        m_dis = _RX["dislike"].search(cl)
        if m_dis:
            intents.append("dislike")
        for k in ("skipped", "ate_out", "quick", "cheaper", "protein"):
            if _RX[k].search(cl) and not (k == "protein" and m_dis):
                intents.append(k)
        if "ate_out" in intents and "skipped" in intents:
            intents.remove("skipped")          # "ate out instead of lunch" is an eating-out event
        if not intents:
            prev = actions[-1] if actions else None
            if prev and prev["intent"] in ("skipped", "ate_out") and slot:   # "skipped lunch and dinner"
                actions.append({**prev, "slot": slot})
            elif prev and prev["intent"] == "dislike":                      # "don't like paneer and dal"
                extra = resolve_foods(cl, tokens)
                prev["foods"] = sorted(set(prev["foods"]) | set(extra))
            continue
        for intent in intents:
            act = {"intent": intent, "slot": slot or (ui_slot if intent in ("skipped", "ate_out") else None),
                   "days": days or carry_days, "foods": [], "minutes": None, "kcal": None, "clause": cl}
            if intent == "dislike":
                act["foods"] = resolve_foods(m_dis.group(1), tokens)
                act["persist_hint"] = "allergic" in cl
                if "allergic" in cl:
                    notes.append("For real allergies, please also add the allergen on the Profile page so it is excluded everywhere.")
            if intent == "quick":
                mm = re.search(r"(\d{1,2})\s*(?:min|mins|minute|minutes)\b", cl)
                act["minutes"] = int(mm.group(1)) if mm else DEFAULT_QUICK_MINUTES
            if intent == "ate_out":
                mk = re.search(r"(\d{3,4})\s*(?:kcal|cal|calories)", cl)
                act["kcal"] = int(mk.group(1)) if mk else None
            actions.append(act)
    for a in actions:
        explicit = a["days"] is not None
        if a["days"] is None:
            a["days"] = list(range(1, 8)) if a["intent"] in ("cheaper", "dislike") else [ui_day]
        a["scope"] = "week" if len(a["days"]) == 7 else "day"
        a["explicit_scope"] = explicit
        a["persist"] = a["intent"] == "dislike" and (a["scope"] == "week" or a.get("persist_hint", False))
    return {"text": text, "actions": actions, "unrecognized": not actions, "notes": notes}


# ------------------------------------------------------------------ applying feedback
class _Ctx:
    def __init__(self, plan, targets, constraints, foods, rules):
        self.targets, self.constraints, self.foods, self.rules = targets, constraints, foods, rules
        self.foods_by_id = {r["food_id"]: r for r in foods.to_dict("records")}
        self.plan_days = plan["days"]
        self.days = [_day_from_plan(d, self.foods_by_id) for d in plan["days"]]
        n = len(self.days)
        self.skipped = [set(d.get("skipped_meals", [])) for d in plan["days"]]
        self.skipinfo = [{s: m.get("skipped_items") for s, m in d["meals"].items() if m.get("skipped")} for d in plan["days"]]
        self.ate_out = [{s: m.get("replaced_items") for s, m in d["meals"].items() if m.get("ate_out")} for d in plan["days"]]
        flagged = [bool(self.skipped[i] or self.ate_out[i]) for i in range(n)]
        self.goal_cal = [(_sum(self.days[i], "cal1") if flagged[i] else targets["calories"]) for i in range(n)]
        self.goal_p = [(_sum(self.days[i], "p1") if flagged[i] else targets["protein_g"]) for i in range(n)]
        self.band = [0.05] * n
        self.changes, self.messages, self.touched = [], [], set()
        self.notes = {i: [] for i in range(n)}
        self.counts = Counter(m for d in self.days for m in _mains(d))
        self.excluded = set(constraints.get("excluded_foods", []))
        self.new_dislikes: set[str] = set()
        self.rebuild_rows()

    def rebuild_rows(self):
        c = {**self.constraints, "excluded_foods": sorted(self.excluded)}
        self.rows = filter_foods(self.foods, c).to_dict("records")
        self.rows_by_name = {r["food_name"]: r for r in self.rows}


def _freeze(day: dict, slots: list[str]) -> list[dict]:
    flagged = []
    for s in slots:
        for it in day.get(s, []):
            if not it["fixed"]:
                it["fixed"] = True
                flagged.append(it)
    return flagged


def _unfreeze(items: list[dict]) -> None:
    for it in items:
        it["fixed"] = False


def _frozen_slots(ctx: _Ctx, di: int) -> list[str]:
    marks = [SLOT_ORDER.index(s) for s in (ctx.skipped[di] | set(ctx.ate_out[di]))]
    return SLOT_ORDER[: max(marks) + 1] if marks else []


def _rescale_day(ctx: _Ctx, di: int) -> None:
    day = ctx.days[di]
    held = _freeze(day, _frozen_slots(ctx, di))
    _adjust_calories(day, ctx.goal_cal[di])
    _improve_protein(day, ctx.goal_p[di], ctx.goal_cal[di], ctx.band[di])
    _unfreeze(held)


def _candidates(ctx: _Ctx, day: dict, slot: str, it: dict, intent: str, minutes: int | None = None) -> list[dict]:
    orig = ctx.foods_by_id.get(it["food_id"])
    if orig is None or it.get("external"):
        return []
    names = {x["food_name"] for x in day[slot]}
    cal_total, cur_cost, cur_p = it["cal1"] * it["servings"], it["cost1"] * it["servings"], it["p1"] * it["servings"]
    pool, seen = [], set()
    for ridx, rule in enumerate(ctx.rules):
        if rule["kind"] in INTENT_KINDS[intent] and _rule_hit(rule, orig["food_name"]):
            for aidx, an in enumerate(rule["alternatives"]):
                r = ctx.rows_by_name.get(an)
                if r and an not in seen:
                    seen.add(an)
                    pool.append((0, ridx, aidx, r, rule))
    if not (intent == "protein" and orig["category"] == "Grain"):      # never trade a staple for a heavy dish
        for r in ctx.rows:
            if r["food_name"] not in seen and _group(r["category"]) == _group(orig["category"]):
                pool.append((1, 0, 0, r, None))
    out = []
    for fallback, ridx, aidx, r, rule in pool:
        if slot not in r["meal_type"] or r["food_name"] in names or r["food_name"] == orig["food_name"]:
            continue
        if r["category"] in FIXED_CATEGORIES and not it["fixed"]:
            continue
        serv = _rescale(None, r, cal_total)
        new_p, new_cost = r["protein_g"] * serv, r["cost_inr"] * serv
        dist = abs(r["calories"] - orig["calories"]) / max(orig["calories"], 1) + 0.5 * abs(r["protein_g"] - orig["protein_g"]) / max(orig["protein_g"], 1)
        benefit, ok = 0.0, True
        if intent == "cheaper":
            benefit = cur_cost - new_cost
            ok = benefit > 0.5 and new_p >= 0.75 * cur_p
        elif intent == "quick":
            benefit = orig["prep_time_min"] - r["prep_time_min"]
            ok = r["prep_time_min"] <= minutes and benefit > 0
        elif intent == "protein":
            benefit = new_p - cur_p
            ok = benefit >= 3 and r["protein_g"] / max(r["calories"], 1) >= 1.15 * it["p1"] / max(it["cal1"], 1)
        if not ok:
            continue
        over = it["role"] == "main" and ctx.counts[r["food_name"]] >= 2
        if over and intent != "dislike":
            continue          # keep the "no main dish more than twice" rule unless the user explicitly asked for a removal
        key = (over, fallback, ridx, aidx, dist) if intent == "dislike" else (over, fallback, -benefit, ridx, aidx)
        out.append({"key": key, "row": r, "serv": serv, "rule": rule, "benefit": benefit})
    out.sort(key=lambda c: c["key"])
    return out


def _swap_item(ctx: _Ctx, di: int, slot: str, it: dict, cand: dict, intent: str) -> dict:
    r, serv, rule = cand["row"], cand["serv"], cand["rule"]
    new = _make_item(r, serv, it["role"], it["fixed"] and r["category"] in FIXED_CATEGORIES)
    if it["role"] == "main":
        ctx.counts[it["food_name"]] -= 1
        ctx.counts[r["food_name"]] += 1
    delta = {"calories": r["calories"] * serv - it["cal1"] * it["servings"], "protein_g": r["protein_g"] * serv - it["p1"] * it["servings"],
             "cost_inr": r["cost_inr"] * serv - it["cost1"] * it["servings"], "prep_time_min": r["prep_time_min"] - it["prep_time_min"]}
    change = {"kind": intent, "day": di + 1, "slot": slot, "from": it["food_name"], "to": r["food_name"],
              "rule_id": rule["id"] if rule else None,
              "reason": rule["reason"] if rule else "it is the closest nutritional match among foods that fit your constraints",
              "delta": {k: round(v, 1) for k, v in delta.items()}}
    ctx.changes.append(change)
    ctx.touched.add(di)
    return new


def _h_dislike(ctx: _Ctx, act: dict) -> None:
    names = set(act["foods"])
    if not names:
        ctx.messages.append(f"I couldn't find a food in the database matching “{act['clause']}”. Try a name like paneer, soya, rice or curd.")
        return
    ctx.excluded |= names
    if act["persist"]:
        ctx.new_dislikes |= names
    ctx.rebuild_rows()
    hits = 0
    for d_no in act["days"]:
        di, day = d_no - 1, ctx.days[d_no - 1]
        for slot in SLOT_ORDER:
            new_items = []
            for it in day.get(slot, []):
                if it["food_name"] not in names:
                    new_items.append(it)
                    continue
                hits += 1
                cands = _candidates(ctx, day, slot, it, "dislike")
                if cands:
                    new_items.append(_swap_item(ctx, di, slot, it, cands[0], "dislike"))
                elif it["role"] == "main":
                    new_items.append(it)
                    ctx.messages.append(f"No suitable replacement for {it['food_name']} on Day {d_no} {slot.lower()}; it was kept.")
                else:
                    ctx.changes.append({"kind": "dislike", "day": d_no, "slot": slot, "from": it["food_name"], "to": "(removed)", "rule_id": None,
                                        "reason": "no suitable replacement fit your constraints, so the side was removed",
                                        "delta": {"calories": round(-it["cal1"] * it["servings"], 1), "protein_g": round(-it["p1"] * it["servings"], 1),
                                                  "cost_inr": round(-it["cost1"] * it["servings"], 1), "prep_time_min": 0}})
                    ctx.touched.add(di)
            day[slot] = new_items
    if not hits:
        ctx.messages.append(f"{', '.join(sorted(names)[:3])}{'…' if len(names) > 3 else ''} is not in the selected days, so nothing changed"
                            + (" - it will be avoided in future plans." if act["persist"] else "."))


def _best_over_scope(ctx: _Ctx, scope: list[int], intent: str, minutes=None, skip_slots=frozenset()):
    best = None
    for di in scope:
        day = ctx.days[di]
        for slot in SLOT_ORDER:
            if (di, slot) in skip_slots:
                continue
            for it in day.get(slot, []):
                if it["fixed"] or it.get("external"):
                    continue
                c = _candidates(ctx, day, slot, it, intent, minutes)
                if c and (best is None or c[0]["benefit"] > best[0]["benefit"]):
                    best = (c[0], di, slot, it)
    return best


def _replace_in_place(ctx, di, slot, it, cand, intent):
    items = ctx.days[di][slot]
    items[items.index(it)] = _swap_item(ctx, di, slot, it, cand, intent)


def _h_cheaper(ctx: _Ctx, act: dict) -> None:
    scope = [d - 1 for d in act["days"]]
    target = CHEAPER_TARGET_SHARE * sum(_sum(ctx.days[di], "cost1") for di in scope)
    saved = n = 0
    while saved < target and n < 12:
        best = _best_over_scope(ctx, scope, "cheaper")
        if best is None:
            break
        cand, di, slot, it = best
        _replace_in_place(ctx, di, slot, it, cand, "cheaper")
        saved += cand["benefit"]
        n += 1
    if n == 0:
        ctx.messages.append("I couldn't find cheaper alternatives that keep your protein up and fit your constraints.")


def _h_quick(ctx: _Ctx, act: dict) -> None:
    limit = act["minutes"]
    stuck = []
    for d_no in act["days"]:
        di, day = d_no - 1, ctx.days[d_no - 1]
        for slot in SLOT_ORDER:
            for it in list(day.get(slot, [])):
                if it.get("external") or it["prep_time_min"] <= limit:
                    continue
                c = _candidates(ctx, day, slot, it, "quick", limit)
                if c:
                    _replace_in_place(ctx, di, slot, it, c[0], "quick")
                else:
                    stuck.append(f"{it['food_name']} (Day {d_no} {slot.lower()}, {it['prep_time_min']} min)")
    if stuck:
        ctx.messages.append(f"No quicker alternative within {limit} min was found for: {', '.join(stuck)}.")
    elif not any(c["kind"] == "quick" for c in ctx.changes):
        ctx.messages.append(f"Every dish on the selected day already takes {limit} minutes or less.")


def _h_protein(ctx: _Ctx, act: dict) -> None:
    for d_no in act["days"]:
        di = d_no - 1
        used = set()                      # at most one swap per meal keeps meals coherent
        for _ in range(3):
            best = _best_over_scope(ctx, [di], "protein", skip_slots=used)
            if best is None:
                break
            cand, _, slot, it = best
            used.add((di, slot))
            _replace_in_place(ctx, di, slot, it, cand, "protein")
        ctx.goal_p[di] = max(ctx.goal_p[di], PROTEIN_BOOST * ctx.targets["protein_g"])
        ctx.goal_cal[di] = max(ctx.goal_cal[di], ctx.targets["calories"])
        ctx.band[di] = 0.08
        ctx.touched.add(di)


def _meal_totals(items: list[dict]) -> dict:
    return {k: sum(i[f] * i["servings"] for i in items) for k, f in (("calories", "cal1"), ("protein_g", "p1"), ("cost_inr", "cost1"))}


def _portion_diff(before: list[tuple], day: dict, slots: list[str]) -> list[str]:
    now = {(s, i["food_name"]): i["servings"] for s in slots for i in day.get(s, [])}
    return [f"{n} {b:g} → {now[(s, n)]:g}" for s, n, b in before if (s, n) in now and now[(s, n)] != b]


def _h_skipped(ctx: _Ctx, act: dict) -> None:
    slot = act["slot"]
    if not slot:
        ctx.messages.append("Which meal did you skip? Say e.g. “I skipped lunch” or pick the meal in the form.")
        return
    di = act["days"][0] - 1
    day, d_no = ctx.days[di], di + 1
    if not day.get(slot):
        ctx.messages.append(f"Day {d_no} {slot.lower()} is already empty or skipped.")
        return
    lost = _meal_totals(day[slot])
    names = ", ".join(i["food_name"] for i in day[slot])
    ctx.skipinfo[di][slot] = ctx.plan_days[di]["meals"][slot]["items"]
    day[slot] = []
    ctx.skipped[di].add(slot)
    later = [s for s in SLOT_ORDER[SLOT_ORDER.index(slot) + 1:] if day.get(s)]
    diffs = []
    if later:
        before = [(s, i["food_name"], i["servings"]) for s in later for i in day[s]]
        ctx.goal_cal[di] = ctx.targets["calories"] - (1 - RECOVER_SHARE) * lost["calories"]
        ctx.goal_p[di] = ctx.targets["protein_g"] - (1 - RECOVER_SHARE) * lost["protein_g"]
        held = _freeze(day, [s for s in SLOT_ORDER if s not in later])
        _adjust_calories(day, ctx.goal_cal[di])
        _improve_protein(day, ctx.goal_p[di], ctx.goal_cal[di], 0.05)
        _unfreeze(held)
        diffs = _portion_diff(before, day, later)
    else:
        ctx.goal_cal[di], ctx.goal_p[di] = _sum(day, "cal1"), _sum(day, "p1")
    ctx.changes.append({"kind": "skipped", "day": d_no, "slot": slot, "from": names, "to": "(skipped)", "rule_id": None,
                        "reason": (f"you skipped it, so later meals were scaled up to recover about {int(RECOVER_SHARE * 100)}% of the lost nutrition"
                                   if later else f"{slot.lower()} is the last meal of the day, so the shortfall cannot be recovered; "
                                                 "consider a light protein-rich snack if you are hungry"),
                        "delta": {"calories": round(-lost["calories"], 1), "protein_g": round(-lost["protein_g"], 1),
                                  "cost_inr": round(-lost["cost_inr"], 1), "prep_time_min": 0},
                        "portion_changes": diffs})
    ctx.touched.add(di)


def _h_ate_out(ctx: _Ctx, act: dict) -> None:
    slot = act["slot"]
    if not slot:
        ctx.messages.append("Which meal did you eat out? Say e.g. “I ate out for dinner” or pick the meal in the form.")
        return
    di = act["days"][0] - 1
    day, d_no = ctx.days[di], di + 1
    old = day.get(slot, [])
    names = ", ".join(i["food_name"] for i in old) or "(nothing planned)"
    lost = _meal_totals(old)
    kcal, p, c, f, fib, cost = EATING_OUT_ESTIMATE[slot]
    k = (act["kcal"] / kcal) if act.get("kcal") else 1.0
    food = {"food_id": f"EXT-{slot}", "food_name": "Restaurant / outside meal (estimated)", "category": "Eating out",
            "serving_label": "1 meal", "serving_grams": 350 * k, "calories": kcal * k, "protein_g": p * k, "carbs_g": c * k,
            "fat_g": f * k, "fiber_g": fib * k, "cost_inr": cost * k, "prep_time_min": 0}
    item = _make_item(food, 1, "side", True)
    item["external"] = True
    ctx.ate_out[di][slot] = ctx.plan_days[di]["meals"][slot]["items"] if slot in ctx.plan_days[di]["meals"] else []
    day[slot] = [item]
    later = [s for s in SLOT_ORDER[SLOT_ORDER.index(slot) + 1:] if day.get(s)]
    diffs = []
    ctx.goal_cal[di], ctx.goal_p[di] = ctx.targets["calories"], ctx.targets["protein_g"]
    if later:
        before = [(s, i["food_name"], i["servings"]) for s in later for i in day[s]]
        held = _freeze(day, [s for s in SLOT_ORDER if s not in later])
        _adjust_calories(day, ctx.goal_cal[di])
        _improve_protein(day, ctx.goal_p[di], ctx.goal_cal[di], 0.05)
        _unfreeze(held)
        diffs = _portion_diff(before, day, later)
    ctx.changes.append({"kind": "ate_out", "day": d_no, "slot": slot, "from": names, "to": f"Restaurant meal (≈{kcal * k:.0f} kcal, estimated)",
                        "rule_id": None,
                        "reason": ("restaurant nutrition is only an estimate; later meals were adjusted to keep the day near target" if later
                                   else "restaurant nutrition is only an estimate and this was the last meal of the day, so nothing could be offset"),
                        "delta": {"calories": round(kcal * k - lost["calories"], 1), "protein_g": round(p * k - lost["protein_g"], 1),
                                  "cost_inr": round(cost * k - lost["cost_inr"], 1), "prep_time_min": 0},
                        "portion_changes": diffs})
    ctx.touched.add(di)


_HANDLERS = {"dislike": _h_dislike, "skipped": _h_skipped, "ate_out": _h_ate_out, "quick": _h_quick, "cheaper": _h_cheaper, "protein": _h_protein}


def scope_totals(plan: dict, days: list[int]) -> dict:
    sel = [d for d in plan["days"] if d["day"] in days]
    keys = ("calories", "protein_g", "carbs_g", "fat_g", "fiber_g", "cost_inr")
    return {k: round(sum(d["totals"][k] for d in sel), 1) for k in keys} | {"days": len(sel)}


def apply_feedback(plan: dict, actions: list[dict], targets: dict, constraints: dict, foods: pd.DataFrame, rules: list[dict]):
    """Apply parsed actions. Returns (new_plan, changes, messages, new_dislikes)."""
    ctx = _Ctx(plan, targets, constraints, foods, rules)
    p0 = [_sum(d, "p1") for d in ctx.days]
    for act in actions:
        _HANDLERS[act["intent"]](ctx, act)
    for di in sorted(ctx.touched):
        if any(c["day"] == di + 1 and c["kind"] not in ("skipped", "ate_out") for c in ctx.changes) or ctx.band[di] != 0.05 \
                or any(c["day"] == di + 1 and c["kind"] in ("skipped", "ate_out") for c in ctx.changes):
            _rescale_day(ctx, di)
    for act in actions:
        if act["intent"] == "protein":
            for d_no in act["days"]:
                gain = _sum(ctx.days[d_no - 1], "p1") - p0[d_no - 1]
                if gain < 8:
                    ctx.messages.append(f"Day {d_no} was already near the highest protein that fits its calorie target (+{max(gain, 0):.0f} g).")
    for c in ctx.changes:
        ctx.notes[c["day"] - 1].append(change_sentence(c))
    new_days = []
    for di, (old, day) in enumerate(zip(plan["days"], ctx.days)):
        fin = _finalize_day(day)
        for slot, items in ctx.skipinfo[di].items():
            fin["meals"][slot].update(skipped=True, skipped_items=items)
        for slot, repl in ctx.ate_out[di].items():
            fin["meals"][slot].update(ate_out=True, replaced_items=repl)
        entry = {**old, **fin, "skipped_meals": sorted(ctx.skipped[di], key=SLOT_ORDER.index)}
        if ctx.notes[di]:
            entry["adaptation_notes"] = [*old.get("adaptation_notes", []), *ctx.notes[di]]
        new_days.append(entry)
    new_plan = {**plan, "days": new_days, "adapted": True, "adaptation_count": plan.get("adaptation_count", 0) + 1,
                **summarize_plan(new_days, targets, constraints)}
    return new_plan, ctx.changes, ctx.messages, sorted(ctx.new_dislikes)


# ------------------------------------------------------------------ explanations
def _delta_phrase(d: dict) -> str:
    parts = []
    if abs(d["cost_inr"]) >= 1:
        parts.append(f"saves ₹{abs(d['cost_inr']):.0f}" if d["cost_inr"] < 0 else f"costs ₹{d['cost_inr']:.0f} more")
    if abs(d["protein_g"]) >= 2:
        parts.append(f"{d['protein_g']:+.0f} g protein")
    if d.get("prep_time_min", 0) <= -3:
        parts.append(f"{abs(d['prep_time_min'])} min quicker")
    return f" ({', '.join(parts)})" if parts else ""


def change_sentence(c: dict) -> str:
    where = f"Day {c['day']} {c['slot'].lower()}"
    if c["kind"] == "skipped":
        extra = f" Portions changed: {'; '.join(c['portion_changes'])}." if c.get("portion_changes") else ""
        return f"{where} was skipped (about {abs(c['delta']['calories']):.0f} kcal and {abs(c['delta']['protein_g']):.0f} g protein): {c['reason']}.{extra}"
    if c["kind"] == "ate_out":
        extra = f" Portions changed: {'; '.join(c['portion_changes'])}." if c.get("portion_changes") else ""
        return f"{where} was replaced by {c['to']}: {c['reason']}.{extra}"
    return f"{where}: {c['from']} was replaced with {c['to']} because {c['reason']}{_delta_phrase(c['delta'])}."


def build_explanation(changes: list[dict], messages: list[str], before: dict, after: dict) -> str:
    """Built-in (mock) explanation - always available, no network or LLM needed."""
    lines = [change_sentence(c) for c in changes] + list(messages)
    if changes:
        n = max(before["days"], 1)
        dc, dp, dk = after["calories"] - before["calories"], after["protein_g"] - before["protein_g"], after["cost_inr"] - before["cost_inr"]
        lines.append(f"Net effect over {n} day(s): {dc:+.0f} kcal, {dp:+.0f} g protein, {dk:+.0f} ₹.")
    return "\n".join(lines)


# ------------------------------------------------------------------ optional Ollama (explanations only)
OLLAMA_URL = os.environ.get("NUTRIPLAN_OLLAMA_URL", "http://localhost:11434")


def ollama_models(timeout: float = 0.7) -> list[str]:
    """Names of locally installed Ollama models, or [] when Ollama is not running."""
    try:
        with urllib.request.urlopen(f"{OLLAMA_URL}/api/tags", timeout=timeout) as r:
            return [m["name"] for m in json.load(r).get("models", [])]
    except Exception:
        return []


def _numbers(text: str) -> list[str]:
    return sorted(re.findall(r"\d+(?:\.\d+)?", text))


def rewrite_with_ollama(text: str, model: str | None = None, timeout: float = 20) -> str | None:
    """Friendly rewrite via a local model. Returns None on any problem, including changed numbers."""
    try:
        model = model or os.environ.get("NUTRIPLAN_OLLAMA_MODEL") or (ollama_models() or [None])[0]
        if not model:
            return None
        prompt = ("Rewrite the following nutrition-plan update in friendly, concise plain language for the user. "
                  "Keep every number, food name and day exactly as written. Do not add advice or new facts. "
                  "Output only the rewritten text.\n\n" + text)
        req = urllib.request.Request(f"{OLLAMA_URL}/api/generate", method="POST", headers={"Content-Type": "application/json"},
                                     data=json.dumps({"model": model, "prompt": prompt, "stream": False, "options": {"temperature": 0.3}}).encode())
        with urllib.request.urlopen(req, timeout=timeout) as r:
            out = (json.load(r).get("response") or "").strip()
        if not out or len(out) > 3 * len(text) + 200 or _numbers(out) != _numbers(text):
            return None          # empty, runaway, or any number added/removed/changed -> reject
        return out
    except Exception:
        return None


def friendly_explanation(text: str, use_ollama: bool) -> tuple[str, str]:
    """(text, source) where source is 'ollama' or 'rules'. Never raises."""
    if use_ollama:
        out = rewrite_with_ollama(text)
        if out:
            return out, "ollama"
    return text, "rules"
