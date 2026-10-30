# Mr. Job Consultant

<img src="app/static/icon.svg" alt="" width="72" align="right">

Finds jobs that fit you across hundreds of login-free job sites, and helps you apply with a tailored
resume and cover letter that only use facts from your own CV.

**Version 0.1.0** · [MIT License](LICENSE) · [Changelog](CHANGELOG.md) · [Contributors](CONTRIBUTORS.md): @vivekchakraverty and Claude (Anthropic)

## Quick start

```bash
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m uvicorn app.server:app --port 8765
```

Then open http://127.0.0.1:8765. The job-portal directory in `data/public_job_portals.json` is
included; rebuild or extend it with the crawler (below).

## The app (`app/`)

The main screen collects what job sites ask for: job title, locations (city, region,
country, or worldwide), job type (remote / full-time / part-time / contract), schedule (fixed
with optional shifts, or flexible) and a CV. Name, email, phone and LinkedIn are read from the
CV so they can be reused in application forms.

```bash
.venv\Scripts\python -m uvicorn app.server:app --port 8765
```

Then open http://127.0.0.1:8765. The profile is saved to `data/search_profile.json` and CVs to
`data/cv/`. Location suggestions live in `app/static/locations.json`.

### Related job titles

Typing a title in step 1 suggests related ones instantly, with no AI and no token (`app/titles.py`):
seniority variants ("Senior Content Writer"), curated role families in `app/title_families.json`
("Content Marketer", "Copywriter", "Content Strategist"…), and real titles from the current
listings, each with its number of open jobs. One seniority step plus the three closest roles are
pre-selected; the rest are one tap away, and any other title can be typed in. The search then
matches all selected titles (`related_titles` in the profile). The typed title counts in full and
related ones at 90%, and each result shows which title it matched. Add families to the JSON file
to cover more fields.

### Finding jobs

"Save & find jobs" collects current posts from the directory (`app/jobs.py`): the RSS feeds the
crawler found plus the public APIs of Remotive, RemoteOK, Himalayas and Jobicy. Results are
cached for 6 hours in `data/jobs_cache.json`; "Fetch fresh jobs" bypasses the cache.

Step 7 picks how jobs are ranked:

* **Title & location only**: no AI, just title matching.
* **Match to my CV** (optional, `app/matching.py`): `intfloat/multilingual-e5-small` ranks
  every job against the CV, then `Qwen/Qwen3-4B-Instruct-2507` reviews the top 12 and explains
  the fit and gaps.

**Who pays:** every AI call uses the Hugging Face token of the person searching, never the
app owner's, in this order:

1. **ZeroGPU Space** (`app/hf_space/`, public at
   [vivekchakraverty/job-cv-matcher](https://huggingface.co/spaces/vivekchakraverty/job-cv-matcher)):
   calls count against the *caller's* free daily ZeroGPU quota, so the Space owner pays nothing for
   other people's searches.
2. **HF Inference Providers**: only if the person ticks the backup box; uses *their* monthly free
   credits.
3. **No token**: free keyword matching on this machine's CPU.

Tokens typed into the page stay in that browser and are sent per request. The Hugging Face login
cached on this computer (`hf auth login`) is only used for requests from this computer itself:
loopback address, loopback Host name, and no proxy forwarding headers. Behind a reverse proxy,
visitors therefore never get it. Set `"allow_local_login": false` in `data/ai_settings.json` to
turn it off entirely. The app also sets `HF_HUB_DISABLE_IMPLICIT_TOKEN=1`, so the HF libraries
can never silently fall back to the saved login, and AI calls refuse to run without the
searcher's own token. The Space id is
also stored there; "Set up my ZeroGPU Space" in step 7 creates one on a PRO account.

### Tailored resume, cover letter and applying

"View job" on a result opens a panel that fetches the full job post and its real apply link
(`app/jobpage.py`; a plain web request, no AI). "Create documents & open application" then:

1. **Reads the CV's facts** into structured data with the LLM, and **checks every extracted
   employer, title, date, degree and bullet against the CV text** (`app/tailor.py`). Anything not
   found is dropped.
2. **Tailors** the resume: the model may only choose, reorder and lightly rephrase the CV's own
   bullets. Code rejects any rewrite that adds a number, a name or tool, or wording from the job ad
   that the original bullet didn't contain, and keeps the original wording instead. Skills,
   headline and summary face the same checks against the whole CV.
3. **Writes an optional cover letter**, verified sentence by sentence. Claims about the candidate
   must be in the CV, and sentences that fail are removed (with one retry that names what wasn't allowed).
4. **Renders ATS-friendly files** (`app/render.py`) as PDF, DOCX and/or TXT: one column, standard
   headings, plain bullets, contact details in the body, selectable text. Files are saved under
   `data/applications/`.
5. **Opens the application page** in a new tab, with a button as a fallback if the browser blocks it.

The panel lists every change or omission the checks made, and the job requirements the CV
doesn't show (these are never added).

Writing engines, always billed to the requester's own token:

* **Free**: `Qwen/Qwen3.6-35B-A3B-FP8` on the public ZeroGPU Space
  [vivekchakraverty/job-application-writer](https://huggingface.co/spaces/vivekchakraverty/job-application-writer)
  (code in `app/hf_space_writer/`, id in `writer_space_id` in `data/ai_settings.json`), on the
  caller's daily ZeroGPU quota, so the owner pays nothing for other people's documents.
* **HF Inference**: `Qwen/Qwen3.8-27B`, the best faithful-extraction score on
  `llamaindex/ExtractBench` among models HF can serve, falling back to `Qwen/Qwen3.6-35B-A3B` and
  `deepseek-ai/DeepSeek-V4.1-Flash`. Uses the caller's own Inference credits (about 1–2¢).
* **No token**: the CV is reformatted for ATS as it is: no tailoring, no letter, no AI.

## Job portal crawler (`portal_crawler/`)

Finds job portals, big and small, records details about each site, and checks whether
**individual job posts can be read without logging in**. The exported list contains only
portals where that check passed.

## Setup

```bash
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
# optional, for --render (JS-heavy job pages):
.venv\Scripts\python -m playwright install chromium
```

## Usage

```bash
# everything: seeds -> directory pages -> crawl -> export
.venv\Scripts\python -m portal_crawler run --max-depth 2

# add web-search discovery for smaller niche/regional boards
.venv\Scripts\python -m portal_crawler run --search --max-queries 60

# step by step / resume (state lives in data/portals.db)
.venv\Scripts\python -m portal_crawler seed
.venv\Scripts\python -m portal_crawler discover --search
.venv\Scripts\python -m portal_crawler crawl --limit 500 --render
.venv\Scripts\python -m portal_crawler export
.venv\Scripts\python -m portal_crawler stats

# re-test portals whose verdict was unknown, with a headless browser
.venv\Scripts\python -m portal_crawler recheck --states portal --access unknown
.venv\Scripts\python -m portal_crawler crawl --render
```

Output: `data/public_job_portals.csv` and `.json`. Use
`export --include public,soft_wall` to also include sites that show the post but nag for a login.

## How it works

1. **Discovery** — about 90 built-in seed portals (`seeds.py`), sites listed on directory
   pages (Wikipedia's list of employment websites and the "awesome job boards" lists), optional
   DuckDuckGo queries (`--search`), and job-related links found on each confirmed portal
   (`--max-depth`).
2. **Classification** (`classify.py`): the homepage is scored for job-search wording, a job
   search form, employer "post a job" wording, `JobPosting` structured data and links to job
   posts. Single-company career pages and ATS hosts (Greenhouse, Lever, Workday…) are excluded.
3. **Details** (`extract.py`): name, description, language, country, niches, rough job count
   and size tier, search form, employer posting, job alerts, RSS, API, platform, socials, contact
   email.
4. **Login check** (`access.py`): finds real job-post URLs from the homepage, listing pages or
   the sitemap, opens up to 3 of them with no cookies, and labels the portal:

   | access | meaning |
   |---|---|
   | `public` | job post readable anonymously, so it's exported |
   | `soft_wall` | post is in the page but it demands sign-in/sign-up |
   | `login_required` | redirect to login, HTTP 401, or a sign-in prompt instead of the post |
   | `blocked` | site refuses the crawler (403/405/429, bot challenge), so it isn't verified |
   | `robots_disallowed` | robots.txt forbids it, so it isn't checked |
   | `unknown` | no job content in static HTML (try `--render`) |

   The `confidence` column says how the `public` verdict was observed: `high` = a job post with
   `JobPosting` data, `medium` = a post's full text, `low` = only listing pages were readable.

## Limits

* The crawler honours robots.txt, waits 1.5 s between requests to a host, and does not try to
  get past bot protection. Many large portals (Monster, Seek, StepStone, Naukri…) refuse plain
  HTTP clients, so they end up `blocked` and are left out.
* `access_source = seed_hint` means the crawler couldn't verify the site and used the
  hand-written hint in `seeds.py` (e.g. Indeed, LinkedIn). Use `export --verified-only` to drop them.
* To record what you've checked by hand, create `data/access_overrides.csv` with columns
  `key,access,note` (key as in the `key` field of the JSON). Overrides are applied at export and
  marked `access_source = manual`.
