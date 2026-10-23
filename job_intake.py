"""Job Intake: rough hiring-manager call notes -> a ready-to-run search.

Pipeline (one run):
  1. Intake Analyst (LLM)       - structures the notes: role, level, must-haves, budget, gaps.
  2. Spec Writer (LLM)          - clean job spec + interview scorecard.         } run in
  3. Sourcing Strategist (LLM)  - target companies + LinkedIn search strings.   } parallel
  4. Checker (code)             - removes SciPro clients from the target list (never recruit
                                  from clients), checks the boolean strings are valid, and flags
                                  any salary number that isn't in the recruiter's notes.
Salary ranges are never invented: the comp check only restates what the client said.
"""

import json
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date

from outreach_writer import client_list, existing_client, numbers_in
from signal_radar import call_llm, normalize

FUNCTIONS = ["Discovery, Preclinical & Nonclinical, Early Development", "Technical Operations",
             "Clinical", "Commercial", "Professional Services"]

SAMPLE_NOTES = """Intake call w/ VP Clinical Development, Brightwater Bio (fictional sample)
- Series B ($120M), lead asset is a bispecific mAb for atopic dermatitis + asthma, Ph2 readout Q3
- need Sr Director / Exec Director, Clinical Development. MD required. I&I, derm or resp ideal
- will be medical lead on the Ph2b, then Ph3 planning. reports to VP Clin Dev
- team of 2 today, building to ~6 over the next year
- must have: MD, 5+ yrs industry clin dev, Ph2/3 trial design, protocol writing, medical monitoring
- nice to have: biologics/mAbs, BLA experience, derm board cert, KOL network in derm
- Boston/Cambridge hybrid 3 days/wk, open to remote for the right person
- budget 330-375 base, 30% bonus, equity TBD
- process: HM screen -> panel (CMO, clin ops, biostats) -> presentation -> CEO
- wants a shortlist in 3 weeks. lost a finalist to big pharma last month on comp
- selling points: lead asset, strong runway, small team = big scope"""


# ---------- 1. Intake Analyst ----------

def analyze_notes(client, notes, company, role, run_id, usage):
    system = """You are a senior delivery recruiter at SciPro, a life-sciences search firm. You turn
messy hiring-manager intake notes into a structured brief. Use ONLY what the notes say: use null
or [] when something isn't mentioned, and never invent compensation."""
    user = f"""Structure these intake notes.
Company (if given separately): {company or 'see notes'}
Role (if given separately): {role or 'see notes'}

Return JSON: {{"job_title", "seniority", "function": one of {json.dumps(FUNCTIONS)},
"company", "company_context": what the notes say about the company, "location", "work_model",
"reports_to", "team_context", "responsibilities": [..], "must_haves": [..], "nice_to_haves": [..],
"compensation": {{"base": text or null, "bonus": text or null, "equity": text or null,
                  "notes": anything else said about comp or null}},
"timeline", "interview_process": [stages], "selling_points": [..],
"risks": [things that could make this search hard, from the notes],
"open_questions": [important things the notes don't answer that the recruiter should ask]}}

INTAKE NOTES:
{notes}"""
    return call_llm(client, "intake_analyst", system, user, run_id, usage=usage)


# ---------- 2. Spec Writer ----------

def write_spec(client, brief, run_id, usage):
    system = """You write clear, compelling job specs and structured interview scorecards for
life-sciences roles. Use only facts in the brief. Never invent compensation, company facts, or
benefits; if compensation isn't given, write "Competitive; details to be confirmed"."""
    user = f"""From this intake brief, write the job spec and scorecard.

Return JSON: {{
"job_spec": {{"title", "about_company": 1-2 sentences from the brief only,
              "summary": 2-3 sentence role overview, "responsibilities": 5-7 bullets,
              "requirements": 4-6 bullets, "preferred": 2-4 bullets,
              "location_line", "comp_line": from the brief only}},
"scorecard": [{{"competency", "weight": 1-3 (3 = critical),
                "what_good_looks_like": one sentence, "questions": 2 interview questions}}]
             (5-6 competencies tied to the must-haves),
"interview_plan": [short stage names, from the brief when given]
}}

BRIEF:
{json.dumps(brief, indent=1)}"""
    return call_llm(client, "spec_writer", system, user, run_id, usage=usage)


# ---------- 3. Sourcing Strategist ----------

def plan_sourcing(client, brief, run_id, usage):
    system = """You are a sourcing strategist at SciPro, a life-sciences search firm. You know where
biotech and pharma talent works. Only name real companies you are confident exist. Never suggest
recruiting from SciPro's own clients (they are off-limits) or from the hiring company."""
    user = f"""Plan the search for this role.

SciPro clients (OFF-LIMITS, never list them): {", ".join(client_list())}

Return JSON: {{
"target_companies": [{{"name", "why": short reason (similar modality, therapeutic area, or stage)}}]
                    (8-12 companies, a mix of similar biotechs and larger companies with
                     relevant teams),
"adjacent_titles": [4-8 titles people with this profile hold],
"linkedin_boolean": a LinkedIn keyword boolean string (OR'd title variants in parentheses AND
                    must-have skills; use quotes for phrases),
"linkedin_titles": a short boolean for LinkedIn's title filter,
"job_board_string": a simpler boolean for job boards,
"sourcing_tips": [3 practical tips for this search]
}}

BRIEF:
{json.dumps(brief, indent=1)}"""
    return call_llm(client, "sourcing_strategist", system, user, run_id, usage=usage)


# ---------- 4. Checker (code) ----------

def boolean_problems(text):
    problems = []
    if text.count("(") != text.count(")"):
        problems.append("unbalanced parentheses")
    if (text.count('"') - text.count('\\"')) % 2:
        problems.append("an unclosed quote")
    if re.search(r"\b(AND|OR)\s*(\)|$)", text) or re.search(r"\(\s*(AND|OR)\b", text):
        problems.append("a dangling AND/OR")
    return problems


def same_number_in(n, notes):
    """True if n appears in the notes, also as thousands ("330,000" matches "330" or "330k")."""
    found = numbers_in(notes)
    return n in found or (n.endswith("000") and n[:-3] in found)


def check_intake(result, notes, company):
    warnings = []
    sourcing = result["sourcing"]
    hiring = normalize(company or result["brief"].get("company") or "")
    kept, removed = [], []
    for t in sourcing.get("target_companies", []):
        name = t.get("name", "")
        if existing_client(name):
            removed.append(f"{name} (SciPro client)")
        elif hiring and normalize(name) == hiring:
            removed.append(f"{name} (the hiring company)")
        else:
            kept.append(t)
    sourcing["target_companies"] = kept
    if removed:
        warnings.append("Removed off-limits target companies: " + ", ".join(removed))
    for key, label in [("linkedin_boolean", "LinkedIn boolean"),
                       ("linkedin_titles", "LinkedIn title filter"),
                       ("job_board_string", "Job board string")]:
        problems = boolean_problems(sourcing.get(key) or "")
        if problems:
            warnings.append(f"{label} has {' and '.join(problems)}. Fix before pasting.")
    comp_text = str(result["spec"].get("job_spec", {}).get("comp_line") or "")
    invented = sorted(n for n in numbers_in(comp_text) if not same_number_in(n, notes))
    if invented:
        warnings.append("The compensation line has numbers that aren't in your notes: "
                        + ", ".join(invented) + ". Remove or confirm them.")
    comp = result["brief"].get("compensation") or {}
    if not any(comp.get(k) for k in ("base", "bonus", "equity")):
        q = "What's the compensation budget (base, bonus, equity)?"
        if q not in result["brief"].setdefault("open_questions", []):
            result["brief"]["open_questions"].insert(0, q)
    return warnings


# ---------- run ----------

def build_intake(client, notes, company, role, run_id):
    usage = []
    brief = analyze_notes(client, notes, company, role, run_id, usage)
    with ThreadPoolExecutor(max_workers=2) as pool:
        spec_job = pool.submit(write_spec, client, brief, run_id, usage)
        sourcing_job = pool.submit(plan_sourcing, client, brief, run_id, usage)
        spec, sourcing = spec_job.result(), sourcing_job.result()
    result = {"brief": brief, "spec": spec, "sourcing": sourcing,
              "company": company or brief.get("company") or "",
              "date": date.today().isoformat()}
    result["warnings"] = check_intake(result, notes, company)
    result["tokens"] = {"input": sum(u["input_tokens"] for u in usage),
                        "cached": sum(u["cached_input_tokens"] for u in usage),
                        "output": sum(u["output_tokens"] for u in usage)}
    return result


def spec_text(result):
    """The job spec as plain text, ready to paste into an email or ATS."""
    s = result["spec"].get("job_spec", {})
    lines = [s.get("title", ""), ""]
    for label, key in [("About the company", "about_company"), ("The role", "summary")]:
        if s.get(key):
            lines += [label, s[key], ""]
    for label, key in [("Responsibilities", "responsibilities"), ("Requirements", "requirements"),
                       ("Preferred", "preferred")]:
        if s.get(key):
            lines += [label] + [f"- {x}" for x in s[key]] + [""]
    for label, key in [("Location", "location_line"), ("Compensation", "comp_line")]:
        if s.get(key):
            lines.append(f"{label}: {s[key]}")
    return "\n".join(lines).strip()
