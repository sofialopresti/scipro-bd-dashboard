"""SciPro BD Dashboard - Dash app.

Run:  .venv/bin/python app.py   then open http://127.0.0.1:8050
"""

import hmac
import json
import os
from datetime import date, datetime

from flask import Response, request
from dash import ALL, Dash, Input, Output, State, ctx, dcc, html, no_update

import hiring_check
import job_intake
import outreach_writer
import pitch_builder
import safety_tests
import signal_radar
import usage
from signal_radar import DATA_DIR, USAGE_LOG, search_url

LATEST_RUN = DATA_DIR / "latest_run.json"
LATEST_PITCH = DATA_DIR / "latest_pitch.json"
LATEST_INTAKE = DATA_DIR / "latest_intake.json"
PIPELINE_FILE = DATA_DIR / "pipeline.json"
STAGES = [("targets", "Targets", "aqua"), ("reached", "Reached Out", "mint"),
          ("pitch", "Pitch", "pink"), ("client", "Client Acquired", "teal")]
STAGE_KEYS = [k for k, _, _ in STAGES]
STAGE_LABELS = {k: label for k, label, _ in STAGES}
SOURCEWHALE_URL = "https://app.sourcewhale.com"

app = Dash(__name__, title="SciPro BD Dashboard", suppress_callback_exceptions=True)


# Optional password screen for sharing the app through a public link (e.g. a tunnel).
# Only active when APP_PASSWORD is set in .env; any username works.
@app.server.before_request
def require_password():
    password = os.environ.get("APP_PASSWORD")
    if not password:
        return None
    auth = request.authorization
    if auth and auth.password and hmac.compare_digest(auth.password, password):
        return None
    return Response("Password required.", 401,
                    {"WWW-Authenticate": 'Basic realm="SciPro BD Dashboard"'})


# ---------- helpers ----------

def load_latest():
    return json.loads(LATEST_RUN.read_text()) if LATEST_RUN.exists() else None


def load_pipeline():
    return json.loads(PIPELINE_FILE.read_text()) if PIPELINE_FILE.exists() else []


def save_pipeline(pipeline):
    PIPELINE_FILE.write_text(json.dumps(pipeline, indent=2))


def new_entry(pick):
    today = date.today().isoformat()
    return {"company": pick["company"], "company_type": pick.get("company_type", ""),
            "hiring_score": pick.get("hiring_score"), "why_now": pick.get("why_now", ""),
            "website": pick.get("website"), "likely_roles": pick.get("likely_roles", []),
            "outreach_angle": pick.get("outreach_angle", ""),
            "evidence": [{"title": e["title"], "link": e["link"]} for e in pick.get("evidence", [])],
            "stage": "targets", "stage_since": today, "added": today}


def company_link(company, website, class_name):
    """Company name linking to its checked website, or a web search when we don't have one."""
    return html.A([company, html.Span(" ↗", className="ext")], href=website or search_url(company),
                  target="_blank", className=class_name, draggable="false",
                  title=website or "Search the web for this company")


def lookup_website(company):
    try:
        client = signal_radar.get_client()
        run_id = "manual-" + datetime.now().strftime("%Y%m%d-%H%M%S")
        return signal_radar.find_websites(client, [company], run_id).get(company)
    except Exception:
        return None


def short_date(iso):
    try:
        return datetime.fromisoformat(iso).strftime("%b %-d")
    except (TypeError, ValueError):
        return ""


def run_tokens(run_id):
    totals = {"input": 0, "cached": 0, "output": 0}
    if run_id and USAGE_LOG.exists():
        for line in USAGE_LOG.read_text().splitlines():
            row = json.loads(line)
            if row["run_id"] == run_id:
                totals["input"] += row["input_tokens"]
                totals["cached"] += row["cached_input_tokens"]
                totals["output"] += row["output_tokens"]
    return totals


def pretty_date(iso):
    try:
        return datetime.fromisoformat(iso).strftime("%A, %B %-d")
    except (TypeError, ValueError):
        return iso or ""


# ---------- layout pieces ----------

NAV = [
    ("home", "Home", "How it all fits"),
    ("pipeline", "Pipeline", "Your BD board"),
    ("radar", "Signal Radar", "Today's 5 companies"),
    ("outreach", "Outreach Assistant", "Chat to write emails"),
    ("pitch", "Pitch Builder", "Tailored pitch decks"),
    ("intake", "Job Intake", "Notes → search plan"),
    ("admin", "Usage & Safety", "Costs, limits, tests"),
]


def sidebar():
    return html.Div(className="sidebar", children=[
        html.Div(className="brand", children=[
            html.Div("SciPro", className="brand-name"),
            html.Div("BD Dashboard", className="brand-sub"),
        ]),
        html.Div(className="nav", children=[
            html.Button(
                [html.Div(label, className="nav-label"), html.Div(sub, className="nav-sub")],
                id={"type": "nav", "page": key},
                className="nav-item",
                n_clicks=0,
            ) for key, label, sub in NAV
        ]),
        html.Div("Biotech + AI drug discovery · USA", className="sidebar-foot"),
    ])


def score_bar(score):
    score = max(1, min(10, int(score or 0)))
    return html.Div(className="score", children=[
        html.Div(className="score-track", children=[
            html.Div(className="score-fill", style={"width": f"{score * 10}%"})]),
        html.Span(f"{score}/10", className="score-num"),
    ])


def client_badge(company):
    if not outreach_writer.existing_client(company):
        return None
    return html.Span("★ Current SciPro client", className="pill client-pill",
                     title="Already a SciPro client: treat this as account expansion, not cold outreach")


def hiring_badge(h):
    """Result of the Hiring Check (open roles right now) for a Signal Radar pick."""
    if not h:
        return None
    link = {"href": h["board_url"], "target": "_blank"} if h.get("board_url") else {}
    if h["status"] == "hiring":
        text = f"✓ Hiring now · {h['open_roles']} open roles"
        if h.get("matching_roles"):
            text += f" · {h['matching_roles']} match the predicted roles"
        return html.A(text + " ↗", className="pill hire-yes",
                      title=", ".join(h.get("matching_titles") or h.get("titles", [])), **link)
    if h["status"] == "job_board":
        return html.A(f"Posts jobs on {h['ats']} ↗", className="pill hire-board", **link)
    if h["status"] == "no_open_roles":
        return html.Span(f"No open roles on {h['ats']} right now", className="pill hire-no")
    return html.Span("Couldn't check open roles automatically", className="pill hire-no")


def company_card(i, p, pipeline):
    sources = [html.A(f"{e['source']}: {e['title']}", href=e["link"], target="_blank",
                      className="source-link") for e in p.get("evidence", [])[:3]]
    sources += [html.A(f"ClinicalTrials.gov {t['nct_id']} · {t['phase']}: {t['title']}",
                       href=f"https://clinicaltrials.gov/study/{t['nct_id']}", target="_blank",
                       className="source-link") for t in p.get("trials", [])[:2]]
    entry = next((e for e in pipeline if e["company"] == p["company"]), None)
    if entry is None:
        button_text, button_class = "+ Add to targets", "btn btn-add"
    elif entry["stage"] == "targets":
        button_text, button_class = "✓ In Targets", "btn btn-add added"
    else:
        button_text = f"✓ In pipeline · {STAGE_LABELS[entry['stage']]}"
        button_class = "btn btn-add added"
    return html.Div(className="card company-card", children=[
        html.Div(className="card-top", children=[
            html.Div(str(i), className="rank"),
            html.Div(className="card-title", children=[
                html.H3(company_link(p["company"], p.get("website"), "company-link")),
                html.Span(p.get("company_type", "biotech"), className="pill"),
                client_badge(p["company"]),
            ]),
            score_bar(p.get("hiring_score")),
            html.Button(button_text, id={"type": "pick", "company": p["company"]},
                        className=button_class, n_clicks=0,
                        title="Click to add this company to your Targets column"),
        ]),
        html.Div(hiring_badge(p.get("hiring")), className="hire-line") if p.get("hiring") else None,
        html.Div(className="why", children=[
            html.Div("Why now", className="label"), html.P(p.get("why_now", ""))]),
        html.Div(className="two-col", children=[
            html.Div([html.Div("Likely roles", className="label"),
                      html.Div([html.Span(r, className="chip") for r in p.get("likely_roles", [])],
                               className="chips")]),
            html.Div([html.Div("Outreach angle", className="label"),
                      html.P(p.get("outreach_angle", ""), className="angle")]),
        ]),
        html.Details(className="sources", children=[
            html.Summary(f"Sources ({len(sources)})"), html.Div(sources)]),
    ])


def radar_page():
    header = html.Div(className="page-head", children=[
        html.Div([
            html.H1("Signal Radar"),
            html.P("Biotech and AI drug discovery companies in the USA that are likely to hire, "
                   "based on today's news and new clinical trials.", className="subtitle"),
        ]),
        html.Div(className="actions", children=[
            dcc.Checklist(id="offline", options=[{"label": " Use saved sample data", "value": "on"}],
                          value=[], className="offline-toggle"),
            html.Button("Re-check hiring", id="recheck-btn", className="btn btn-soft recheck",
                        n_clicks=0, title="Free: no AI tokens. Re-checks open roles for these picks."),
            html.Button("Find today's companies", id="run-btn", className="btn btn-primary",
                        n_clicks=0),
        ]),
    ])

    return html.Div([
        header,
        html.Div(id="running", className="running", style={"display": "none"}, children=[
            html.Div(className="dots", children=[html.Span(), html.Span(), html.Span()]),
            html.Div([html.Strong("Agents at work: "),
                      "Scout → Analyst → Ranker → Verifier → Linker. This takes about 30-60 seconds."]),
        ]),
        html.Div(id="run-error"),
        html.Div(id="radar-body"),
    ])


def radar_body(run, pipeline):
    if not run:
        return html.Div(className="card empty", children=[
            html.H3("No companies yet"),
            html.P("Click “Find today's companies” to scan the news and ClinicalTrials.gov."),
        ])
    tokens = run_tokens(run.get("run_id"))
    counts = run.get("counts", {})
    stats = html.Div(className="stats", children=[
        stat(counts.get("articles", 0), "news articles scanned", "teal"),
        stat(counts.get("trial_sponsors", 0), "US trial sponsors", "aqua"),
        stat(len(run.get("news_signals", [])), "company signals found", "mint"),
        hiring_stat(run.get("picks", [])),
    ])
    cards = [company_card(i, p, pipeline) for i, p in enumerate(run["picks"], 1)]
    rejected = run.get("rejected", [])
    foot = html.Div(className="run-foot", children=[
        html.Span(f"Run {pretty_date(run.get('date'))} · "
                  f"{tokens['input']:,} input / {tokens['cached']:,} cached / "
                  f"{tokens['output']:,} output tokens"),
        html.Span(f" · Verifier removed: {', '.join(f'{n} ({w})' for n, w in rejected)}"
                  if rejected else " · Verifier: every pick matched a source"),
        html.Span(f" · Sanitizer quarantined {len(run['quarantined'])} article(s)"
                  if run.get("quarantined") else ""),
    ])
    return html.Div([
        html.Div(f"Results for {pretty_date(run.get('date'))}", className="date-line"),
        stats, html.Div(cards, className="cards"), foot, agent_trace(run)])


def agent_tokens(run_id):
    """Tokens per agent for one run, from the usage log."""
    per = {}
    if run_id and USAGE_LOG.exists():
        for line in USAGE_LOG.read_text().splitlines():
            row = json.loads(line)
            if row["run_id"] == run_id:
                t = per.setdefault(row["agent"], {"input": 0, "output": 0, "effort": row.get("reasoning_effort")})
                t["input"] += row["input_tokens"]
                t["output"] += row["output_tokens"]
    return per


def trace_step(num, name, kind, what, body, tokens=None):
    tok = (f"{tokens['input']:,} in / {tokens['output']:,} out tokens"
           + (f" · {tokens['effort']} effort" if tokens.get("effort") else "")) if tokens else "no AI tokens"
    return html.Div(className="trace-step", children=[
        html.Div(str(num), className="trace-num"),
        html.Div([
            html.Div([html.Strong(name), html.Span(kind, className=f"pill trace-{'ai' if 'AI' in kind else 'code'}"),
                      html.Span(tok, className="trace-tok")], className="trace-head"),
            html.P(what, className="muted small"),
            body,
        ], className="trace-body"),
    ])


def agent_trace(run):
    """Step-by-step view of what each agent did in this run (for the report's traced run)."""
    if not run.get("ranked"):
        return None
    tok = agent_tokens(run.get("run_id"))
    signals = run.get("news_signals", [])
    picks = run.get("picks", [])
    kept = {p["company"] for p in picks}
    counts = run.get("counts", {})
    steps = [
        trace_step(1, "Scout", "code", "Pulls the last 72h of biotech news RSS and new Phase 2/3 drug trials "
                   "from ClinicalTrials.gov; the Sanitizer quarantines prompt-injection text.",
                   html.P(f"{counts.get('articles', 0)} articles from {', '.join(run.get('sources_used', {}).get('feeds', []))} · "
                          f"{counts.get('trial_sponsors', 0)} US trial sponsors · "
                          f"{len(run.get('quarantined', []))} article(s) quarantined", className="small")),
        trace_step(2, "Analyst", "AI agent", "Reads every article and extracts in-scope company hiring signals "
                   "(drops big pharma, non-US, devices, diagnostics, CROs).",
                   html.Table([html.Tbody([html.Tr([html.Td(s["company"]), html.Td(s.get("signal_type")),
                                                    html.Td(s.get("summary"), className="small")])
                                           for s in signals[:8]])], className="data-table trace-table"),
                   tok.get("analyst")),
        trace_step(3, "Ranker", "AI agent", "Merges news + trial signals, scores hiring likelihood 1-10, and "
                   "picks the top companies with a 'why now'.",
                   html.Div([html.Span(f"{r['company']} · {r.get('hiring_score')}/10", className="chip")
                             for r in run["ranked"]], className="chips"),
                   tok.get("ranker")),
        trace_step(4, "Verifier", "code", "Keeps a pick only if it matches a real source; blocks big pharma and "
                   "companies shown in the last 14 days.",
                   html.P([f"{len(picks)} kept · ",
                           f"rejected: {', '.join(f'{n} ({w})' for n, w in run.get('rejected', []))}"
                           if run.get("rejected") else "0 rejected (every pick matched a source)"], className="small")),
        trace_step(5, "Linker", "AI agent + code", "The model suggests each website; code checks the page loads "
                   "and names the company, else falls back to a web search.",
                   html.Div([html.Span(f"{p['company']}: {'✓ ' + p['website'] if p.get('website') else 'web search'}",
                                       className="chip") for p in picks], className="chips"),
                   tok.get("linker")),
        trace_step(6, "Hiring Check", "code", "Reads each pick's job board to confirm open roles right now.",
                   html.Div([html.Span(f"{p['company']}: " + (
                       f"{p['hiring']['open_roles']} open roles ({p['hiring']['ats']})" if p.get("hiring", {}).get("status") == "hiring"
                       else {"job_board": "job board found", "no_open_roles": "no open roles", "unknown": "couldn't check"}
                       .get(p.get("hiring", {}).get("status"), "—")), className="chip") for p in picks], className="chips")),
    ]
    return html.Details(className="card trace", children=[
        html.Summary("How the agents got here: step-by-step trace of this run"),
        html.Div(steps, className="trace-steps"),
    ])


def hiring_stat(picks):
    checked = [p for p in picks if p.get("hiring")]
    if not checked:
        return stat(len(picks), "companies picked", "pink")
    hiring = sum(1 for p in checked if p["hiring"]["status"] == "hiring")
    return stat(f"{hiring} / {len(picks)}", "picks confirmed hiring now", "pink")


def stat(value, label, color):
    return html.Div(className=f"stat stat-{color}", children=[
        html.Div(str(value), className="stat-num"), html.Div(label, className="stat-label")])


def email_text(email):
    return f"Subject: {email.get('subject', '')}\n\n{email.get('body', '')}"


def bot_bubble(children):
    return html.Div(className="msg msg-bot", children=[
        html.Div("S", className="avatar"), html.Div(children, className="bubble")])


def email_block(email):
    warnings = email.get("warnings", [])
    return html.Div(className="email-card", children=[
        html.Div(className="email-subject", children=[
            html.Span("Subject", className="label"), html.Div(email.get("subject", ""))]),
        html.Div(email.get("body", ""), className="email-body"),
        html.Div(className="case-line", children=[
            html.Span(f"Case study: {email.get('case_study', '')}", className="pill case-pill"),
            html.Span(email.get("why_this_case_study", ""), className="case-why"),
        ]),
        html.Div("📎 Attach “" + email["attachment"] + "” when you send this in SourceWhale.",
                 className="attach-note") if email.get("attachment") else None,
        html.Div([html.Div("⚠ " + w, className="warn") for w in warnings]) if warnings else None,
        html.Div(className="oactions", children=[
            html.Button("Copy email", className="btn btn-primary copy-btn",
                        **{"data-copy": email_text(email)}),
            html.A("Open SourceWhale ↗", href=SOURCEWHALE_URL, target="_blank",
                   className="btn btn-soft"),
        ]),
    ])


def chat_messages(chat):
    items = []
    for m in (chat or {}).get("display", []):
        if m["role"] == "user":
            items.append(html.Div(className="msg msg-user",
                                  children=html.Div(m["text"], className="bubble")))
        elif m.get("error"):
            items.append(bot_bubble(html.Div(m["text"], className="warn")))
        else:
            items.append(bot_bubble([html.P(m["text"]),
                                     email_block(m["email"]) if m.get("email") else None]))
    return items


def company_options(run, pipeline):
    def label(name, where):
        return f"{name}  ·  {where}" + ("  ·  ★ current client" if outreach_writer.existing_client(name) else "")
    options = [{"label": label(p["company"], "today's pick"), "value": p["company"]}
               for p in (run or {}).get("picks", [])]
    seen = {o["value"] for o in options}
    options += [{"label": label(e["company"], "from Targets"), "value": e["company"]}
                for e in pipeline if e["stage"] == "targets" and e["company"] not in seen]
    return options


def outreach_page():
    return html.Div(className="chat-page", children=[
        html.Div(className="page-head", children=[html.Div([
            html.H1("Outreach Assistant"),
            html.P("Pick a company, get a draft that cites the best-fit SciPro case study, then "
                   "tell the assistant what to change.", className="subtitle")])]),
        html.Div(className="chat-shell", children=[
            html.Div(id="chat-scroll", className="chat-scroll", children=[
                bot_bubble([
                    html.P("Hi! Which company do you want to do outreach for right now?"),
                    dcc.Dropdown(id="chat-company", placeholder="Choose a company...",
                                 clearable=False, className="company-dropdown"),
                ]),
                html.Div(id="chat-messages"),
                html.Div(id="chat-thinking", className="msg msg-bot", style={"display": "none"},
                         children=[html.Div("S", className="avatar"),
                                   html.Div(className="bubble typing", children=[
                                       html.Span(), html.Span(), html.Span()])]),
            ]),
            html.Div(className="composer", children=[
                dcc.Textarea(id="chat-input", className="chat-input", disabled=True,
                             placeholder="Choose a company first..."),
                html.Button("Send", id="chat-send", className="btn btn-primary", n_clicks=0,
                            disabled=True),
            ]),
            html.Div(className="chat-foot", children=[
                html.Span(["Enter to send · Shift+Enter for a new line · ",
                           html.Button("Start over", id="chat-reset", className="link-btn",
                                       n_clicks=0)]),
                html.Span(id="chat-tokens"),
            ]),
        ]),
    ])


def pipeline_page():
    return html.Div([
        html.Div(className="page-head", children=[
            html.Div([
                html.H1("BD Pipeline"),
                html.P("Drag companies between stages as each relationship moves forward.",
                       className="subtitle"),
            ]),
            html.Div(className="actions", children=[
                dcc.Input(id="add-company", placeholder="Add a company...", className="text-input",
                          debounce=False, n_submit=0),
                html.Button("Add", id="add-btn", className="btn btn-primary", n_clicks=0),
            ]),
        ]),
        html.Div(id="pipeline-board"),
    ])


def pipeline_board(pipeline):
    total = len(pipeline)
    funnel = []
    for i, (key, label, color) in enumerate(STAGES):
        # how many companies reached at least this stage
        reached = sum(1 for e in pipeline if STAGE_KEYS.index(e["stage"]) >= i)
        share = reached / total if total else 0
        funnel.append(html.Div(className=f"funnel-step stat-{color}", children=[
            html.Div(label, className="stat-label"),
            html.Div(str(sum(1 for e in pipeline if e["stage"] == key)), className="stat-num"),
            html.Div(className="funnel-track", children=[
                html.Div(className="funnel-fill", style={"width": f"{share * 100:.0f}%"})]),
            html.Div(f"{share:.0%} of all targets got this far" if i else f"{total} in pipeline",
                     className="funnel-note"),
        ]))

    columns = []
    for key, label, color in STAGES:
        cards = [pipeline_card(e) for e in pipeline if e["stage"] == key]
        columns.append(html.Div(className=f"kcol kcol-{color}", **{"data-stage": key}, children=[
            html.Div(className="kcol-head", children=[
                html.Span(label), html.Span(str(len(cards)), className="kcount")]),
            html.Div(cards or html.Div("Drop companies here", className="kempty"),
                     className="kcol-cards"),
        ]))
    return html.Div([html.Div(funnel, className="funnel"), html.Div(columns, className="board")])


def pipeline_card(e):
    meta = [html.Span(e["company_type"], className="pill")] if e.get("company_type") else []
    if outreach_writer.existing_client(e["company"]):
        meta.insert(0, html.Span("★ Client", className="pill client-pill"))
    if e.get("hiring_score"):
        meta.append(html.Span(f"{e['hiring_score']}/10", className="kscore"))
    return html.Div(className="kcard", draggable="true", title=e.get("why_now", ""),
                    **{"data-company": e["company"]}, children=[
        html.Div(className="kcard-top", children=[
            company_link(e["company"], e.get("website"), "kname"),
            html.Button("×", id={"type": "remove", "company": e["company"]},
                        className="kremove", n_clicks=0, title="Remove from pipeline"),
        ]),
        html.Div(meta, className="kmeta"),
        html.Div(f"In {STAGE_LABELS[e['stage']]} since {short_date(e.get('stage_since'))}",
                 className="ksince"),
    ])


def field(label, component):
    return html.Div([html.Div(label, className="label field-label"), component])


def pitch_page():
    return html.Div([
        html.Div(className="page-head", children=[html.Div([
            html.H1("Pitch Builder"),
            html.P("Drop in what you know about a company and get a tailored SciPro pitch deck, "
                   "built on SciPro's own deck structure.", className="subtitle")])]),
        html.Div(className="card pitch-form", children=[
            html.Div(className="two-col", children=[
                field("Company", html.Div([
                    dcc.Input(id="pitch-company", list="pitch-company-list", debounce=False,
                              placeholder="Type or pick a company...", className="field"),
                    html.Datalist(id="pitch-company-list")])),
                field("Company website", dcc.Input(id="pitch-website", className="field",
                                                   placeholder="https://...")),
            ]),
            # extras stay hidden (and empty) unless the consultant chooses to customize
            html.Details(className="customize", children=[
                html.Summary("Customize this pitch (optional)"),
                html.P("Leave these empty and the AI picks the best-fit hiring model and capabilities "
                       "from the company's website.", className="muted"),
                html.Div(className="two-col", children=[
                    html.Div([
                        field("Your notes", dcc.Textarea(
                            id="pitch-notes", className="field notes-field",
                            placeholder="Anything you know: call notes, hiring pains, who you're meeting...")),
                        field("News articles (links or pasted text, one per line)", dcc.Textarea(
                            id="pitch-news", className="field notes-field",
                            placeholder="https://endpoints.news/...")),
                    ]),
                    html.Div([
                        field("Hiring models to pitch", dcc.Checklist(
                            id="pitch-models", options=list(pitch_builder.OFFERINGS),
                            value=[], className="check-chips")),
                        field("Capabilities to emphasize", dcc.Checklist(
                            id="pitch-areas", options=pitch_builder.CAPABILITY_AREAS, value=[],
                            className="check-chips")),
                    ]),
                ]),
            ]),
            html.Div(className="oactions", children=[
                html.Button("Build pitch", id="pitch-build", className="btn btn-primary",
                            n_clicks=0),
                html.Span("Pricing is never included: commercial terms stay with the consultant.",
                          className="muted"),
            ]),
        ]),
        html.Div(id="pitch-running", className="running", style={"display": "none"}, children=[
            html.Div(className="dots", children=[html.Span(), html.Span(), html.Span()]),
            html.Div([html.Strong("Agents at work: "),
                      "Fetcher → Researcher → Pitch Writer → Checker. About 20-40 seconds."]),
        ]),
        html.Div(id="pitch-error"),
        html.Div(id="pitch-result"),
        dcc.Download(id="pitch-download"),
    ])


def pitch_result(p):
    if not p:
        return None
    t = p.get("tokens", {})
    return html.Div(className="card pitch-result", children=[
        html.Div(className="ohead", children=[
            html.Div([html.H3(f"Pitch for {p['company']}"),
                      html.P(f"{t.get('input', 0):,} input / {t.get('cached', 0):,} cached / "
                             f"{t.get('output', 0):,} output tokens · arrow keys to move, "
                             "F for full screen", className="muted")]),
            html.Button("Download pitch (HTML)", id="pitch-dl-btn", className="btn btn-primary",
                        n_clicks=0),
        ]),
        html.Div([html.Div("⚠ " + w, className="warn") for w in p.get("warnings", [])]),
        html.Iframe(srcDoc=pitch_builder.render_html(p), className="pitch-frame"),
    ])


# ---------- home ----------

FLOW = [
    ("radar", "1", "Prospect", "Signal Radar",
     "Find the biotechs about to hire: fresh funding, new trials, big milestones.",
     "Scout → Analyst → Ranker → Verifier → Linker", "aqua"),
    ("outreach", "2", "Outreach", "Outreach Assistant",
     "Write a personal first email that cites the SciPro case study that fits.",
     "Outreach Writer → Checker", "mint"),
    ("pitch", "3", "Pitch", "Pitch Builder",
     "Turn research into a tailored SciPro pitch deck for the meeting.",
     "Fetcher → Researcher → Pitch Writer → Checker", "magenta"),
    ("intake", "4", "Job Intake", "Job Intake",
     "Turn the hiring manager's call into a spec, scorecard, and search plan.",
     "Intake Analyst → Spec Writer + Sourcing Strategist → Checker", "teal"),
]
# what moves a card on the Pipeline board between steps
HANDOFFS = ["Card → Targets", "Email sent → Reached Out", "Signed → Client Acquired"]


def home_page():
    steps = []
    for i, (page, num, title, tool, what, agents, color) in enumerate(FLOW):
        steps.append(html.Div(className="flow-step", children=[
            html.Div(num, className=f"flow-sphere sphere-{color}"),
            html.Div(title, className="flow-title"),
            html.P(what, className="flow-what"),
            html.Div([html.Span("AI agents", className="label"), html.Div(agents, className="flow-agents")],
                     className="flow-agent-box"),
            html.Button(f"Open {tool} →", id={"type": "goto", "page": page},
                        className="btn btn-soft flow-btn", n_clicks=0),
        ]))
        if i < len(FLOW) - 1:
            steps.append(html.Div(className="flow-link", children=[
                html.Div(className="flow-line"), html.Div(HANDOFFS[i], className="flow-handoff")]))
    return html.Div([
        html.Div(className="hero", children=[
            html.Div("SciPro BD Dashboard", className="kicker"),
            html.H1(["Business development for recruiters with ", html.Em("purpose.")],
                    className="hero-title"),
            html.P("A recruiter's week moves in four steps, from spotting a company to running "
                   "the search. Each step has its own AI agents, and a human approves every move.",
                   className="subtitle hero-sub"),
        ]),
        html.Div(steps, className="flow"),
        html.Div(className="tracker", children=[
            html.Div(className="tracker-arrows", children=[html.Span("↑") for _ in range(4)]),
            html.Div(className="tracker-bar", children=[
                html.Div([html.Strong("Pipeline board"),
                          html.Span(" tracks every company through the flow: Targets → Reached Out "
                                    "→ Pitch → Client Acquired. Only you move the cards.")]),
                html.Button("Open Pipeline →", id={"type": "goto", "page": "pipeline"},
                            className="btn btn-primary", n_clicks=0),
            ]),
        ]),
        html.Div("Today at a glance", className="label home-label"),
        html.Div(id="home-stats", className="stats"),
    ])


def home_stats(run, pipeline):
    counts = {k: sum(1 for e in pipeline if e["stage"] == k) for k in STAGE_KEYS}
    return [
        stat(len((run or {}).get("picks", [])), "companies found today", "teal"),
        stat(counts["targets"] + counts["reached"], "in Targets or Reached Out", "aqua"),
        stat(counts["pitch"], "in Pitch", "pink"),
        stat(counts["client"], "clients acquired", "mint"),
    ]


# ---------- job intake ----------

def load_intake():
    return json.loads(LATEST_INTAKE.read_text()) if LATEST_INTAKE.exists() else None


def intake_page():
    return html.Div([
        html.Div(className="page-head", children=[html.Div([
            html.H1("Job Intake"),
            html.P("Paste your rough notes from the hiring-manager call. Get a clean job spec, an "
                   "interview scorecard, and a ready-to-run search plan.", className="subtitle")])]),
        html.Div(className="card pitch-form", children=[
            html.Div(className="two-col", children=[
                field("Company (optional)", dcc.Input(id="intake-company", list="pitch-company-list",
                                                      placeholder="Type or pick a company...",
                                                      className="field")),
                field("Role (optional)", dcc.Input(id="intake-role", className="field",
                                                   placeholder="e.g. Senior Director, Clinical Development")),
            ]),
            field("Intake call notes", dcc.Textarea(
                id="intake-notes", className="field intake-notes",
                placeholder="Bullets are fine: must-haves, budget, location, team, process, timeline...")),
            html.Div(className="oactions", children=[
                html.Button("Build search plan", id="intake-build", className="btn btn-primary",
                            n_clicks=0),
                html.Button("Load sample notes", id="intake-sample", className="btn btn-soft",
                            n_clicks=0),
                html.Span("SciPro clients are never suggested as places to recruit from.",
                          className="muted"),
            ]),
        ]),
        html.Div(id="intake-running", className="running", style={"display": "none"}, children=[
            html.Div(className="dots", children=[html.Span(), html.Span(), html.Span()]),
            html.Div([html.Strong("Agents at work: "),
                      "Intake Analyst → Spec Writer + Sourcing Strategist → Checker. About 20-40 seconds."]),
        ]),
        html.Div(id="intake-error"),
        html.Div(id="intake-result"),
    ])


def bullet_list(items):
    return html.Ul([html.Li(x) for x in items or [] if x])


def copy_button(text, label="Copy"):
    return html.Button(label, className="btn btn-soft copy-btn", **{"data-copy": text})


def intake_result(r):
    if not r:
        return None
    brief, spec, src = r["brief"], r["spec"], r["sourcing"]
    js = spec.get("job_spec", {})
    comp = brief.get("compensation") or {}
    t = r.get("tokens", {})

    comp_rows = [html.Div([html.Span(k.title(), className="label"), html.Div(comp.get(k) or "Not given")],
                          className="comp-item") for k in ("base", "bonus", "equity")]
    spec_tab = html.Div(className="intake-tab", children=[
        html.Div(className="ohead", children=[html.H3(js.get("title", "")),
                                              copy_button(job_intake.spec_text(r), "Copy job spec")]),
        html.P(js.get("about_company"), className="muted"),
        html.P(js.get("summary")),
        html.Div(className="two-col", children=[
            html.Div([html.Div("Responsibilities", className="label"), bullet_list(js.get("responsibilities"))]),
            html.Div([html.Div("Requirements", className="label"), bullet_list(js.get("requirements")),
                      html.Div("Preferred", className="label"), bullet_list(js.get("preferred"))]),
        ]),
        html.P([html.Strong("Location: "), js.get("location_line") or "Not given"]),
        html.Div(className="comp-check", children=[
            html.Div("Compensation check", className="label"),
            html.Div(comp_rows, className="comp-grid"),
            html.P(comp.get("notes"), className="muted") if comp.get("notes") else None,
            html.P("Only what the client said. Benchmark against SciPro's salary data before "
                   "advising the client.", className="muted"),
        ]),
    ])

    score_tab = html.Div(className="intake-tab", children=[
        html.Div(className="scorecard", children=[
            html.Div(className="score-row", children=[
                html.Div([html.Div(c.get("competency"), className="score-comp"),
                          html.Div("●" * int(c.get("weight") or 1) + "○" * (3 - int(c.get("weight") or 1)),
                                   className="weight", title="Weight: 3 = critical")]),
                html.Div([html.P(c.get("what_good_looks_like")), bullet_list(c.get("questions"))]),
            ]) for c in spec.get("scorecard", [])
        ]),
        html.Div("Interview plan", className="label"),
        html.Div([html.Span(f"{i}. {st}", className="chip") for i, st in
                  enumerate(spec.get("interview_plan") or brief.get("interview_process") or [], 1)],
                 className="chips"),
    ])

    strings = [("LinkedIn keywords (boolean)", src.get("linkedin_boolean")),
               ("LinkedIn title filter", src.get("linkedin_titles")),
               ("Job boards", src.get("job_board_string"))]
    source_tab = html.Div(className="intake-tab", children=[
        html.Div("Target companies", className="label"),
        html.Div([html.Div([html.Strong(t.get("name")), html.Span(t.get("why"))], className="target")
                  for t in src.get("target_companies", [])], className="targets"),
        html.Div("Adjacent titles", className="label field-label"),
        html.Div([html.Span(x, className="chip") for x in src.get("adjacent_titles", [])], className="chips"),
        *[html.Div([html.Div(className="ohead", children=[html.Div(label, className="label field-label"),
                                                          copy_button(text or "")]),
                    html.Pre(text, className="bool-box")]) for label, text in strings if text],
        html.Div("Sourcing tips", className="label field-label"),
        bullet_list(src.get("sourcing_tips")),
    ])

    questions = brief.get("open_questions") or []
    risks = brief.get("risks") or []
    q_tab = html.Div(className="intake-tab", children=[
        html.Div(className="ohead", children=[html.Div("Ask the hiring manager", className="label"),
                                              copy_button("\n".join(f"- {q}" for q in questions), "Copy questions")]),
        bullet_list(questions),
        html.Div("Search risks to manage", className="label field-label") if risks else None,
        bullet_list(risks),
    ])

    return html.Div(className="card pitch-result", children=[
        html.Div(className="ohead", children=[
            html.Div([html.H3(f"Search plan: {js.get('title', '')}"),
                      html.P(f"{r.get('company') or 'Company not named'} · {t.get('input', 0):,} input / "
                             f"{t.get('cached', 0):,} cached / {t.get('output', 0):,} output tokens",
                             className="muted")]),
        ]),
        html.Div([html.Div("⚠ " + w, className="warn") for w in r.get("warnings", [])]),
        dcc.Tabs(className="intake-tabs", children=[
            dcc.Tab(label="Job spec", children=spec_tab, className="itab", selected_className="itab-on"),
            dcc.Tab(label="Scorecard", children=score_tab, className="itab", selected_className="itab-on"),
            dcc.Tab(label="Sourcing plan", children=source_tab, className="itab", selected_className="itab-on"),
            dcc.Tab(label=f"Questions ({len(questions)})", children=q_tab, className="itab",
                    selected_className="itab-on"),
        ]),
    ])


# ---------- usage & safety ----------

FIRM_FIELDS = [("consultants", "Consultants at the firm"), ("team_size", "Consultants per team"),
               ("radar_scans_per_day", "Radar scans / day"), ("emails_per_day", "Emails / consultant / day"),
               ("turns_per_email", "Chat turns / email"), ("pitches_per_week", "Pitches / consultant / week"),
               ("intakes_per_week", "Intakes / consultant / week"), ("workdays_per_month", "Workdays / month")]


def num_input(id_, value, step="any"):
    return dcc.Input(id=id_, type="number", value=value, min=0, step=step, className="field num-field")


def admin_page():
    firm = signal_radar.CONFIG["firm_defaults"]
    prices = usage.pricing()
    return html.Div([
        html.Div(className="page-head", children=[html.Div([
            html.H1("Usage & Safety"),
            html.P("What the agents cost, the limits that stop overspending, how accurate Signal "
                   "Radar is, and the safety tests that try to break the app.", className="subtitle"),
            html.P("SciPro knowledge in use: " + ("private files (internal deck, case-study PDFs, real "
                   "emails), kept out of the repo" if outreach_writer.using_private_knowledge() else
                   "public website version (data/)"), className="muted knowledge-note")])]),
        dcc.Interval(id="usage-tick", interval=20000),
        html.Div(className="card", children=[
            html.H3("Today's AI budget"),
            html.P("The app refuses to call the model past these limits until midnight "
                   "(set in config.json). Chats are also capped at "
                   f"{signal_radar.CONFIG['chat_max_turns']} messages.", className="muted"),
            html.Div(id="budget-meters", className="meters"),
        ]),
        html.Div(className="card", children=[
            html.H3("Measured tokens per run"),
            html.P("Averages from the real token log (test and safety runs excluded). Each agent's "
                   "reasoning effort is set in config.json.", className="muted"),
            html.Div(id="measured-table"),
        ]),
        html.Div(className="card", children=[
            html.H3("Firm-wide cost plan"),
            html.P("Measured tokens × how a firm would use the app. Enter the model's prices to see "
                   "dollars.", className="muted"),
            html.Div(className="firm-grid", children=[
                html.Div([html.Div("$ / 1M input tokens", className="label"),
                          num_input("price-input", prices.get("input"))]),
                html.Div([html.Div("$ / 1M cached input", className="label"),
                          num_input("price-cached", prices.get("cached_input"))]),
                html.Div([html.Div("$ / 1M output tokens", className="label"),
                          num_input("price-output", prices.get("output"))]),
                *[html.Div([html.Div(label, className="label"), num_input(f"firm-{key}", firm[key])])
                  for key, label in FIRM_FIELDS],
            ]),
            dcc.Checklist(id="firm-shared", options=[{"label": " Share one Signal Radar scan per team "
                                                      "(cost-cutting: the picks are the same for everyone)",
                                                      "value": "on"}],
                          value=["on"], className="offline-toggle shared-toggle"),
            html.Div(id="firm-table"),
        ]),
        html.Div(className="card", children=[
            html.Div(className="ohead", children=[
                html.Div([html.H3("Signal Radar quality check"),
                          html.P("Are the picks really hiring? The Hiring Check reads each pick's job "
                                 "board (free, no AI). Re-check weeks later to see if they still are.",
                                 className="muted")]),
                html.Button("Re-check all past picks", id="quality-recheck", className="btn btn-soft recheck",
                            n_clicks=0),
            ]),
            html.Div(id="quality-panel"),
        ]),
        html.Div(className="card", children=[
            html.Div(className="ohead", children=[
                html.Div([html.H3("Safety tests"),
                          html.P("Each test attacks the app or forces a malfunction and checks that a "
                                 "defense stops it.", className="muted")]),
                html.Div(className="actions", children=[
                    dcc.Checklist(id="safety-live", options=[{"label": " Include live AI injection tests "
                                                              "(~15k tokens)", "value": "on"}],
                                  value=[], className="offline-toggle"),
                    html.Button("Run safety tests", id="safety-run", className="btn btn-primary", n_clicks=0),
                ]),
            ]),
            html.Div(id="safety-results"),
        ]),
    ])


def meters():
    used, limits = signal_radar.today_usage(), signal_radar.CONFIG["daily_limits"]
    rows = []
    for key, label in [("llm_calls", "AI calls"), ("tokens", "Tokens"), ("radar_scans", "Signal Radar scans")]:
        share = min(used[key] / limits[key], 1) if limits[key] else 1
        rows.append(html.Div(className="meter", children=[
            html.Div([html.Span(label, className="label"),
                      html.Span(f"{used[key]:,} / {limits[key]:,}", className="meter-num")],
                     className="meter-head"),
            html.Div(className="meter-track", children=[
                html.Div(className="meter-fill" + (" meter-hot" if share > .8 else ""),
                         style={"width": f"{share * 100:.0f}%"})]),
        ]))
    return rows


def money(x, digits=2):
    return "—" if x is None else (f"${x:,.{digits}f}" if x >= 0.01 or x == 0 else f"${x:.4f}")


def measured_table(prices=None):
    m, prices = usage.per_workflow(), prices or usage.pricing()
    head = html.Tr([html.Th(h) for h in ["Workflow", "Runs measured", "AI calls", "Input", "Cached input",
                                         "Output", "Cost / run"]])
    rows = [html.Tr([html.Td(w["label"]), html.Td(w["runs"]), html.Td(w["calls"]),
                     html.Td(f"{w['input']:,}"), html.Td(f"{w['cached']:,}"), html.Td(f"{w['output']:,}"),
                     html.Td(money(usage.cost_per_run(w, prices), 4))]) for w in m.values()]
    return html.Table([html.Thead(head), html.Tbody(rows)], className="data-table")


def quality_panel():
    hist = hiring_check.history()
    if not hist:
        return html.P("No hiring checks yet. Run Signal Radar or click “Re-check hiring” on it.",
                      className="muted")
    latest = {}
    for row in hist:  # latest check per company
        latest[row["company"]] = row
    rows = list(latest.values())
    checkable = [r for r in rows if r["status"] in ("hiring", "no_open_roles")]
    hiring = [r for r in checkable if r["status"] == "hiring"]
    matched = [r for r in hiring if r.get("matching_roles")]
    stats = html.Div(className="stats", children=[
        stat(len(rows), "picks checked", "teal"),
        stat(f"{len(hiring)} / {len(checkable)}" if checkable else "—", "with a countable job board are hiring", "aqua"),
        stat(f"{len(matched)} / {len(hiring)}" if hiring else "—", "hiring for the roles Radar predicted", "mint"),
        stat(f"{len(rows) - len(checkable)}", "couldn't be counted automatically", "pink"),
    ])
    table = html.Table([
        html.Thead(html.Tr([html.Th(h) for h in ["Company", "Picked", "Last checked", "Result", "Open roles",
                                                 "Match predicted roles"]])),
        html.Tbody([html.Tr([
            html.Td(r["company"]), html.Td(r.get("picked_on") or "—"), html.Td((r.get("checked") or r["date"])[:10]),
            html.Td({"hiring": "✓ Hiring", "no_open_roles": "No open roles", "job_board":
                     f"Job board ({r.get('ats')})", "unknown": "Couldn't check"}[r["status"]]),
            html.Td(r["open_roles"] if r["open_roles"] is not None else "—"),
            html.Td(r["matching_roles"] if r["matching_roles"] is not None else "—"),
        ]) for r in sorted(rows, key=lambda r: r.get("picked_on") or "", reverse=True)]),
    ], className="data-table")
    return html.Div([stats, table])


def safety_results():
    if not safety_tests.REPORT.exists():
        return html.P("No safety tests run yet.", className="muted")
    report = json.loads(safety_tests.REPORT.read_text())
    res = report["results"]
    return html.Div([
        html.P(f"{sum(r['passed'] for r in res)} / {len(res)} passed · last run {report['ran_at'].replace('T', ' ')}",
               className="safety-score"),
        html.Table([
            html.Thead(html.Tr([html.Th(h) for h in ["", "Test", "Risk", "Defense", "Result"]])),
            html.Tbody([html.Tr([
                html.Td("✓" if r["passed"] else "✗", className="pass" if r["passed"] else "fail"),
                html.Td([html.Strong(r["name"]), html.Div(r["attack"], className="muted small")]),
                html.Td(r["risk"]), html.Td(r["defense"], className="small"),
                html.Td(r["detail"], className="small"),
            ]) for r in res]),
        ], className="data-table safety-table"),
    ])


PAGE_KEYS = [key for key, _, _ in NAV]


def serve_layout():
    # a function, so every page load reads the latest saved run and pipeline
    return html.Div(className="shell", children=[
        dcc.Store(id="run", data=load_latest()),
        dcc.Store(id="pipeline", data=load_pipeline()),
        dcc.Store(id="pipeline-move"),
        dcc.Store(id="pitch"),  # starts blank on every visit
        dcc.Store(id="intake", data=load_intake()),
        dcc.Store(id="chat"),          # current Outreach Assistant conversation
        dcc.Store(id="chat-pending"),  # set when the assistant owes a reply
        sidebar(),
        html.Main(className="content", children=[
            html.Div(home_page(), id="page-home"),
            html.Div(pipeline_page(), id="page-pipeline"),
            html.Div(radar_page(), id="page-radar"),
            html.Div(outreach_page(), id="page-outreach"),
            html.Div(pitch_page(), id="page-pitch"),
            html.Div(intake_page(), id="page-intake"),
            html.Div(admin_page(), id="page-admin"),
        ]),
    ])


app.layout = serve_layout


# ---------- callbacks ----------
# Every page is built once and navigation only shows/hides them, so buttons are never
# re-created (re-creating them would re-fire their callbacks and start unwanted runs).

@app.callback([Output(f"page-{k}", "style") for k in PAGE_KEYS]
              + [Output({"type": "nav", "page": ALL}, "className")],
              Input({"type": "nav", "page": ALL}, "n_clicks"),
              Input({"type": "goto", "page": ALL}, "n_clicks"))
def navigate(_nav, _goto):
    page = ctx.triggered_id["page"] if isinstance(ctx.triggered_id, dict) else "home"
    styles = [{"display": "block" if k == page else "none"} for k in PAGE_KEYS]
    return styles + [["nav-item active" if k == page else "nav-item" for k in PAGE_KEYS]]


@app.callback(Output("radar-body", "children"), Output("pipeline-board", "children"),
              Output("chat-company", "options"), Output("pitch-company-list", "children"),
              Output("home-stats", "children"),
              Input("run", "data"), Input("pipeline", "data"))
def render(run, pipeline):
    pipeline = pipeline or []
    options = company_options(run, pipeline)
    return (radar_body(run, pipeline), pipeline_board(pipeline), options,
            [html.Option(value=o["value"]) for o in options], home_stats(run, pipeline))


@app.callback(Output("run", "data"), Output("run-error", "children"),
              Input("run-btn", "n_clicks"), State("offline", "value"),
              running=[(Output("run-btn", "disabled"), True, False),
                       (Output("run-btn", "children"), "Searching...", "Find today's companies"),
                       (Output("running", "style"), {"display": "flex"}, {"display": "none"}),
                       (Output("radar-body", "style"), {"opacity": 0.35}, {"opacity": 1})],
              prevent_initial_call=True)
def run_radar(n_clicks, offline):
    if not n_clicks:
        return no_update, no_update
    try:
        result = signal_radar.run(offline=bool(offline))
    except Exception as e:  # show the problem in the app instead of crashing
        return no_update, html.Div(className="card error", children=[
            html.H3("Something went wrong"), html.P(str(e))])
    LATEST_RUN.write_text(json.dumps(result, indent=2))
    return result, None


@app.callback(Output("pipeline", "data"), Output("add-company", "value"),
              Input({"type": "pick", "company": ALL}, "n_clicks"),
              Input("pipeline-move", "data"),
              Input("add-btn", "n_clicks"), Input("add-company", "n_submit"),
              Input({"type": "remove", "company": ALL}, "n_clicks"),
              State("add-company", "value"), State("run", "data"),
              prevent_initial_call=True)
def update_pipeline(_picks, move, _add, _submit, _removes, new_name, run):
    # buttons re-rendered with n_clicks=0 also trigger this; ignore those
    if not ctx.triggered or not ctx.triggered[0]["value"]:
        return no_update, no_update
    trigger = ctx.triggered_id
    pipeline = load_pipeline()  # the saved file is the source of truth (safe with 2 tabs open)
    by_name = {e["company"]: e for e in pipeline}

    if trigger == "pipeline-move":
        entry = by_name.get(move.get("company"))
        if not entry or move.get("stage") not in STAGE_KEYS or entry["stage"] == move["stage"]:
            return no_update, no_update
        entry["stage"], entry["stage_since"] = move["stage"], date.today().isoformat()
    elif trigger in ("add-btn", "add-company"):
        name = (new_name or "").strip()[:80]
        if not name or name in by_name:
            return no_update, ""
        pipeline.append(new_entry({"company": name, "company_type": "added manually",
                                   "website": lookup_website(name)}))
    elif trigger["type"] == "remove":
        pipeline = [e for e in pipeline if e["company"] != trigger["company"]]
    elif trigger["type"] == "pick":
        entry = by_name.get(trigger["company"])
        if entry is None:
            pick = next((p for p in (run or {}).get("picks", [])
                         if p["company"] == trigger["company"]), {"company": trigger["company"]})
            pipeline.append(new_entry(pick))
        elif entry["stage"] == "targets":
            pipeline.remove(entry)  # clicking again un-targets it
        else:
            return no_update, no_update

    save_pipeline(pipeline)
    return pipeline, ""


def company_details(name, run):
    """Everything we know about a company: Signal Radar pick and/or pipeline entry."""
    pick = next((p for p in (run or {}).get("picks", []) if p["company"] == name), {})
    entry = next((e for e in load_pipeline() if e["company"] == name), {})
    return {**pick, **{k: v for k, v in entry.items() if v}, "company": name}


@app.callback(Output("chat", "data"), Output("chat-input", "value"),
              Output("chat-pending", "data"),
              Input("chat-company", "value"), Input("chat-send", "n_clicks"),
              Input("chat-reset", "n_clicks"),
              State("chat-input", "value"), State("chat", "data"), State("run", "data"),
              prevent_initial_call=True)
def chat_user_turn(company, _send, reset, text, chat, run):
    """Instant step: show the user's message (or start a new company), then ask for a reply."""
    if ctx.triggered_id == "chat-reset" and not reset:
        return no_update, no_update, no_update
    if ctx.triggered_id in ("chat-company", "chat-reset"):
        if not company:
            return no_update, no_update, no_update
        details = company_details(company, run)
        chat = {"company": company, "details": details,
                "history": [outreach_writer.first_turn(details)],
                "display": [{"role": "user", "text": f"Draft an email for {company}"}],
                "tokens": {"input": 0, "cached": 0, "output": 0}}
        return chat, "", datetime.now().isoformat()
    text = (text or "").strip()
    if not text or not chat:
        return no_update, no_update, no_update
    max_turns = signal_radar.CONFIG["chat_max_turns"]
    if sum(1 for m in chat["display"] if m["role"] == "user") >= max_turns:
        # loop guard: a conversation can't grow (and get more expensive) forever
        chat["display"].append({"role": "bot", "error": True, "text":
                                f"This chat has reached {max_turns} messages. Click “Start over” "
                                "below to begin a fresh conversation."})
        return chat, "", no_update
    chat["history"].append({"role": "user", "content": text[:2000]})
    chat["display"].append({"role": "user", "text": text})
    return chat, "", datetime.now().isoformat()


@app.callback(Output("chat", "data", allow_duplicate=True),
              Input("chat-pending", "data"), State("chat", "data"),
              running=[(Output("chat-thinking", "style"), {"display": "flex"}, {"display": "none"}),
                       (Output("chat-send", "disabled"), True, False),
                       (Output("chat-input", "disabled"), True, False),
                       (Output("chat-company", "disabled"), True, False)],
              prevent_initial_call=True)
def chat_assistant_turn(pending, chat):
    """Slow step: the Outreach Writer replies (new draft or revision) and the Checker runs."""
    if not pending or not chat:
        return no_update
    usage = []
    try:
        reply = outreach_writer.chat_turn(signal_radar.get_client(), chat["details"],
                                          chat["history"], "chat-" + pending[:19], usage)
    except Exception as e:
        chat["history"].pop()  # let the user simply try again
        chat["display"].append({"role": "bot", "error": True,
                                "text": f"Sorry, something went wrong: {e}"})
        return chat
    chat["history"].append({"role": "assistant", "content": json.dumps(
        {"message": reply.get("message", ""),
         "email": {k: v for k, v in (reply.get("email") or {}).items() if k != "warnings"}
         or None})})
    chat["display"].append({"role": "bot", "text": reply.get("message", ""),
                            "email": reply.get("email")})
    for u in usage:
        chat["tokens"]["input"] += u["input_tokens"]
        chat["tokens"]["cached"] += u["cached_input_tokens"]
        chat["tokens"]["output"] += u["output_tokens"]
    return chat


@app.callback(Output("chat-messages", "children"), Output("chat-tokens", "children"),
              Output("chat-input", "placeholder"),
              Input("chat", "data"))
def show_chat(chat):
    if not chat:
        return [], "", "Choose a company first..."
    t = chat["tokens"]
    return (chat_messages(chat),
            f"This chat: {t['input']:,} input ({t['cached']:,} cached) / {t['output']:,} output tokens",
            "Tell me what to change, e.g. “shorter and punchier” or “mention contract roles”")


@app.callback(Output("pitch-website", "value"), Input("pitch-company", "value"),
              State("pitch-website", "value"), State("run", "data"), prevent_initial_call=True)
def fill_website(company, current, run):
    """Pre-fill the website when the company is one we already know."""
    known = company_details(company, run).get("website") if company else None
    return known if known and not current else no_update


@app.callback(Output("pitch", "data"), Output("pitch-error", "children"),
              Input("pitch-build", "n_clicks"),
              State("pitch-company", "value"), State("pitch-website", "value"),
              State("pitch-notes", "value"), State("pitch-news", "value"),
              State("pitch-models", "value"), State("pitch-areas", "value"),
              running=[(Output("pitch-running", "style"), {"display": "flex"}, {"display": "none"}),
                       (Output("pitch-build", "disabled"), True, False)],
              prevent_initial_call=True)
def build_pitch(n_clicks, company, website, notes, news, models, areas):
    company = (company or "").strip()[:80]
    if not n_clicks:
        return no_update, no_update
    if not company:
        return no_update, html.Div("Add a company name first.", className="warn")
    website = (website or "").strip()
    if website and not website.startswith("http"):
        website = "https://" + website
    try:
        pitch = pitch_builder.build_pitch(
            signal_radar.get_client(), company, website, (notes or "")[:4000], (news or "")[:8000],
            models or [], areas or [], "pitch-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
    except Exception as e:
        return no_update, html.Div(className="card error", children=[
            html.H3("Something went wrong"), html.P(str(e))])
    LATEST_PITCH.write_text(json.dumps(pitch, indent=2))
    return pitch, None


@app.callback(Output("pitch-result", "children"), Input("pitch", "data"))
def show_pitch(pitch):
    return pitch_result(pitch)


@app.callback(Output("pitch-download", "data"), Input("pitch-dl-btn", "n_clicks"),
              State("pitch", "data"), prevent_initial_call=True)
def download_pitch(n_clicks, pitch):
    if not n_clicks or not pitch:
        return no_update
    name = "".join(c if c.isalnum() else "_" for c in pitch["company"]).strip("_")
    return dict(content=pitch_builder.render_html(pitch), filename=f"SciPro_pitch_{name}.html")


@app.callback(Output("intake-notes", "value"), Input("intake-sample", "n_clicks"),
              prevent_initial_call=True)
def load_sample_notes(n_clicks):
    return job_intake.SAMPLE_NOTES if n_clicks else no_update


@app.callback(Output("intake", "data"), Output("intake-error", "children"),
              Input("intake-build", "n_clicks"),
              State("intake-notes", "value"), State("intake-company", "value"),
              State("intake-role", "value"),
              running=[(Output("intake-running", "style"), {"display": "flex"}, {"display": "none"}),
                       (Output("intake-build", "disabled"), True, False)],
              prevent_initial_call=True)
def build_intake(n_clicks, notes, company, role):
    notes = (notes or "").strip()
    if not n_clicks:
        return no_update, no_update
    if len(notes) < 40:
        return no_update, html.Div("Add your intake call notes first (or load the sample).",
                                   className="warn")
    try:
        result = job_intake.build_intake(
            signal_radar.get_client(), notes[:8000], (company or "").strip()[:80],
            (role or "").strip()[:120], "intake-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
    except Exception as e:
        return no_update, html.Div(className="card error", children=[
            html.H3("Something went wrong"), html.P(str(e))])
    LATEST_INTAKE.write_text(json.dumps(result, indent=2))
    return result, None


@app.callback(Output("intake-result", "children"), Input("intake", "data"))
def show_intake(result):
    return intake_result(result)


@app.callback(Output("run", "data", allow_duplicate=True), Input("recheck-btn", "n_clicks"),
              State("run", "data"),
              running=[(Output("recheck-btn", "disabled"), True, False),
                       (Output("recheck-btn", "children"), "Checking job boards...", "Re-check hiring")],
              prevent_initial_call=True)
def recheck_hiring(n_clicks, run):
    """Hiring Check only (code, no tokens): are today's picks posting open roles?"""
    if not n_clicks or not run or not run.get("picks"):
        return no_update
    hiring_check.check_picks(run["picks"])
    LATEST_RUN.write_text(json.dumps(run, indent=2))
    return run


@app.callback(Output("budget-meters", "children"), Output("measured-table", "children"),
              Output("quality-panel", "children"), Output("safety-results", "children"),
              Input("usage-tick", "n_intervals"))
def refresh_usage(_):
    return meters(), measured_table(), quality_panel(), safety_results()


@app.callback(Output("firm-table", "children"), Output("measured-table", "children", allow_duplicate=True),
              Input("price-input", "value"), Input("price-cached", "value"), Input("price-output", "value"),
              *[Input(f"firm-{key}", "value") for key, _ in FIRM_FIELDS],
              Input("firm-shared", "value"), Input("usage-tick", "n_intervals"),
              prevent_initial_call="initial_duplicate")
def firm_table(p_in, p_cached, p_out, *rest):
    values = dict(zip([k for k, _ in FIRM_FIELDS], rest[:len(FIRM_FIELDS)]))
    shared = bool(rest[len(FIRM_FIELDS)])
    values = {k: (v or 0) for k, v in values.items()}
    prices = {"input": p_in, "cached_input": p_cached, "output": p_out}
    model = usage.firm_model(usage.per_workflow(), prices, values, max(values["team_size"], 1),
                             shared, values["workdays_per_month"] or 21)
    scipro = max(int(values["consultants"]), 1)
    rows = []
    for label, users in [("1 consultant", 1), (f"SciPro ({scipro} consultants)", scipro),
                         ("100 users", 100), ("10,000 users", 10_000)]:
        r = model(users)
        per_user = None if r["dollars"] is None else r["dollars"] / users
        rows.append(html.Tr([html.Td(label), html.Td(f"{r['tokens'] / 1e6:,.1f}M"),
                             html.Td(money(r["dollars"])), html.Td(money(per_user)),
                             html.Td(money(None if r["dollars"] is None else r["dollars"] * 12, 0))]))
    note = None if None not in prices.values() else html.P(
        "Enter the three prices above to see dollars. Token totals are already measured.", className="muted")
    return html.Div([note, html.Table([
        html.Thead(html.Tr([html.Th(h) for h in ["Scale", "Tokens / month", "Cost / month",
                                                 "Cost / user / month", "Cost / year"]])),
        html.Tbody(rows)], className="data-table")]), measured_table(prices)


@app.callback(Output("safety-results", "children", allow_duplicate=True), Input("safety-run", "n_clicks"),
              State("safety-live", "value"),
              running=[(Output("safety-run", "disabled"), True, False),
                       (Output("safety-run", "children"), "Running tests...", "Run safety tests")],
              prevent_initial_call=True)
def run_safety(n_clicks, live):
    if not n_clicks:
        return no_update
    safety_tests.run_all(live=bool(live))
    return safety_results()


@app.callback(Output("quality-panel", "children", allow_duplicate=True), Input("quality-recheck", "n_clicks"),
              running=[(Output("quality-recheck", "disabled"), True, False),
                       (Output("quality-recheck", "children"), "Checking job boards...", "Re-check all past picks")],
              prevent_initial_call=True)
def recheck_all(n_clicks):
    """Re-check every past pick (code only, no tokens) to see whether it's still hiring."""
    if not n_clicks:
        return no_update
    latest = {}
    for row in hiring_check.history():
        latest[row["company"]] = row
    picks = [{"company": r["company"], "website": r.get("website"), "likely_roles": r.get("likely_roles"),
              "picked_on": r.get("picked_on")} for r in latest.values()]
    hiring_check.check_picks(picks)
    return quality_panel()


if __name__ == "__main__":
    app.run(debug=False, port=int(os.environ.get("PORT", 8050)))
