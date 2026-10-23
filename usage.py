"""Measured token usage per workflow (from data/token_usage.jsonl) and the firm-wide cost model.

Every model call is logged with its run_id. A "run" is one use of a workflow: one Signal Radar
scan, one Outreach Assistant turn, one pitch deck, or one job intake. Test and safety runs are
excluded so the averages reflect real use.
"""

import json
from collections import defaultdict

from signal_radar import CONFIG, USAGE_LOG

WORKFLOWS = {
    "radar": ("Signal Radar scan", {"analyst", "ranker", "linker"}, "analyst"),
    "email": ("Outreach Assistant turn (draft or revision)", {"writer"}, "writer"),
    "pitch": ("Pitch deck", {"researcher", "pitch_writer"}, "pitch_writer"),
    "intake": ("Job intake", {"intake_analyst", "spec_writer", "sourcing_strategist"}, "spec_writer"),
}
EXCLUDE_PREFIXES = ("test", "safety", "backfill", "manual", "experiment")


def runs():
    """{run_id: {"agents": set, "input", "cached", "output", "calls"}} for real (non-test) runs."""
    out = defaultdict(lambda: {"agents": set(), "input": 0, "cached": 0, "output": 0, "calls": 0})
    if not USAGE_LOG.exists():
        return {}
    for line in USAGE_LOG.read_text().splitlines():
        r = json.loads(line)
        if r["run_id"].startswith(EXCLUDE_PREFIXES):
            continue
        run = out[r["run_id"]]
        run["agents"].add(r["agent"])
        run["input"] += r["input_tokens"]
        run["cached"] += r["cached_input_tokens"]
        run["output"] += r["output_tokens"]
        run["calls"] += 1
    return out


def per_workflow():
    """Average measured tokens per run of each workflow."""
    all_runs = runs()
    result = {}
    for key, (label, agents, main) in WORKFLOWS.items():
        if key == "email":  # each writer call is one chat turn
            samples = []
            for line in USAGE_LOG.read_text().splitlines() if USAGE_LOG.exists() else []:
                r = json.loads(line)
                if r["agent"] == "writer" and not r["run_id"].startswith(EXCLUDE_PREFIXES):
                    samples.append({"input": r["input_tokens"], "cached": r["cached_input_tokens"],
                                    "output": r["output_tokens"], "calls": 1})
        else:
            samples = [r for r in all_runs.values() if main in r["agents"] and r["agents"] <= agents]
        n = len(samples)
        result[key] = {
            "label": label, "runs": n,
            "input": round(sum(s["input"] for s in samples) / n) if n else 0,
            "cached": round(sum(s["cached"] for s in samples) / n) if n else 0,
            "output": round(sum(s["output"] for s in samples) / n) if n else 0,
            "calls": round(sum(s["calls"] for s in samples) / n, 1) if n else 0,
        }
    return result


def cost_per_run(w, prices):
    """Dollar cost of one run given $/1M token prices, or None if prices aren't set."""
    if not prices or any(prices.get(k) in (None, "") for k in ("input", "cached_input", "output")):
        return None
    fresh = max(w["input"] - w["cached"], 0)
    return (fresh * prices["input"] + w["cached"] * prices["cached_input"]
            + w["output"] * prices["output"]) / 1_000_000


def firm_model(measured, prices, usage, team_size, shared_radar=True, workdays=21):
    """Monthly tokens and cost for one consultant and for teams of several sizes.

    usage = per consultant: radar scans/day, emails/day, turns/email, pitches/week, intakes/week.
    With shared_radar, one Signal Radar scan per day serves a whole team (the picks are the same),
    so radar cost is per team, not per consultant.
    """
    weeks = workdays / 5
    per_person = {
        "email": usage["emails_per_day"] * usage["turns_per_email"] * workdays,
        "pitch": usage["pitches_per_week"] * weeks,
        "intake": usage["intakes_per_week"] * weeks,
    }
    radar_per_month = usage["radar_scans_per_day"] * workdays

    def month(users):
        teams = max(1, round(users / team_size)) if shared_radar else users
        counts = {k: v * users for k, v in per_person.items()}
        counts["radar"] = radar_per_month * teams
        tokens = sum(counts[k] * (measured[k]["input"] + measured[k]["output"]) for k in counts)
        costs = [cost_per_run(measured[k], prices) for k in counts]
        dollars = None if None in costs else sum(counts[k] * c for k, c in zip(counts, costs))
        return {"users": users, "runs": counts, "tokens": tokens, "dollars": dollars}

    return month


def pricing():
    return CONFIG.get("pricing_per_million") or {}
