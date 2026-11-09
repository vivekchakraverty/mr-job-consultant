// MediaPipe detects landmarks; these conservative observations are heuristics, not model judgments.
export async function analyzePose(blob, duration, progress) {
  const { FilesetResolver, PoseLandmarker } = await import("/api/interview/vision/vision_bundle.mjs");
  const fileset = await FilesetResolver.forVisionTasks("/api/interview/vision/wasm");
  const landmarker = await PoseLandmarker.createFromOptions(fileset, {
    baseOptions: { modelAssetPath: "/static/models/pose_landmarker_lite.task", delegate: "CPU" },
    runningMode: "VIDEO", numPoses: 1, minPoseDetectionConfidence: 0.6, minTrackingConfidence: 0.6,
  });
  const video = document.createElement("video");
  const url = URL.createObjectURL(blob);
  video.preload = "auto"; video.muted = true; video.playsInline = true; video.src = url;
  const event = (name) => new Promise((resolve, reject) => {
    const timer = setTimeout(() => { cleanup(); reject(new Error("The browser could not read the video for analysis.")); }, 10000);
    const done = () => { cleanup(); resolve(); };
    const fail = () => { cleanup(); reject(new Error("The browser could not read the recorded video.")); };
    const cleanup = () => { clearTimeout(timer); video.removeEventListener(name, done); video.removeEventListener("error", fail); };
    video.addEventListener(name, done, { once: true }); video.addEventListener("error", fail, { once: true });
  });
  try {
    await event("loadeddata");
    // MediaRecorder WebM often omits duration metadata; seeking to the end lets browsers recover it.
    if (!Number.isFinite(video.duration)) {
      const seeking = event("seeked"); video.currentTime = 1e9; await seeking;
    }
    const end = Math.min(duration, Number.isFinite(video.duration) ? video.duration : duration);
    if (!Number.isFinite(end) || end < 3) throw new Error("Record at least 3 seconds to analyze movement.");
    const samples = [], total = Math.max(1, Math.floor(end));
    for (let i = 0; i < total; i++) {
      const t = Math.min(i + 0.05, Math.max(0, end - 0.1));
      const seeked = event("seeked"); video.currentTime = t; await seeked;
      const result = landmarker.detectForVideo(video, Math.round(t * 1000));
      const pose = result.landmarks?.[0];
      const good = (p) => p && p.visibility >= 0.65 && p.x >= 0 && p.x <= 1 && p.y >= 0 && p.y <= 1;
      if (pose && good(pose[11]) && good(pose[12])) {
        const left = pose[11], right = pose[12], width = Math.abs(left.x - right.x);
        if (width > 0.05) {
          const tilt = Math.atan2(Math.abs(left.y - right.y) * video.videoHeight, width * video.videoWidth) * 180 / Math.PI;
          const center = (left.x + right.x) / 2, shoulderY = (left.y + right.y) / 2;
          samples.push({ time: t, center, width, tilt,
            hands: [pose[15], pose[16]].map((p) => good(p) ? { x: (p.x - center) / width, y: (p.y - shoulderY) / width } : null) });
        }
      }
      if (i % 5 === 0) { progress(i + 1, total); await new Promise((resolve) => setTimeout(resolve, 0)); }
    }
    return poseObservations(samples, total);
  } finally { landmarker.close(); video.removeAttribute("src"); video.load(); URL.revokeObjectURL(url); }
}

export function poseObservations(samples, total) {
  if (samples.length < 3 || samples.length / total < 0.5) return { total, visible: samples.length, events: [] };
  const events = [];
  let lastShift = -20, lastTilt = -20, lastHands = -20, tiltRun = [];
  for (let i = 0; i < samples.length; i++) {
    const s = samples[i], prior = samples[i - 1];
    if (s.tilt >= 15 && (!prior || s.time - prior.time <= 1.5)) tiltRun.push(s); else tiltRun = s.tilt >= 15 ? [s] : [];
    if (tiltRun.length >= 3 && s.time - lastTilt >= 20) {
      events.push({ time: tiltRun[0].time, text: "Shoulder line appears angled for several seconds. Check the camera angle and your position in the replay." }); lastTilt = s.time;
    }
    // Allow one missed sample while tracking recovers; longer gaps are not comparable.
    if (!prior || s.time - prior.time > 2.5) continue;
    if (Math.abs(s.center - prior.center) / Math.max(s.width, prior.width) >= 0.45 && s.time - lastShift >= 15) {
      events.push({ time: prior.time, text: "Your upper body shifted in the camera frame. Review whether this movement fits your answer." }); lastShift = s.time;
    }
    const moved = s.hands.some((p, index) => p && prior.hands[index] &&
      Math.hypot(p.x - prior.hands[index].x, p.y - prior.hands[index].y) >= 0.65);
    if (moved && s.time - lastHands >= 15) {
      events.push({ time: prior.time, text: "A visible hand moved noticeably. Review the gesture alongside what you were saying." }); lastHands = s.time;
    }
  }
  return { total, visible: samples.length, events: events.sort((a, b) => a.time - b.time).slice(0, 12) };
}
