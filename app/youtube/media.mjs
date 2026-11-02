// Speech-to-text and body-language vision for interview practice, run by app/interview.py.
//
// Same protocol as worker.mjs: one JSON request per line on stdin, one JSON reply per line on stdout.
//   {"id": 1, "op": "transcribe", "wav": "<path>"}      -> {text, chunks: [{start, end, text}], duration,
//                                                           speech_seconds, pauses: [[start, end], ...]}
//   {"id": 2, "op": "frames", "paths": ["<jpg>", ...]}  -> [{width, height, keypoints: {name: [x, y, score]},
//                                                            expression: [{label, score}] | null}]
//
// Everything runs on this computer's CPU with ONNX exports of Hugging Face models, so it's free and
// nothing leaves the machine. app/interview.py downloads the models once into MODEL_DIR; they are
// only ever loaded from there.
import { readFile } from "node:fs/promises";
import readline from "node:readline";

const ASR_MODEL = "onnx-community/whisper-base";
const POSE_MODEL = "onnx-community/vitpose-base-simple";
const FACE_MODEL = "Xenova/facial_emotions_image_detection";
const KEYPOINTS = ["nose", "left_eye", "right_eye", "left_ear", "right_ear", "left_shoulder", "right_shoulder",
  "left_elbow", "right_elbow", "left_wrist", "right_wrist", "left_hip", "right_hip", "left_knee", "right_knee",
  "left_ankle", "right_ankle"];

let libReady = null;
const lib = () => (libReady ??= import("@huggingface/transformers").then((t) => {
  t.env.localModelPath = process.env.MODEL_DIR;
  t.env.allowRemoteModels = false;
  return t;
}));
const once = (load) => { let p = null; return () => (p ??= load().catch((e) => { p = null; throw e; })); };

const asr = once(async () => (await lib()).pipeline("automatic-speech-recognition", ASR_MODEL, { dtype: "q8" }));
const pose = once(async () => {
  const t = await lib();
  return { model: await t.AutoModel.from_pretrained(POSE_MODEL, { dtype: "q8" }),
           processor: await t.AutoImageProcessor.from_pretrained(POSE_MODEL) };
});
const face = once(async () => (await lib()).pipeline("image-classification", FACE_MODEL, { dtype: "q8" }));

// ---------- audio ----------

/** 16-bit PCM WAV (what the page sends: mono, 16 kHz) as Float32 samples. */
function readWav(buf) {
  const dv = new DataView(buf.buffer, buf.byteOffset, buf.byteLength);
  let pos = 12, fmt = null, data = null;
  while (pos + 8 <= dv.byteLength) {
    const id = String.fromCharCode(...buf.subarray(pos, pos + 4));
    const size = dv.getUint32(pos + 4, true);
    if (id === "fmt ") fmt = { channels: dv.getUint16(pos + 10, true), rate: dv.getUint32(pos + 12, true), bits: dv.getUint16(pos + 22, true) };
    else if (id === "data") { data = [pos + 8, Math.min(size, dv.byteLength - pos - 8)]; break; }
    pos += 8 + size + (size % 2);
  }
  if (!fmt || !data || fmt.bits !== 16) throw new Error("expected a 16-bit PCM WAV file");
  const n = Math.floor(data[1] / 2 / fmt.channels);
  const samples = new Float32Array(n);
  for (let i = 0; i < n; i++) samples[i] = dv.getInt16(data[0] + i * 2 * fmt.channels, true) / 32768;
  return { samples, rate: fmt.rate };
}

function resample(samples, from, to) {
  const out = new Float32Array(Math.floor(samples.length * to / from));
  for (let i = 0; i < out.length; i++) {
    const x = i * from / to, j = Math.floor(x), f = x - j;
    out[i] = samples[j] * (1 - f) + (samples[j + 1] ?? samples[j]) * f;
  }
  return out;
}

/** Silences of 1.2 s or more, from the loudness of 50 ms windows (more reliable than caption timing). */
function pausesIn(samples, rate) {
  const win = Math.round(rate * 0.05);
  const rms = [];
  for (let i = 0; i + win <= samples.length; i += win) {
    let s = 0;
    for (let j = i; j < i + win; j++) s += samples[j] * samples[j];
    rms.push(Math.sqrt(s / win));
  }
  const sorted = [...rms].sort((a, b) => a - b);
  const floor = sorted[Math.floor(sorted.length * 0.1)] || 0, loud = sorted[Math.floor(sorted.length * 0.9)] || 0;
  const threshold = floor + (loud - floor) * 0.15;  // relative, so it works with any microphone level
  const pauses = [];
  let start = null, speech = 0;
  rms.forEach((v, i) => {
    if (v < threshold) { start ??= i; return; }
    speech += 1;
    if (start !== null && (i - start) * 0.05 >= 1.2) pauses.push([+(start * 0.05).toFixed(2), +(i * 0.05).toFixed(2)]);
    start = null;
  });
  // silence before the first word and after the last isn't a pause
  const first = rms.findIndex((v) => v >= threshold) * 0.05;
  return { pauses: pauses.filter(([s]) => s > first), speech_seconds: +(speech * 0.05).toFixed(1) };
}

async function transcribe({ wav }) {
  const { samples, rate } = readWav(await readFile(wav));
  const audio = rate === 16000 ? samples : resample(samples, rate, 16000);
  const out = await (await asr())(audio, { return_timestamps: true, chunk_length_s: 30, stride_length_s: 5 });
  return {
    text: (out.text || "").trim(),
    chunks: (out.chunks || []).map((c) => ({ start: c.timestamp[0], end: c.timestamp[1], text: c.text.trim() })),
    duration: +(audio.length / 16000).toFixed(1),
    ...pausesIn(audio, 16000),
  };
}

// ---------- frames ----------

/** A box around the face from the eyes, nose and ears, or null when the face isn't clearly visible. */
function faceBox(kp, width, height) {
  const [nose, le, re] = [kp.nose, kp.left_eye, kp.right_eye];
  if (![nose, le, re].every((p) => p[2] >= 0.3)) return null;
  const eyes = Math.hypot(le[0] - re[0], le[1] - re[1]);
  const half = Math.max(eyes * 1.6, 24);
  const x0 = Math.max(0, Math.round(nose[0] - half)), y0 = Math.max(0, Math.round(nose[1] - half * 1.15));
  const x1 = Math.min(width - 1, Math.round(nose[0] + half)), y1 = Math.min(height - 1, Math.round(nose[1] + half * 0.95));
  return x1 - x0 > 20 && y1 - y0 > 20 ? [x0, y0, x1, y1] : null;
}

async function frames({ paths }) {
  const t = await lib();
  const { model, processor } = await pose();
  const classify = await face().catch(() => null);  // expressions are a bonus; posture still works without them
  const out = [];
  for (const path of paths) {
    const image = await t.RawImage.read(path);
    const { heatmaps } = await model(await processor(image));
    const [person] = processor.post_process_pose_estimation(heatmaps, [[[0, 0, image.width, image.height]]])[0];
    const keypoints = {};
    person.keypoints.forEach(([x, y], i) => {
      keypoints[KEYPOINTS[person.labels[i]]] = [Math.round(x), Math.round(y), +person.scores[i].toFixed(3)];
    });
    let expression = null;
    const box = faceBox(keypoints, image.width, image.height);
    if (classify && box) {
      const found = await classify(await image.crop(box), { top_k: 7 });
      expression = found.map((e) => ({ label: e.label.toLowerCase(), score: +e.score.toFixed(3) }));
    }
    out.push({ width: image.width, height: image.height, keypoints, expression });
  }
  return out;
}

// ---------- protocol ----------

const OPS = { transcribe, frames, ping: async () => "pong" };
const send = (msg) => process.stdout.write(JSON.stringify(msg) + "\n");

readline.createInterface({ input: process.stdin }).on("line", async (line) => {
  let req;
  try {
    req = JSON.parse(line);
  } catch {
    return;
  }
  try {
    if (!OPS[req.op]) throw new Error(`unknown op ${req.op}`);
    send({ id: req.id, ok: true, result: await OPS[req.op](req) });
  } catch (e) {
    send({ id: req.id, ok: false, error: String(e?.message || e).slice(0, 300) });
  }
});
