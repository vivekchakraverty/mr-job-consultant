---
title: CV Job Matcher
emoji: 🧭
colorFrom: green
colorTo: yellow
sdk: gradio
sdk_version: 6.31.0
python_version: "3.12"
app_file: app.py
pinned: false
short_description: Scores job posts against a CV on the caller's ZeroGPU quota
---

Matches a CV against job posts: `intfloat/multilingual-e5-small` ranks every job, then
`Qwen/Qwen3-4B-Instruct-2507` reviews the closest ones and explains the fit.

Each request runs on the ZeroGPU quota of whoever calls it (their Hugging Face token), not the
Space owner's.
