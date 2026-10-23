"""Hiring Check (code, no LLM): is a Signal Radar pick actually hiring right now?

This is the quality check on Signal Radar's predictions. For each company it:
  1. reads the company website + careers page and looks for the applicant-tracking system
     (Greenhouse, Lever, Ashby, Workable, BambooHR) most biotechs post jobs through,
  2. falls back to guessing the job-board name from the company name,
  3. counts open roles from the ATS's public job-board API, and
  4. checks how many open titles match the roles the Ranker predicted.
Results are appended to data/hiring_checks.jsonl so picks can be re-checked weeks later.
"""

import json
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from urllib.parse import urljoin

import requests

from signal_radar import DATA_DIR, USER_AGENT, normalize

HISTORY = DATA_DIR / "hiring_checks.jsonl"
HEADERS = {"User-Agent": USER_AGENT}

ATS_PATTERNS = [
    ("greenhouse", r"(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/(?:embed/job_board(?:/js)?\?for=)?([a-z0-9_-]+)"),
    ("greenhouse", r"greenhouse\.io/embed/job_board/js\?for=([a-z0-9_-]+)"),
    ("lever", r"jobs\.lever\.co/([a-z0-9_.-]+)"),
    ("ashby", r"jobs\.ashbyhq\.com/([a-z0-9_.%-]+)"),
    ("workable", r"apply\.workable\.com/([a-z0-9_-]+)"),
    ("bamboohr", r"([a-z0-9-]+)\.bamboohr\.com"),
]
# job boards without a public listings API: we can link to them but not count roles
OTHER_BOARDS = {"paylocity": "Paylocity", "icims": "iCIMS", "myworkdayjobs": "Workday",
                "jobvite": "Jobvite", "smartrecruiters": "SmartRecruiters", "rippling": "Rippling",
                "adp": "ADP", "ultipro": "UKG", "breezy": "Breezy", "jazzhr": "JazzHR",
                "applytojob": "JazzHR", "recruitee": "Recruitee", "dover": "Dover"}
ATS_NAMES = {"greenhouse": "Greenhouse", "lever": "Lever", "ashby": "Ashby",
             "workable": "Workable", "bamboohr": "BambooHR"}
IGNORE_SLUGS = {"embed", "api", "js", "www", "jobs", "careers", "app"}
GENERIC_WORDS = {"senior", "sr", "director", "manager", "lead", "associate", "head", "vp", "vice",
                 "president", "of", "and", "the", "executive", "principal", "staff", "specialist",
                 "i", "ii", "iii", "a", "for", "chief", "officer", "global"}


def get(url, as_json=False, tries=2):
    for _ in range(tries):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=12)
            if resp.status_code == 404:
                return None
            if resp.status_code == 200:
                return resp.json() if as_json else resp.text
        except ValueError:
            return None
        except requests.RequestException:
            pass
    return None


def fetch_jobs(ats, slug):
    """Open jobs as [{"title", "url"}] from an ATS's public API, or None if the board isn't there."""
    if ats == "greenhouse":
        d = get(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs", True)
        return None if d is None else [{"title": j["title"], "url": j.get("absolute_url")}
                                       for j in d.get("jobs", [])]
    if ats == "lever":
        d = get(f"https://api.lever.co/v0/postings/{slug}?mode=json", True)
        return None if not isinstance(d, list) else [{"title": j["text"], "url": j.get("hostedUrl")}
                                                     for j in d]
    if ats == "ashby":
        d = get(f"https://api.ashbyhq.com/posting-api/job-board/{slug}", True)
        return None if d is None else [{"title": j["title"], "url": j.get("jobUrl")}
                                       for j in d.get("jobs", [])]
    if ats == "workable":
        d = get(f"https://apply.workable.com/api/v1/widget/accounts/{slug}", True)
        return None if d is None else [{"title": j["title"], "url": j.get("url")}
                                       for j in d.get("jobs", [])]
    if ats == "bamboohr":
        d = get(f"https://{slug}.bamboohr.com/careers/list", True)
        return None if d is None else [{"title": j.get("jobOpeningName"), "url": None}
                                       for j in d.get("result", [])]
    return None


def find_boards(website):
    """(ats, slug) pairs referenced on the website or its careers pages, the careers URL, and
    any job board we can link to but not count."""
    pages, careers, other = [], None, None
    home = get(website) if website else None
    if home:
        pages.append(home)
        links = []
        for link in re.findall(r'href=["\']([^"\']+)["\']', home, re.I):
            if (re.search(r"career|jobs|join|opportunit|work-with-us", link, re.I)
                    and not link.startswith(("mailto:", "data:", "#"))):
                url = urljoin(website, link).split("#")[0]
                if url not in links:
                    links.append(url)
        for url in links[:3]:  # e.g. /careers/ then /careers/jobs/
            page = get(url)
            if page:
                careers = careers or url
                pages.append(page)
                for sub in re.findall(r'href=["\']([^"\']*(?:jobs|openings|positions)[^"\']*)["\']', page, re.I)[:2]:
                    sub_url = urljoin(url, sub)
                    if sub_url not in links and sub_url.startswith("http") and len(pages) < 6:
                        links.append(sub_url)
                        sub_page = get(sub_url)
                        if sub_page:
                            pages.append(sub_page)
    for page in pages:
        for key, name in OTHER_BOARDS.items():
            m = re.search(r'https?://[a-z0-9.-]*' + key + r'[a-z0-9./?=&%_-]*', page, re.I)
            if m and not other:
                other = (name, m.group(0))
    found = []
    for page in pages:
        for ats, pattern in ATS_PATTERNS:
            for slug in re.findall(pattern, page, re.I):
                slug = slug.lower().strip("/")
                if slug not in IGNORE_SLUGS and (ats, slug) not in found:
                    found.append((ats, slug))
    return found, careers, other


def guess_slugs(company):
    base = normalize(company).replace(" ", "")
    words = re.sub(r"[^a-z0-9 ]", "", company.lower()).split()
    full = "".join(w for w in words if w not in {"inc", "corp", "ltd", "plc", "llc"})
    return list(dict.fromkeys(s for s in [full, base, "-".join(words[:2])] if len(s) >= 4))


def role_keywords(likely_roles):
    words = set()
    for role in likely_roles or []:
        words |= {w for w in re.findall(r"[a-z]+", role.lower()) if w not in GENERIC_WORDS and len(w) > 2}
    return words


def check_company(company, website=None, likely_roles=None):
    boards, careers, other = find_boards(website)
    tried = list(boards) + [(ats, s) for s in guess_slugs(company)
                            for ats in ("greenhouse", "lever", "ashby")]
    for ats, slug in tried:
        jobs = fetch_jobs(ats, slug)
        if jobs is None:
            continue
        from_site = (ats, slug) in boards
        if not from_site and (not jobs or len(slug) < 10):
            # a board found only by guessing the name is trusted only when the guess is the
            # full, distinctive company name (e.g. "generatebiomedicines"); a short name like
            # "pathos" can belong to a different company with the same name
            continue
        keywords = role_keywords(likely_roles)
        matching = [j["title"] for j in jobs if j["title"] and
                    keywords & set(re.findall(r"[a-z]+", j["title"].lower()))]
        return {"status": "hiring" if jobs else "no_open_roles", "open_roles": len(jobs),
                "matching_roles": len(matching), "ats": ATS_NAMES[ats],
                "found_via": "website" if from_site else "name match",
                "titles": [j["title"] for j in jobs][:10], "matching_titles": matching[:6],
                "board_url": next((j["url"] for j in jobs if j.get("url")), careers)}
    if other:  # a job board exists but has no public API to count roles
        return {"status": "job_board", "open_roles": None, "matching_roles": None, "ats": other[0],
                "found_via": "website", "titles": [], "matching_titles": [], "board_url": other[1]}
    return {"status": "unknown", "open_roles": None, "matching_roles": None, "ats": None,
            "found_via": None, "titles": [], "matching_titles": [], "board_url": careers}


def check_picks(picks, save=True):
    """Hiring-check every pick in parallel; attaches p["hiring"] and logs history."""
    with ThreadPoolExecutor(max_workers=5) as pool:
        results = list(pool.map(lambda p: check_company(p["company"], p.get("website"),
                                                        p.get("likely_roles")), picks))
    for p, r in zip(picks, results):
        r["checked"] = datetime.now().isoformat(timespec="seconds")
        p["hiring"] = r
    if save:
        with HISTORY.open("a") as f:
            for p, r in zip(picks, results):
                f.write(json.dumps({"date": date.today().isoformat(), "company": p["company"],
                                    "website": p.get("website"), "likely_roles": p.get("likely_roles"),
                                    "picked_on": p.get("picked_on"), **r}) + "\n")
    return picks


def history():
    if not HISTORY.exists():
        return []
    return [json.loads(line) for line in HISTORY.read_text().splitlines() if line.strip()]
