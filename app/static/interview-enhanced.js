"use strict";

(() => {
  const el = (id) => document.getElementById(`practice-${id}`);
  const practice = { recorder: null, audioRecorder: null, stream: null, chunks: [], blob: null, audioBlob: null, videoMode: false, visionResult: null, url: null, start: 0,
    duration: null, timer: null, words: [], transcript: "", reviewed: null, previous: null, busy: false };
  const examples = { behavioral: "Tell me about a time you overcame a challenge at work.",
    general: "Tell me about yourself.", technical: "How would you approach solving a difficult technical problem?" };
  const clock = (s) => `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
  const error = (message = "") => { el("error").textContent = message; };
  const status = (message = "") => { el("status").textContent = message; };
  const current = () => ({ question: el("question").value.trim(), kind: el("kind").value,
    answer: el("answer").value.trim(), duration: practice.duration });
  const same = (a, b) => a && b && a.question === b.question && a.kind === b.kind && a.answer === b.answer && a.duration === b.duration;
  const valid = (data) => {
    if (!data.question || !data.answer) { error("Enter a question and an answer first."); return false; }
    return true;
  };
  const setBusy = (busy) => {
    practice.busy = busy;
    ["record", "stop", "transcribe", "review", "retry", "clear", "coach", "analyze"].forEach((id) => {
      el(id).disabled = busy || (id === "stop" ? !practice.recorder : !!practice.recorder);
    });
    ["question", "kind", "answer", "min", "max", "mode"].forEach((id) => { el(id).disabled = busy || (id === "mode" && !!practice.recorder); });
  };
  function discardRecording() {
    if (practice.url) URL.revokeObjectURL(practice.url);
    practice.url = null; practice.blob = null; practice.audioBlob = null; practice.videoMode = false; practice.visionResult = null; practice.duration = null; practice.words = []; practice.transcript = "";
    el("audio").removeAttribute("src"); el("audio").hidden = true; el("transcription").hidden = true; el("clock").textContent = "00:00";
    el("video").pause(); el("video").srcObject = null; el("video").removeAttribute("src"); el("video").hidden = true;
    el("vision").hidden = true; el("vision-results").replaceChildren();
  }
  function invalidateReview() { el("report").hidden = true; el("feedback").replaceChildren(); }
  async function api(url, options) {
    const response = await fetch(url, options);
    let data = {}; try { data = await response.json(); } catch { /* display a general error */ }
    if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Could not complete the request. Please try again.");
    return data;
  }
  function paragraph(parent, label, text) {
    if (!text) return;
    const p = document.createElement("p"), strong = document.createElement("strong");
    strong.textContent = `${label}: `; p.append(strong, document.createTextNode(text)); parent.append(p);
  }
  function card(value, label) {
    const div = document.createElement("div"), strong = document.createElement("strong"), span = document.createElement("span");
    div.className = "interview-metric"; strong.textContent = value; span.textContent = label; div.append(strong, span); return div;
  }
  function renderVision(result) {
    const target = el("vision-results"); target.replaceChildren();
    if (result.visible < 3 || result.visible / result.total < 0.5) {
      paragraph(target, "Not enough visibility", "The model could not track both shoulders in enough frames. Try brighter lighting and include your head and upper body in the frame.");
      return;
    }
    paragraph(target, "Tracking coverage", `${Math.round(result.visible / result.total * 100)}% of sampled frames. Observations are approximate and do not assess confidence or intent.`);
    if (!result.events.length) { paragraph(target, "Review", "No large movements were detected in the sampled frames. This is not a body-language score."); return; }
    const list = document.createElement("ul"); list.className = "interview-events";
    for (const item of result.events) {
      const li = document.createElement("li"), button = document.createElement("button");
      button.type = "button"; button.className = "link-btn"; button.textContent = clock(item.time);
      button.addEventListener("click", () => { el("video").currentTime = item.time; el("video").play().catch(() => {}); el("video").scrollIntoView({ block: "nearest" }); });
      const nearby = practice.words.filter((w) => w.start <= item.time + 3 && w.end >= Math.max(0, item.time - 2)).map((w) => w.word).join(" ");
      li.append(button, document.createTextNode(item.text + (nearby && el("answer").value.trim() === practice.transcript.trim() ? ` Near: “${nearby}”` : ""))); list.append(li);
    }
    target.append(list);
  }
  el("kind").addEventListener("change", () => {
    if (Object.values(examples).includes(el("question").value.trim())) el("question").value = examples[el("kind").value];
    invalidateReview();
  });
  ["question", "answer", "min", "max"].forEach((id) => el(id).addEventListener("input", invalidateReview));
  el("answer").addEventListener("input", () => { if (practice.visionResult) renderVision(practice.visionResult); });
  el("record").addEventListener("click", async () => {
    error(); status();
    if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) { error("Recording is not supported here. You can type your answer instead."); return; }
    try {
      setBusy(true);
      // The five-question workflow has its own camera preview. Do not leave two cameras active.
      if (typeof iv !== "undefined" && iv.recording) {
        setBusy(false); error("Finish the current five-question recording first."); return;
      }
      if (typeof stopCamera === "function") stopCamera();
      const videoMode = el("mode").value === "video";
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true, video: videoMode ? { width: { ideal: 640 }, height: { ideal: 480 } } : false });
      let recorder, audioRecorder;
      try {
        recorder = new MediaRecorder(stream, videoMode ? { videoBitsPerSecond: 700000 } : {});
        if (videoMode) audioRecorder = new MediaRecorder(new MediaStream(stream.getAudioTracks()));
      } catch (e) { stream.getTracks().forEach((track) => track.stop()); throw e; }
      discardRecording(); invalidateReview();
      practice.stream = stream; practice.chunks = []; practice.recorder = recorder; practice.audioRecorder = audioRecorder || null; practice.videoMode = videoMode;
      const audioChunks = []; let videoDone = false, audioDone = !audioRecorder, failed = false;
      if (videoMode) { el("video").srcObject = stream; el("video").muted = true; el("video").controls = false; el("video").hidden = false; el("video").play().catch(() => {}); }
      recorder.ondataavailable = (event) => { if (event.data.size) practice.chunks.push(event.data); };
      if (audioRecorder) audioRecorder.ondataavailable = (event) => { if (event.data.size) audioChunks.push(event.data); };
      const finish = () => {
        if (!videoDone || !audioDone) return;
        clearInterval(practice.timer);
        stream.getTracks().forEach((track) => track.stop());
        practice.duration = Math.max(1, Math.min(1800, (performance.now() - practice.start) / 1000));
        practice.blob = new Blob(practice.chunks, { type: recorder.mimeType || (videoMode ? "video/webm" : "audio/webm") });
        practice.audioBlob = audioRecorder ? new Blob(audioChunks, { type: audioRecorder.mimeType || "audio/webm" }) : practice.blob;
        practice.chunks = []; practice.recorder = null; practice.audioRecorder = null; practice.stream = null;
        if (!failed && practice.blob.size && practice.audioBlob.size) {
          practice.url = URL.createObjectURL(practice.blob);
          const player = el(videoMode ? "video" : "audio"); player.srcObject = null; player.src = practice.url;
          player.muted = false; player.controls = true; player.hidden = false; el("transcription").hidden = false;
          el("vision").hidden = !videoMode;
          status("Recording ready. Transcribe it or enter your answer below.");
        } else { discardRecording(); error("No audio was captured. Please try again."); }
        setBusy(false);
      };
      recorder.onstop = () => { videoDone = true; finish(); };
      if (audioRecorder) audioRecorder.onstop = () => { audioDone = true; finish(); };
      const stop = () => { if (recorder.state !== "inactive") recorder.stop(); if (audioRecorder?.state !== "inactive") audioRecorder?.stop(); };
      recorder.onerror = () => { failed = true; stop(); };
      if (audioRecorder) audioRecorder.onerror = () => { failed = true; stop(); };
      recorder.start(); practice.start = performance.now();
      if (audioRecorder) audioRecorder.start();
      practice.timer = setInterval(() => {
        const seconds = (performance.now() - practice.start) / 1000; el("clock").textContent = clock(seconds);
        if (seconds >= 300 && recorder.state === "recording") { setBusy(true); stop(); }
      }, 250);
      status("Recording… stop when you are ready. Maximum 5 minutes."); setBusy(false);
    } catch (e) {
      if (practice.stream) practice.stream.getTracks().forEach((track) => track.stop());
      practice.stream = null; practice.recorder = null; setBusy(false); error(`Could not start the microphone: ${e.message}`);
    }
  });
  el("stop").addEventListener("click", () => { if (practice.recorder?.state === "recording") { setBusy(true); practice.recorder.stop(); if (practice.audioRecorder?.state === "recording") practice.audioRecorder.stop(); } });
  function stopActiveRecording() { if (practice.recorder?.state === "recording") el("stop").click(); }
  document.querySelectorAll("[role=tab]").forEach((tab) => tab.addEventListener("click", () => {
    if (tab.dataset.tab !== "interview") stopActiveRecording();
  }));
  document.querySelector(".practice-extra").addEventListener("toggle", (event) => {
    if (!event.currentTarget.open) stopActiveRecording();
  });
  document.getElementById("iv-camera").addEventListener("click", stopActiveRecording);
  el("transcribe").addEventListener("click", async () => {
    if (!practice.blob || practice.busy) return;
    if (el("answer").value.trim() && !window.confirm("Replace the current answer with the transcript?")) return;
    error(); setBusy(true); status("Transcribing locally. The first model download can take several minutes…");
    const form = new FormData(); form.append("file", practice.audioBlob, "answer.webm");
    try {
      const data = await api("/api/interview/transcribe", { method: "POST", body: form });
      el("answer").value = data.text; practice.transcript = data.text; practice.words = data.words || []; invalidateReview();
      if (practice.visionResult) renderVision(practice.visionResult);
      status(data.text ? "Transcript ready. Check and edit it before reviewing." : "No speech was detected. Try again or type your answer.");
    } catch (e) { error(e.message); status(); } finally { setBusy(false); }
  });
  el("analyze").addEventListener("click", async () => {
    if (!practice.blob || practice.busy || !practice.videoMode) return;
    error(); setBusy(true); status("Loading the local pose model…");
    try {
      const { analyzePose } = await import("/static/interview-vision.js");
      practice.visionResult = await analyzePose(practice.blob, practice.duration, (done, total) => status(`Analyzing video: ${done} / ${total} seconds…`));
      renderVision(practice.visionResult);
      status("Video analysis ready. Select a timestamp to review the clip.");
    } catch (e) { error(`Could not analyze the video: ${e.message}`); status(); } finally { setBusy(false); }
  });
  el("review").addEventListener("click", async () => {
    const data = current(); error(); if (!valid(data)) return;
    const min = Number(el("min").value), max = Number(el("max").value);
    if (!el("min").value || !el("max").value || min < 1 || max > 1800 || min > max) { error("Enter a valid length guide between 1 and 1800 seconds."); return; }
    setBusy(true); status("Preparing your review…");
    try {
      const m = await api("/api/interview/metrics", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) });
      if (!same(data, current())) { status("Your answer changed. Review it again."); return; }
      practice.reviewed = { data, metrics: m };
      const metrics = el("metrics"); metrics.replaceChildren(); metrics.className = "interview-metrics";
      metrics.append(card(m.words, "words"), card(m.duration ? clock(m.duration) : "—", "recorded duration"),
        card(m.overall_wpm ?? "—", "words / minute overall"), card(m.filler_candidates, "possible filler words"));
      const length = el("length"); length.replaceChildren();
      if (m.duration) paragraph(length, "Length guide", `${m.duration < min ? "Shorter than" : m.duration > max ? "Longer than" : "Within"} your ${min}–${max} second guide.`);
      const fillers = el("fillers"); fillers.replaceChildren();
      if (m.fillers.length) paragraph(fillers, "Possible fillers", `${m.fillers.join(", ")}${m.filler_candidates > m.fillers.length ? " …" : ""}`);
      const comparison = el("comparison"); comparison.replaceChildren();
      if (practice.previous && practice.previous.data.question === data.question && practice.previous.data.kind === data.kind) {
        const prev = practice.previous.metrics;
        paragraph(comparison, "Previous attempt", `${prev.words} words${prev.duration ? `, ${clock(prev.duration)} long` : ""}, ${prev.filler_candidates} possible fillers.`);
      }
      const pauses = el("pauses"); pauses.replaceChildren();
      if (practice.words.length && data.answer === practice.transcript.trim()) {
        const gaps = practice.words.slice(1).map((w, i) => ({ start: practice.words[i].end, length: w.start - practice.words[i].end }))
          .filter((gap) => gap.length >= 2).slice(0, 10);
        paragraph(pauses, "Estimated pauses of 2 seconds or more", gaps.length ? gaps.map((g) => `${clock(g.start)} (${g.length.toFixed(1)}s)`).join(", ") : "None detected between transcribed words.");
      } else if (practice.words.length) paragraph(pauses, "Pauses", "Not shown because the transcript was edited; word timings may no longer match.");
      el("feedback").replaceChildren(); el("report").hidden = false; status("Review ready.");
    } catch (e) { error(e.message); status(); } finally { setBusy(false); }
  });
  el("coach").addEventListener("click", async () => {
    const data = current(); error(); if (!valid(data) || !same(data, practice.reviewed?.data)) { error("Review your current answer first."); return; }
    setBusy(true); status("Asking your writer Space for feedback…");
    try {
      const result = await api("/api/interview/coach", { method: "POST", headers: { "Content-Type": "application/json", ...hfHeaders() }, body: JSON.stringify(data) });
      if (!same(data, current())) { status("Your answer changed. Review it again."); return; }
      const target = el("feedback"); target.replaceChildren(); const f = result.feedback;
      paragraph(target, "What worked", f.strength); paragraph(target, "Structure", f.structure); paragraph(target, "Opening", f.opening); paragraph(target, "To improve", f.improvement);
      paragraph(target, "Try next", f.next_step); paragraph(target, "From your answer", f.evidence && `“${f.evidence}”`);
      status("Coaching ready.");
    } catch (e) { error(e.message); status(); } finally { setBusy(false); }
  });
  el("retry").addEventListener("click", () => {
    if (practice.recorder || practice.busy) return;
    if (practice.reviewed && same(practice.reviewed.data, current())) practice.previous = practice.reviewed;
    practice.reviewed = null; discardRecording(); el("answer").value = ""; invalidateReview(); error(); status("Ready for another attempt."); el("answer").focus();
  });
  el("clear").addEventListener("click", () => {
    if (practice.recorder || practice.busy) return;
    practice.previous = null; practice.reviewed = null; discardRecording(); el("answer").value = ""; invalidateReview(); error(); status("Practice cleared.");
  });
  window.addEventListener("pagehide", () => { if (practice.stream) practice.stream.getTracks().forEach((track) => track.stop()); });
})();
