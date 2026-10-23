"""Pitch Builder: turns research on one company into a tailored SciPro pitch deck.

Pipeline (one run):
  1. Fetcher (code)       - downloads the company website and any news links the recruiter pasted.
  2. Researcher (LLM)     - builds a fact-only company profile from the website, news, and notes.
  3. Pitch Writer (LLM)   - writes the tailored slides, following SciPro's own pitch deck structure.
  4. Checker (code)       - case study and clients must be real SciPro ones, no numbers that aren't
                            in the sources, and no pricing (fees are never given to the model).
  5. Renderer (code)      - builds one self-contained HTML slide deck (shown in the app + download).
Fixed slides (SciPro at a glance, hiring models, candidate profiles, LHi Group) come from code.
"""

import base64
import html
import json
import re
from datetime import date
from pathlib import Path

import requests

from outreach_writer import capabilities, case_study_names, client_list, existing_client, numbers_in
from signal_radar import UNTRUSTED_NOTE, USER_AGENT, call_llm, sanitize_text, strip_tags

CAPABILITY_AREAS = ["Discovery, Preclinical & Nonclinical, Early Development",
                    "Technical Operations", "Clinical", "Commercial", "Professional Services"]


def section(title):
    """Lines of one '## title' section of the capabilities file (private or public version)."""
    text = capabilities()
    if f"## {title}" not in text:
        return []
    body = text.split(f"## {title}", 1)[1].split("\n## ", 1)[0]
    return [line.rstrip() for line in body.splitlines()[1:]]


def offerings():
    """{model name: description} from the capabilities file's 'Our offering' section."""
    items, current = {}, None
    for line in section("Our offering (hiring models)"):
        if line.startswith("- ") and ":" in line:
            current, desc = line[2:].split(":", 1)
            current = current.strip()
            items[current] = desc.strip()
        elif current and line.strip():
            items[current] += " " + line.strip()
    return {name: desc[:1].upper() + desc[1:] for name, desc in items.items()}


def at_a_glance():
    """([(number, label)], note) from the capabilities file's 'At a glance' section."""
    stats, note = [], ""
    for line in section("At a glance"):
        if line.startswith("- ") and "|" in line:
            num, label = line[2:].split("|", 1)
            stats.append((num.strip(), label.strip()))
        elif line.startswith(">"):
            note = line[1:].strip()
    return stats, note


OFFERINGS = offerings()  # checklist options in the app


# ---------- 1. Fetcher (code) ----------

def fetch_text(url, limit=6000):
    try:
        resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=12)
        resp.raise_for_status()
    except requests.RequestException as e:
        return None, str(e)
    page = re.sub(r"(?is)<(script|style|noscript|svg).*?</\1>", " ", resp.text)
    return html.unescape(strip_tags(page))[:limit], None


def gather_sources(website, news):
    """Returns (sources dict, list of fetch notes for the recruiter)."""
    notes, sources = [], {}
    if website:
        text, err = fetch_text(website)
        if text:
            sources["website"] = text
        else:
            notes.append(f"Couldn't read the website ({err}).")
    articles = []
    for line in (news or "").splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("http") and len(articles) < 3:
            text, err = fetch_text(line, limit=4000)
            if text:
                articles.append(text)
            else:
                notes.append(f"Couldn't read {line[:60]}... ({err}).")
        elif not line.startswith("http"):
            articles.append(line[:4000])
    if articles:
        sources["news"] = articles
    # Sanitizer: strip prompt-injection sentences before any model reads the web content
    removed = 0
    if "website" in sources:
        sources["website"], n = sanitize_text(sources["website"])
        removed += n
    if "news" in sources:
        cleaned = [sanitize_text(a) for a in sources["news"]]
        sources["news"] = [c for c, _ in cleaned]
        removed += sum(n for _, n in cleaned)
    if removed:
        notes.append(f"Sanitizer removed {removed} suspicious sentence(s) that looked like "
                     "instructions to the AI (possible prompt injection).")
    return sources, notes


# ---------- 2. Researcher (LLM) ----------

def research_company(client, company, sources, notes_text, run_id, usage):
    system = f"""You are a research analyst at SciPro, a life-sciences recruitment firm. You build
fact-only profiles of biotech companies for a business-development pitch.
{UNTRUSTED_NOTE}"""
    user = f"""Build a profile of {company} using ONLY the sources below. Use null or [] when
the sources don't say. The recruiter's notes are trusted.

Return JSON: {{"summary": 2 sentences on what the company does,
"modality": e.g. "bispecific antibodies", "therapeutic_areas": [..], "stage": e.g. "Phase 2",
"headquarters": city/state or null, "milestones": [recent funding, data, deals, launches],
"likely_hiring_needs": [functions or roles the company will likely need next]}}

<sources>
{json.dumps(sources, indent=1)[:16000]}
</sources>

RECRUITER NOTES:
{notes_text or 'none'}"""
    return call_llm(client, "researcher", system, user, run_id, usage=usage)


# ---------- 3. Pitch Writer (LLM) ----------

def write_pitch(client, company, profile, notes_text, models, areas, run_id, usage):
    system = f"""You are a senior business-development lead at SciPro writing a tailored pitch
deck for a prospective biotech client. Be specific to the company, warm, and confident.
Never invent facts, numbers, clients, or candidates. Never mention fees, percentages of salary,
or any pricing: commercial terms are shared separately by the consultant.
{UNTRUSTED_NOTE}

SCIPRO CAPABILITIES AND CASE STUDIES:
{capabilities()}"""
    user = f"""Write the tailored slides for a pitch to {company}.
Hiring models the recruiter wants to pitch: {', '.join(models) or 'recommend the best fit'}.
Capability areas to emphasize: {', '.join(areas) or 'choose the most relevant'}.

Return JSON: {{
"headline": cover-slide headline under 10 words, about building {company}'s team,
"snapshot_bullets": 3-4 bullets on what SciPro sees happening at {company} (from the profile),
"hiring_areas": [{{"area": one of {json.dumps(CAPABILITY_AREAS)},
                   "roles": 2-4 role titles they will likely need, "why": one short sentence}}]
                (2-4 areas),
"clients_title": slide title for the client logos, e.g. "Trusted by teams like yours",
"relevant_clients": 3-8 names copied exactly from SciPro's therapeutic-area client list,
                    most similar to {company} in modality, therapeutic area, or stage,
"recommended_model": one of {json.dumps(list(offerings()))},
"model_reason": 2 sentences on why that model fits {company} right now,
"case_study": {{"client": exact case study client name, "headline": short headline,
                "points": 3 bullets using only that case study's stated facts,
                "relevance": one sentence linking it to {company}}},
"next_steps": 3 short bullets for the closing slide
}}

<sources>
COMPANY PROFILE:
{json.dumps(profile, indent=1)}
</sources>

RECRUITER NOTES (trusted):
{notes_text or 'none'}
{f"{company} is ALREADY a SciPro client: frame this as expanding the partnership." if existing_client(company) else ""}"""
    return call_llm(client, "pitch_writer", system, user, run_id, usage=usage)


# ---------- 4. Checker (code) ----------

# a fee number: "18% of base salary", "$12k upfront", "fee of $15,000"... (made-up examples)
PRICING = re.compile(r"\d+\s?%\s*of\s*(the\s*)?(candidate|base|first)|\$\s?\d[\d,.]*\s?k?\b[^.]{0,40}"
                     r"(fee|upfront|placement|retainer)|(fee|upfront|retainer)[^.]{0,30}\$\s?\d", re.I)


def check_pitch(pitch, profile, notes_text, sources):
    warnings = []
    cs = pitch.get("case_study") or {}
    if cs.get("client") not in case_study_names():
        warnings.append(f"Case study “{cs.get('client')}” isn't a SciPro case study, so it was removed.")
        pitch["case_study"] = None
    clients = client_list()
    fake = [c for c in pitch.get("relevant_clients", []) if c not in clients]
    if fake:
        warnings.append("Removed clients that aren't on SciPro's client list: " + ", ".join(fake))
    pitch["relevant_clients"] = [c for c in pitch.get("relevant_clients", []) if c in clients]
    if pitch.get("recommended_model") not in offerings():
        pitch["recommended_model"] = next(iter(offerings()), "")
    text = json.dumps({k: v for k, v in pitch.items() if k != "warnings"})
    allowed = (numbers_in(capabilities()) | numbers_in(json.dumps(profile))
               | numbers_in(notes_text) | numbers_in(json.dumps(sources)))
    unknown = sorted(numbers_in(text) - allowed)
    if unknown:
        warnings.append("Double-check these numbers, they aren't in the sources: " + ", ".join(unknown))
    if PRICING.search(text):
        warnings.append("The pitch mentions pricing or fees. Remove it: terms are shared separately.")
    return warnings


# ---------- run ----------

def build_pitch(client, company, website, notes_text, news, models, areas, run_id):
    usage = []
    sources, fetch_notes = gather_sources(website, news)
    profile = research_company(client, company, sources, notes_text, run_id, usage)
    pitch = write_pitch(client, company, profile, notes_text, models, areas, run_id, usage)
    pitch["warnings"] = fetch_notes + check_pitch(pitch, profile, notes_text, sources)
    pitch.update({"company": company, "website": website, "profile": profile,
                  "date": date.today().isoformat(),
                  "tokens": {"input": sum(u["input_tokens"] for u in usage),
                             "cached": sum(u["cached_input_tokens"] for u in usage),
                             "output": sum(u["output_tokens"] for u in usage)}})
    return pitch


# ---------- 5. Renderer (code) ----------

# the SciPro molecule bubbles, embedded so a downloaded deck works offline
BUBBLES = ('<img class="bubbles" alt="" src="data:image/svg+xml;base64,'
           + base64.b64encode((Path(__file__).parent / "assets" / "bubbles.svg").read_bytes()).decode()
           + '">')


def esc(text):
    return html.escape(str(text or ""))


def bullets(items):
    return "<ul>" + "".join(f"<li>{esc(i)}</li>" for i in items if i) + "</ul>"


def render_html(p):
    """One self-contained HTML file: arrow keys / buttons to move, F for full screen."""
    company = esc(p["company"])
    prof = p.get("profile") or {}
    slides = []

    slides.append(f"""<section class="slide cover">{BUBBLES}
      <div class="kicker">SciPro · Partnership proposal</div>
      <h1>{esc(p.get('headline'))}</h1>
      <p class="sub">Prepared for {company} · {esc(p.get('date'))}</p>
      <p class="tag">For people with <em>purpose.</em> Experts in life-science recruitment.</p></section>""")

    facts = [f for f in [prof.get("modality"), ", ".join(prof.get("therapeutic_areas") or []),
                         prof.get("stage"), prof.get("headquarters")] if f]
    slides.append(f"""<section class="slide"><h2>What we're seeing at {company}</h2>
      {'<div class="chips">' + ''.join(f'<span>{esc(f)}</span>' for f in facts) + '</div>' if facts else ''}
      {bullets(p.get('snapshot_bullets', []))}</section>""")

    glance, glance_note = at_a_glance()
    stats = "".join(f'<div class="stat"><b>{esc(n)}</b><span>{esc(t)}</span></div>' for n, t in glance)
    slides.append(f"""<section class="slide"><h2>SciPro at a glance</h2>
      <div class="stats">{stats}</div>
      <p class="note">{esc(glance_note)}</p></section>""")

    areas = "".join(f"""<div class="area"><h3>{esc(a.get('area'))}</h3>
      <p>{esc(a.get('why'))}</p>{bullets(a.get('roles', []))}</div>"""
                    for a in p.get("hiring_areas", []))
    slides.append(f"""<section class="slide"><h2>Where we can help {company} hire</h2>
      <div class="areas">{areas}</div></section>""")

    if p.get("relevant_clients"):
        logos = "".join(f"<span>{esc(c)}</span>" for c in p["relevant_clients"])
        slides.append(f"""<section class="slide"><h2>{esc(p.get('clients_title') or 'Selected SciPro clients')}</h2>
          <div class="logos">{logos}</div></section>""")

    rec = p.get("recommended_model")
    models = "".join(f"""<div class="model{' rec' if name == rec else ''}"><h3>{esc(name)}
      {'<em>Recommended</em>' if name == rec else ''}</h3><p>{esc(desc)}</p></div>"""
                     for name, desc in offerings().items())
    slides.append(f"""<section class="slide"><h2>How we partner</h2>
      <div class="models">{models}</div></section>""")

    cs = p.get("case_study")
    if cs:
        slides.append(f"""<section class="slide"><h2>Case study: {esc(cs.get('client'))}</h2>
          <p class="lead">{esc(cs.get('headline'))}</p>{bullets(cs.get('points', []))}
          <p class="note">Why it's relevant: {esc(cs.get('relevance'))}</p></section>""")

    slots = "".join("""<div class="slot"><b>Job title</b><span>Location</span>
      <p>Candidate summary to be added by your SciPro consultant.</p></div>""" for _ in range(3))
    slides.append(f"""<section class="slide"><h2>Potential candidate profiles</h2>
      <div class="slots">{slots}</div>
      <p class="note">Profiles are shared by your consultant once search priorities are agreed.</p></section>""")

    slides.append(f"""<section class="slide"><h2>Our recommendation</h2>
      <div class="model rec big"><h3>{esc(rec)}<em>Recommended</em></h3><p>{esc(p.get('model_reason'))}</p></div>
      <p class="note">Commercial terms are shared directly by your SciPro consultant.</p>
      <h3 class="next">Next steps</h3>{bullets(p.get('next_steps', []))}</section>""")

    slides.append(f"""<section class="slide cover end">{BUBBLES}<div class="kicker">SciPro is an LHi Group business</div>
      <h1>Let's build your team.</h1>
      <p class="sub">Part of the LHi Group family of specialist talent brands.</p>
      <p class="tag">sciproglobal.com</p></section>""")

    return PITCH_TEMPLATE.replace("{{TITLE}}", f"SciPro pitch for {company}") \
                         .replace("{{SLIDES}}", "\n".join(slides))


PITCH_TEMPLATE = """<!doctype html><html><head><meta charset="utf-8"><title>{{TITLE}}</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Open+Sans:ital,wght@0,300;0,400;0,600;1,300&display=swap">
<style>
*{box-sizing:border-box}html,body{margin:0;height:100%;font-family:"Open Sans",-apple-system,Helvetica,sans-serif;
color:#3a4752;background:#eef6f7}
.deck{position:relative;width:100vw;height:100vh;display:grid;place-items:center}
.slide{display:none;width:min(96vw,calc(96vh*16/9));aspect-ratio:16/9;background:#fff;border-radius:18px;
padding:4.5% 6%;box-shadow:0 10px 40px rgba(11,74,80,.12);position:relative;overflow:hidden;font-size:min(1.6vw,2.9vh)}
.slide.on{display:flex;flex-direction:column}
.slide>*{position:relative;z-index:1}
.slide::after{content:"SciPro";position:absolute;right:4%;bottom:4.5%;font-weight:300;color:#7b8892;font-size:1.5em;letter-spacing:-.02em}
.slide::before{content:"";position:absolute;left:-9%;bottom:-22%;width:30%;aspect-ratio:1;border-radius:50%;
background:linear-gradient(135deg,#2b8a8f,#5fd3d9 55%,#7fe6eb);opacity:.18;z-index:0}
h1{font-size:3.2em;margin:.2em 0;letter-spacing:-.02em;line-height:1.08;font-weight:300;color:#4f5b69}
h1 em{font-style:italic;color:#5fc4c8}
h2{font-size:2em;margin:0 0 .7em;letter-spacing:-.01em;color:#0b4a50;font-weight:600}
h3{font-size:1.05em;margin:0 0 .3em;color:#0b4a50}p{line-height:1.5;margin:.3em 0}ul{margin:.3em 0;padding-left:1.2em;line-height:1.6}
.cover{justify-content:flex-end;padding-bottom:7%}
.cover::before{left:-6%;top:18%;bottom:auto;width:16%;background:radial-gradient(circle at 70% 50%,#7fe0ad,#e9fff4 75%);opacity:.9}
.cover .bubbles{position:absolute;top:-6%;right:-4%;width:58%;height:auto;z-index:0;opacity:.95}
.kicker{text-transform:uppercase;letter-spacing:.18em;font-size:.75em;color:#2a9fa7;font-weight:600}
.sub{font-size:1.15em;color:#7b8892}.tag{margin-top:1.2em;color:#0b4a50;font-weight:600}
.chips{display:flex;gap:.5em;flex-wrap:wrap;margin-bottom:.8em}.chips span{background:#eefbfb;border:1px solid #c9f3f5;color:#0b4a50;border-radius:99px;padding:.25em .8em;font-size:.85em}
.stats{display:grid;grid-template-columns:repeat(4,1fr);gap:.8em}.stat{border-radius:12px;padding:1em;background:#0b4a50;color:#79e3ea}
.stat:nth-child(even){background:#c9f3f5;color:#0b4a50}
.stat b{display:block;font-size:2.1em;font-weight:600}.stat span{font-size:.85em}
.note{color:#7b8892;font-size:.9em;margin-top:auto}.lead{font-size:1.2em;font-weight:600;color:#0b4a50}
.areas{display:grid;grid-template-columns:repeat(2,1fr);gap:1em}.area{background:#eefbfb;border-radius:12px;padding:.9em 1.1em;font-size:.92em}
.area:nth-child(2){background:#effcf5}.area:nth-child(3){background:#fdf1f8}.area:nth-child(4){background:#f2f5f7}
.logos{display:flex;flex-wrap:wrap;gap:.8em}.logos span{border:1px solid #e2ecee;border-radius:12px;padding:.9em 1.2em;font-weight:600;color:#0b4a50;background:#fff;box-shadow:0 2px 8px rgba(11,74,80,.06)}
.models{display:grid;grid-template-columns:repeat(4,1fr);gap:.7em}.model{border-radius:12px;padding:.7em .9em;font-size:.8em;background:#ececec;color:#4f5b69}
.model h3{color:#4f5b69}.model:nth-child(1),.model:nth-child(6){background:#4f5b69;color:#e6ebef}.model:nth-child(1) h3,.model:nth-child(6) h3{color:#fff}
.model:nth-child(2),.model:nth-child(5){background:#8ea4b5;color:#fff}.model:nth-child(2) h3,.model:nth-child(5) h3{color:#fff}
.model.rec{background:#0b4a50;color:#c9f3f5;outline:3px solid #5fcfd5}.model.rec h3{color:#79e3ea}
.model em{font-style:normal;background:#5fcfd5;color:#0b4a50;border-radius:99px;padding:.1em .6em;margin-left:.5em;font-size:.75em}
.model.big{font-size:1em;padding:1em 1.3em}.next{margin-top:1em}
.slots{display:grid;grid-template-columns:repeat(3,1fr);gap:1em}.slot{border:2px dashed #b9e6e9;border-radius:12px;padding:1em;color:#7b8892}
.slot b,.slot span{display:block}.slot b{color:#0b4a50}
.nav{position:fixed;bottom:14px;left:50%;transform:translateX(-50%);display:flex;gap:10px;align-items:center;font-size:13px;color:#7b8892}
.nav button{border:none;background:#0b4a50;color:#fff;border-radius:99px;padding:6px 14px;cursor:pointer;font:inherit;font-weight:600}
</style></head><body><div class="deck">
{{SLIDES}}
</div><div class="nav"><button onclick="go(-1)">←</button><span id="n"></span><button onclick="go(1)">→</button>
<span>· arrow keys · F = full screen</span></div>
<script>
var s=document.querySelectorAll('.slide'),i=0;function show(){s.forEach(function(x,k){x.classList.toggle('on',k===i)});
document.getElementById('n').textContent=(i+1)+' / '+s.length}function go(d){i=Math.max(0,Math.min(s.length-1,i+d));show()}
document.addEventListener('keydown',function(e){if(e.key==='ArrowRight'||e.key===' ')go(1);if(e.key==='ArrowLeft')go(-1);
if(e.key==='f'||e.key==='F'){document.fullscreenElement?document.exitFullscreen():document.documentElement.requestFullscreen()}});show();
</script></body></html>"""
