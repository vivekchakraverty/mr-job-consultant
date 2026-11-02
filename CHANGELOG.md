# Changelog

## Unreleased

### Added
- **Update notice**: when a newer release is out, a banner says so, with the download for your
  system (desktop builds) or a link to the update steps (running from source), and what's new.
  Dismiss it until the next version. The app checks GitHub's latest release at most every 12 hours,
  shows nothing if GitHub can't be reached, never installs anything by itself, and can be turned off
  (`"check_for_updates": false` in `ai_settings.json`, or `MRJC_NO_UPDATE_CHECK=1`).

## 0.3.0 (2026-11-02)

Practise tough interview questions on camera and get feedback, and run the app without installing
anything: ready-to-run downloads for Windows, macOS and Linux.

### Added
- **Interview tab**: record yourself answering five common but tough non-technical questions (tell
  me about yourself, your greatest weakness, a time you failed, a conflict with a colleague, why we
  should hire you). Re-record and replay any answer. "Get my feedback" then scores your **body
  language, articulation and mindset** (1–5) against what interviewers commonly look for, and lists
  the flaws with their evidence and a fix, what worked, and how a stronger answer would go.
  - On your own computer, free: speech to text with timings (`onnx-community/whisper-base`), 17 body
    keypoints per frame (`onnx-community/vitpose-base-simple`), and smiles
    (`Xenova/facial_emotions_image_detection`). From these: pace, filler and hedging words, pauses,
    length, facing the camera, posture, head tilt, hands, fidgeting and framing.
  - The free writer Space, on your own Hugging Face account, writes the feedback from the transcript
    and those measurements. Quotes it gives as evidence are checked against what you said.
  - Recordings stay on your computer; only the transcript and the measurements go to the AI. The
    models (about 240 MB) download the first time you ask for feedback.
- **Desktop builds** for Windows x64, macOS (Apple Silicon and Intel) and Linux x64, each with
  Python, Node.js and every package inside. Run `MrJobConsultant` and the app opens in your browser.
  Running it again while it's open just shows the open one; if port 8765 is taken, it picks another.
- Builds keep your data in your user folder (`%APPDATA%\MrJobConsultant`,
  `~/Library/Application Support/MrJobConsultant` or `~/.local/share/MrJobConsultant`), so installing
  a new version never touches it. Running from source still uses `./data`; `MRJC_DATA_DIR`
  overrides either.
- `python packaging/build.py` builds for the system it runs on and self-checks the result;
  `.github/workflows/build.yml` builds all four on GitHub Actions and attaches them to a release.
- `python -m app.launcher` starts the app from source the same way the builds do.

### Notes
- The builds aren't code-signed yet: Windows SmartScreen may warn, and macOS needs the download flag
  cleared once (see "Download" in the README).
- Interview feedback takes 1–2 minutes per answer on the free GPU. The tab needs a camera and
  microphone; it has been tested in Chromium-based browsers (Chrome, Edge).

## 0.2.1 (2026-11-02)

Type any skill in Reskilling, and upgrade from 0.1.0 safely. To upgrade, follow "Upgrading from
0.1.0" in the README (the same steps apply from 0.2.0).

### Changed
- **Reskilling**: you now type the skill you want to learn, instead of picking from AI-suggested
  career moves. The learning plan, videos, viewed tracking and tests work exactly as before, and
  the plan still starts from your CV. Upskilling keeps its suggestions.

### Fixed
- Upgrading from 0.1.0: without `npm install`, video lookups now say what to run straight away
  instead of first downloading the 125 MB sentiment model. Without yt-dlp, tests say to re-run
  `pip install -r requirements.txt` instead of failing with an import error. The README has an
  "Upgrading from 0.1.0" section.
- A topic's top 3 videos no longer include the same lesson twice from different channels.

## 0.2.0 (2026-11-02)

Learn the skills the job asks for: two new tabs turn your CV into a learning plan, with the best
videos for every topic and a test on each one. Everything new is free: AI runs on your own
Hugging Face ZeroGPU time, and the rest runs on your computer.

### Upskilling and Reskilling tabs
- **Upskilling** suggests 8 specific skills that build on your CV for your current role, or for a
  role you type in, each with why it pays off and what in your CV it builds on.
- **Reskilling** suggests 3 realistic new roles, the strengths that carry over, and the skills each
  one needs.
- Both start from the CV saved with your job search (reusing its already-extracted facts, with no
  new AI call), or a CV attached in the tab. Skills your CV already names are left out and listed.
- Pick any skill for a learning plan aimed at moderate proficiency: 4–6 units, modules, concrete
  topics, a practice task per module, a checkpoint per unit and a capstone project. Download it as
  PDF or DOCX. Plans are saved, so reopening one is instant.

### Videos for every topic
- Each topic is a collapsible section with its top 3 YouTube videos embedded.
- Videos are found with youtubei.js (no login, no API key) and ranked on the sentiment of their top
  comments, likes per view, how well they match the topic, views and length. Videos with only a
  handful of views or comments can't win on a lucky ratio.
- Sentiment comes from `cardiffnlp/twitter-roberta-base-sentiment-latest`, run on your CPU.

### Progress and tests
- **Mark as viewed** under every video; each topic's header shows what you've watched.
- **Take test**: yt-dlp reads the video's captions and the AI writes 10 multiple-choice questions on
  one screen, each checked in code (four distinct options, one answer, no repeats). Answers stay on
  your computer until you submit; you then see your score, the right answers and a one-line reason
  for each.
- Every score is saved with its date. **Retake** writes a fresh set from other parts of the
  transcript and avoids every question you've already had.

### Setup
- Needs Node.js 20+ for the videos: run `npm install --prefix app/youtube` once.
- `pip install -r requirements.txt` adds yt-dlp. The sentiment model (about 125 MB) downloads by
  itself on first use.

### Notes
- The free model generates about 10 tokens a second on ZeroGPU, so a learning plan takes 3–4
  minutes and a test 2–3 minutes. Each call is kept small enough to fit the writer Space's GPU
  budget; the Space itself is unchanged.

## 0.1.0 (2026-10-30)

First release.

### Job portal crawler (`portal_crawler/`)
- Discovers job portals of every size from seed lists, directory pages, optional web search and links between portals.
- Records site details (niches, size, country, language, search form, RSS, API, platform, contacts).
- Opens real job posts anonymously and keeps only portals whose posts can be read **without logging in**.
- Respects robots.txt, rate-limits per host, never works around bot protection.

### App (`app/`)
- Calm, step-by-step search profile: job title, locations (cities, regions, countries or worldwide),
  job type, schedule and shifts, CV upload with contact extraction, optional application details
  (work authorization, sponsorship, pay, start date, years of experience).
- Related job titles suggested as you type and searched together with the typed title.
- Collects current jobs from the directory's RSS feeds and public APIs.
- Optional CV matching: multilingual embeddings plus a Qwen review of the closest jobs, with reasons and skill gaps.
- Per job: full description, real apply link, and a tailored ATS-friendly resume and optional cover
  letter (PDF, DOCX, TXT). Every fact is checked against the CV; anything unsupported is removed and listed.
- Every AI cost is billed to the searching person's own Hugging Face token (ZeroGPU quota or
  Inference credits); without a token the app falls back to free, non-AI behaviour.
