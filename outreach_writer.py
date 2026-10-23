"""Outreach Writer: a chat assistant that drafts and revises a BD email for one company.

Email structure (SciPro consultants' own playbook, see data/example_emails.md):
  1. Congratulate them on the specific milestone (asset, raise, next phase).
  2. Who I am + SciPro: a life-sciences search firm that mirrors the drug development lifecycle.
  3. Network: similar SciPro clients (same modality / therapeutic area / stage).
  4. The SciPro case study that best matches the company.
  5. Call to action: 20-30 minutes over the next couple of weeks.
Current SciPro clients get an account-expansion note instead of a cold introduction.

The recruiter can then give feedback in the chat and the email is rewritten. After every
draft a Checker (code) flags any case study that isn't real and any number in the email
that doesn't appear in the sources, so the recruiter knows what to double-check.
"""

import json
import re
from datetime import datetime

from signal_radar import DATA_DIR, UNTRUSTED_NOTE, call_llm, normalize

# Knowledge files: private/ holds SciPro's internal versions (deck, case-study PDFs, real emails)
# and is never committed; data/ holds public-website-only versions that ship with the repo.
PRIVATE_DIR = DATA_DIR.parent / "private"


def knowledge_file(name):
    private = PRIVATE_DIR / name
    return private if private.exists() else DATA_DIR / name


def using_private_knowledge():
    return (PRIVATE_DIR / "scipro_capabilities.md").exists()
# Case-study PDFs live in the app folder (gitignored). Spinouts are covered by Sera's PDF.
CASE_STUDY_ALIASES = {"Navigator Medicines": "Sera Medicines", "Climb Bio": "Sera Medicines"}


def capabilities():
    return knowledge_file("scipro_capabilities.md").read_text()


def case_study_names():
    """Client names from the case-study table in the capabilities file."""
    names = []
    for line in capabilities().splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if line.startswith("| ") and cells[0] not in ("Client", "") and not set(cells[0]) <= set("-"):
            names.append(cells[0])
    return names


def client_list():
    """SciPro's therapeutic-area client names, from the capabilities file."""
    section = capabilities().split("## Therapeutic-area clients", 1)[1].split("##", 1)[0]
    body = " ".join(line for line in section.splitlines()[1:] if line.strip())
    return [c.strip().rstrip(".") for c in body.split(",") if c.strip()]


def case_study_pdf(name):
    """File name of the case-study PDF the recruiter can attach, if there is one."""
    key = CASE_STUDY_ALIASES.get(name, name or "").lower()
    for f in sorted(PRIVATE_DIR.glob("*.pdf")) if PRIVATE_DIR.exists() else []:
        if key and key in f.name.lower() and "case study" in f.name.lower():
            return f.name
    return None


def existing_client(company):
    """True when the company is already a SciPro client (client list or case study)."""
    target = normalize(company or "")
    return bool(target) and any(normalize(c) == target for c in client_list() + case_study_names())


def system_prompt():
    examples_file = knowledge_file("example_emails.md")
    examples = examples_file.read_text() if examples_file.exists() else ""
    # Kept identical for every company so the provider can cache it.
    return f"""You are a consultant at SciPro Global writing a first-touch business-development
email to a leader at a biotech company.

Follow this structure, as 5 short paragraphs:
1. Congrats on their specific milestone, naming the asset, raise, or next phase when the sources
   give it (e.g. "Congrats on the launch - X is a compelling asset, and the raise puts you in an
   exciting position heading into Phase 2b/3"). Use only facts given about the company. If no
   milestone is given, open with genuine interest in what they're building. Never invent news.
2. Who you are: a consultant at SciPro Global. Describe SciPro in one sentence using only the
   Positioning and "SciPro at a glance" sections below (e.g. how its teams are organized, where
   its US offices are). Name the practice area that fits their likely hiring needs (e.g.
   clinical, CMC / technical operations, commercial).
3. Your network: name 3-6 SciPro clients from the client list that are most similar to them
   (modality, therapeutic area, stage) and say briefly why they're similar.
4. ONE SciPro case study that best matches the company. Name the client and use only the
   facts stated for it. If it is in ATTACHABLE CASE STUDIES, say you've attached it ("I've
   attached a case study on our work with..."); otherwise offer to share it ("happy to send over
   a case study on...").
5. Call to action: you'd love to meet and get introduced. Ask for 20-30 minutes over the next
   couple of weeks.
If "existing_client" is true, they already work with SciPro: skip the cold introduction, thank
them for the partnership, and offer support with the hiring their new milestone will bring.

Style: match the example emails' voice: warm, specific, enthusiastic, conversational, with at
most one friendly emoji. 140-200 words in the body. No buzzwords, no em dashes.
Copy the examples' style only: never reuse the example sender's title, tenure, or practice, and
never reuse facts about the example companies.
Start with "Hi [First Name]," and end with "Best,\\n[Your Name]".
Never invent facts, numbers, or clients.
{UNTRUSTED_NOTE}

You are chatting with the recruiter. Always reply with JSON:
{{"message": a short, friendly note to the recruiter (1-2 sentences, e.g. what you changed),
  "email": {{"subject": under 9 words, "body": the full email with \\n line breaks,
            "case_study": exact client name of the case study cited,
            "why_this_case_study": one short sentence,
            "clients_mentioned": [every SciPro client named in the email, exactly as listed]}}
           or null if the recruiter only asked a question}}
When the recruiter gives feedback, rewrite the whole email applying it. Keep the structure
unless they ask otherwise.

ATTACHABLE CASE STUDIES (the recruiter has these as PDFs): {", ".join(n for n in case_study_names() if case_study_pdf(n)) or "none"}

SCIPRO CAPABILITIES AND CASE STUDIES:
{capabilities()}
{"EXAMPLE EMAILS FROM THIS RECRUITER (match their voice):" + chr(10) + examples if examples else ""}"""


def company_facts(company):
    facts = {k: company.get(k) for k in
             ("company", "company_type", "why_now", "likely_roles", "outreach_angle")
             if company.get(k)}
    facts["news_headlines"] = [e["title"] for e in company.get("evidence", [])][:3]
    facts["existing_client"] = existing_client(company.get("company"))
    return facts


def first_turn(company):
    """Opening chat message: the company facts plus a request for the first draft."""
    return {"role": "user", "content": "Write the first draft of the outreach email for this "
            f"company.\n\n<sources>\n{json.dumps(company_facts(company), indent=2)}\n</sources>"}


def chat_turn(client, company, history, run_id, usage=None):
    """Next assistant reply for the conversation in `history` (list of role/content dicts)."""
    reply = call_llm(client, "writer", system_prompt(), None, run_id, history=history, usage=usage)
    email = reply.get("email")
    if email:
        email["attachment"] = case_study_pdf(email.get("case_study"))
        email["warnings"] = check_email(email, company_facts(company))
    return reply


NUMBER = re.compile(r"\d[\d,.]*")


def numbers_in(text):
    return {n.rstrip(".,").replace(",", "") for n in NUMBER.findall(text or "")}


def check_email(email, facts):
    """Checker: flag made-up case studies and numbers that aren't in the sources."""
    warnings = []
    body = email.get("body", "")
    names = case_study_names()
    cited = email.get("case_study", "")
    if cited not in names:
        warnings.append(f"Case study “{cited}” is not in SciPro's case study list.")
    elif cited.split()[0].lower() not in body.lower():
        warnings.append(f"The email doesn't mention the cited case study ({cited}).")
    real = set(client_list()) | set(names)
    fake = [c for c in email.get("clients_mentioned", []) if c not in real]
    if fake:
        warnings.append("These aren't on SciPro's client list, remove them: " + ", ".join(fake))
    allowed = (numbers_in(capabilities()) | numbers_in(json.dumps(facts))
               | {"20", "30"})  # the playbook's own "20-30 minutes" ask
    unknown = sorted(numbers_in(body) - allowed)
    if unknown:
        warnings.append("Double-check these numbers, they aren't in the sources: "
                        + ", ".join(unknown))
    if re.search(r"\battach", body, re.I) and not case_study_pdf(cited):
        warnings.append("The email mentions an attachment, but there's no PDF for this case study.")
    if not facts.get("why_now"):
        warnings.append("No recent milestone on file, so the opening is general. Add a "
                        "personal hook before sending.")
    if re.search(r"\[(?!First Name\]|Your Name\])[^\]]+\]", body):
        warnings.append("The email has an unfilled [placeholder].")
    return warnings
