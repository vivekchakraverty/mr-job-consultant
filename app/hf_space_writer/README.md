---
title: Application Writer
emoji: ✍️
colorFrom: green
colorTo: yellow
sdk: gradio
sdk_version: 6.31.0
python_version: "3.12"
app_file: app.py
pinned: false
short_description: Grounded resume and cover-letter drafting
---

Drafts tailored resumes and cover letters with `Qwen/Qwen3.6-35B-A3B-FP8` for the job-search
app, which checks every fact against the user's own CV before using it.

Each request runs on the ZeroGPU quota of whoever calls it (their Hugging Face token), not the
Space owner's.
