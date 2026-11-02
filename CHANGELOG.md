# Changelog

## Unreleased

### Changed
- **Reskilling**: you now type the skill you want to learn, instead of picking from AI-suggested
  career moves. The learning plan, videos, viewed tracking and tests work exactly as before, and
  the plan still starts from your CV. Upskilling keeps its suggestions.

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
