# SciPro BD Dashboard

Repo: https://github.com/sofialopresti/scipro-bd-dashboard · Report: `report.html` · Slides: `slides.html`

An AI agent app for life-sciences recruiters. Every day it finds 5 US biotech / AI drug discovery
companies that are likely to hire, explains why now, drafts outreach emails in a chat,
builds tailored pitch decks, and turns job intake notes into a search plan.

## Run it

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python app.py
```

Then open http://127.0.0.1:8050 (set `PORT=xxxx` to use a different port)

The app needs a `PORTKEY_API_KEY` (see `.env.example`). It looks for `.env` in this folder first,
then in the folder one level up.

## Pages

- **Home**: a flow diagram of the recruiter's workflow (Prospect → Outreach → Pitch → Job
  Intake), the AI agents behind each step, and today's numbers.
- **Pipeline**: drag companies between Targets → Reached Out → Pitch → Client Acquired.
  Saved to `data/pipeline.json`.
- **Signal Radar**: today's 5 companies; "+ Add to targets" puts one on the Pipeline board.
  Companies already on SciPro's client list get a "★ Current SciPro client" badge, and the
  Outreach Writer and Pitch Builder treat them as account expansion instead of cold outreach.
- **Outreach Assistant**: a chat. Pick one of today's 5 companies (or a Targets company) and
  the Outreach Writer drafts an email in a SciPro consultant's style: specific congrats → who
  I am / how SciPro is structured → similar SciPro clients → best-matching case study → ask for
  20-30 minutes. Reply with feedback ("shorter", "more
  casual") to revise. A Checker flags case studies that aren't real and numbers not found in
  the sources. Copy the email and paste it into SourceWhale. Case studies live in
  `data/scipro_capabilities.md`; add real example emails to `data/example_emails.md` to match
  the recruiter's voice.
- **Pitch Builder**: enter a company, website, notes, and news links; pick hiring models and
  capabilities to pitch. Fetcher → Researcher → Pitch Writer → Checker build a 10-slide deck on
  SciPro's own pitch structure, viewable in the app and downloadable as one HTML file. Pricing
  is never included, the candidate-profile slide is a placeholder, and the Checker removes any
  case study or client that isn't SciPro's.
- **Usage & Safety**: today's AI budget vs. the daily limits, measured tokens per run for
  each workflow, a firm-wide cost calculator (enter the model's $/1M-token prices), the Signal
  Radar quality check (are picks really hiring?), and a "Run safety tests" button.
- **Job Intake**: paste rough hiring-manager call notes (or click "Load sample notes").
  Intake Analyst → Spec Writer + Sourcing Strategist (in parallel) → Checker produce a job spec,
  compensation check (only what the client said, never invented), interview scorecard, target
  companies, LinkedIn / job-board boolean strings, and questions for the client. The Checker
  removes SciPro clients from the target list (never recruit from clients) and validates the
  boolean strings.

## Public vs. private SciPro data

The app's SciPro knowledge (capabilities, case studies, client list, example emails) comes in two
versions:

- `data/` (committed): built only from SciPro's public website, plus a fictional example email.
  A fresh clone runs fully on this version.
- `private/` (gitignored, never committed): SciPro's internal pitch deck, case-study PDFs, and
  real consultant emails. When this folder exists, the app uses it automatically (richer client
  list and stats, and it reminds you which case-study PDF to attach).

The Usage & Safety page shows which version is in use, and the safety tests check that nothing
private-only appears in the public files. SciPro's fee terms are never stored in either version.

## Sharing a test link (optional)

Set `APP_PASSWORD` in `.env` to put a password screen in front of the app (any username works),
then run `cloudflared tunnel --url http://127.0.0.1:8050` for a temporary public link.

## Test data

Check **Use saved sample data** on the Signal Radar page to run the agents on the saved news and
trial data in `data/sample_sources.json` instead of pulling live sources.

## Command line

```bash
.venv/bin/python signal_radar.py             # live run, prints today's 5 companies
.venv/bin/python signal_radar.py --offline   # run on the saved sample data
```

## How Signal Radar works

1. **Scout** (code): biotech news RSS (BioSpace, STAT, BioPharma Dive, Endpoints) + new
   industry-sponsored Phase 2/3 drug trials with US sites from ClinicalTrials.gov.
2. **Analyst** (`gpt-6-luna`): extracts company hiring signals; drops big pharma, non-US companies,
   devices, diagnostics, health tech, CROs, and CDMOs.
3. **Ranker** (`gpt-6-luna`): merges news + trials and picks the top 5 with a "why now".
4. **Verifier** (code): rejects any pick not matched to a real source, big pharma, and companies
   shown on a previous day in the last 14 days.
5. **Linker** (`gpt-6-luna` + code): suggests each company's website, then code checks the link
   loads and mentions the company. Otherwise the name links to a web search instead.

6. **Hiring Check** (code): reads each pick's job board (Greenhouse, Lever, Ashby, Workable,
   BambooHR) to confirm it has open roles and whether they match the predicted roles.

Before any model sees scraped text, the **Sanitizer** (code) quarantines articles containing
prompt-injection phrases. Token usage for every model call is logged to `data/token_usage.jsonl`.

## Models, limits, and safety

- Every agent uses `gpt-6-luna` (the course key's model). `config.json` sets each agent's
  reasoning effort (low for extraction, medium for judgment calls) and an output cap.
- `daily_limits` in `config.json` stop model calls past a daily number of calls, tokens, and
  Signal Radar scans; chats are capped at `chat_max_turns` messages.
- `.venv/bin/python safety_tests.py` runs 10 free safety tests; add `--live` for 2 real
  prompt-injection attempts against the model. Results show on the Usage & Safety page.
- `.venv/bin/python experiment_cost_quality.py --repeats 2` tests whether Signal Radar's
  cost cuts (fewer articles, lower Ranker effort) hurt quality, measured by real job boards.

## Report and slides

`report.html` (13 printed pages) and `slides.html` (10 reveal.js slides) are self-contained:
open them by double-clicking. All images are embedded; slides load reveal.js from a CDN
(arrow keys to move, F for full screen).
