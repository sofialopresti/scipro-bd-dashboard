"""Does cutting cost hurt quality? Signal Radar A/B test on the same saved sources.

Variants (all run on data/sample_sources.json so every variant sees identical news + trials):
  baseline        - 60 articles, Analyst low effort, Ranker medium effort (the app's settings)
  half_articles   - only the 30 newest articles go to the Analyst  (fewer input tokens)
  ranker_low      - Ranker at low reasoning effort                  (fewer output tokens)
  both_cuts       - both cost cuts together

Quality is measured without the model grading itself:
  - picks confirmed hiring right now (Hiring Check, from real job boards)
  - picks matching the baseline's picks (overlap)
  - picks the Verifier had to reject (lower is better)

Usage (costs roughly 4 variants x repeats x ~11k tokens):
    .venv/bin/python experiment_cost_quality.py --repeats 2
Results go to data/experiment_results.json and print as a table.
"""

import argparse
import json
import time
from datetime import datetime

import hiring_check
import signal_radar as sr

VARIANTS = {
    "baseline": {"articles": 60, "ranker_effort": "medium"},
    "half_articles": {"articles": 30, "ranker_effort": "medium"},
    "ranker_low": {"articles": 60, "ranker_effort": "low"},
    "both_cuts": {"articles": 30, "ranker_effort": "low"},
}
RESULTS = sr.DATA_DIR / "experiment_results.json"


def run_variant(name, settings, sample, client, repeat):
    run_id = f"experiment-{name}-{repeat}-{datetime.now().strftime('%H%M%S')}"
    saved_effort = sr.CONFIG["agents"]["ranker"]["reasoning_effort"]
    sr.CONFIG["agents"]["ranker"]["reasoning_effort"] = settings["ranker_effort"]
    usage, start = [], time.time()
    try:
        articles, _ = sr.sanitize_articles(sample["articles"][:settings["articles"]])
        # call_llm logs every call; collect this run's rows from the usage log afterwards
        signals = sr.analyze_news(client, articles, run_id)
        picks = sr.rank_companies(client, signals, sample["trial_sponsors"], [], run_id)
        kept, rejected = sr.verify(picks, signals, sample["trial_sponsors"], [])
        sites = sr.find_websites(client, [p["company"] for p in kept], run_id)
        for p in kept:
            p["website"] = sites.get(p["company"])
        hiring_check.check_picks(kept, save=False)
    finally:
        sr.CONFIG["agents"]["ranker"]["reasoning_effort"] = saved_effort
    for line in sr.USAGE_LOG.read_text().splitlines():
        row = json.loads(line)
        if row["run_id"] == run_id:
            usage.append(row)
    return {
        "variant": name, "repeat": repeat, "seconds": round(time.time() - start, 1),
        "input_tokens": sum(u["input_tokens"] for u in usage),
        "output_tokens": sum(u["output_tokens"] for u in usage),
        "picks": [p["company"] for p in kept],
        "rejected": len(rejected),
        "hiring": sum(1 for p in kept if p["hiring"]["status"] == "hiring"),
        "checkable": sum(1 for p in kept if p["hiring"]["status"] in ("hiring", "no_open_roles")),
        "matching_roles": sum(1 for p in kept if p["hiring"].get("matching_roles")),
    }


def summarize(rows):
    baseline_picks = {sr.normalize(c) for r in rows if r["variant"] == "baseline" for c in r["picks"]}
    table = []
    for name in VARIANTS:
        rs = [r for r in rows if r["variant"] == name]
        if not rs:
            continue
        n = len(rs)
        overlap = sum(len({sr.normalize(c) for c in r["picks"]} & baseline_picks) for r in rs) / n
        table.append({
            "variant": name, "runs": n,
            "tokens": round(sum(r["input_tokens"] + r["output_tokens"] for r in rs) / n),
            "input": round(sum(r["input_tokens"] for r in rs) / n),
            "output": round(sum(r["output_tokens"] for r in rs) / n),
            "seconds": round(sum(r["seconds"] for r in rs) / n, 1),
            "hiring_rate": f"{sum(r['hiring'] for r in rs)}/{sum(r['checkable'] for r in rs)}",
            "role_match": f"{sum(r['matching_roles'] for r in rs)}/{sum(r['hiring'] for r in rs)}",
            "avg_rejected": round(sum(r["rejected"] for r in rs) / n, 1),
            "overlap_with_baseline": round(overlap, 1),
        })
    return table


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--variants", nargs="*", default=list(VARIANTS))
    args = parser.parse_args()
    sample = json.loads(sr.SAMPLE_FILE.read_text())
    client = sr.get_client()
    rows = []
    for repeat in range(1, args.repeats + 1):
        for name in args.variants:
            print(f"running {name} #{repeat}...")
            rows.append(run_variant(name, VARIANTS[name], sample, client, repeat))
    table = summarize(rows)
    RESULTS.write_text(json.dumps({"ran_at": datetime.now().isoformat(timespec="seconds"),
                                   "sample_articles": len(sample["articles"]),
                                   "rows": rows, "summary": table}, indent=2))
    print(f"\n{'variant':15}{'tokens':>9}{'input':>8}{'output':>8}{'secs':>7}"
          f"{'hiring':>9}{'role match':>12}{'rejected':>10}{'overlap':>9}")
    for t in table:
        print(f"{t['variant']:15}{t['tokens']:>9,}{t['input']:>8,}{t['output']:>8,}{t['seconds']:>7}"
              f"{t['hiring_rate']:>9}{t['role_match']:>12}{t['avg_rejected']:>10}{t['overlap_with_baseline']:>9}")


if __name__ == "__main__":
    main()
