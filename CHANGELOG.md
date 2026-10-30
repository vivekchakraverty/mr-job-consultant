# Changelog

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
