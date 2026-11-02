// YouTube lookups and comment sentiment for the learning plans, run by app/videos.py.
//
// One long-lived process: one JSON request per line on stdin, one JSON reply per line on stdout.
//   {"id": 1, "op": "search", "query": "...", "limit": 10}      -> {"id": 1, "ok": true, "result": [video, ...]}
//   {"id": 2, "op": "details", "video": "<id>", "comments": 20} -> likes, comment count, comment sentiment
//
// youtubei.js talks to YouTube's own web API the way a browser on this connection would: one request
// per search, two per video, no login. Sentiment runs here on the CPU with an ONNX export of
// cardiffnlp/twitter-roberta-base-sentiment-latest, so it costs nothing and uses no quota.
import readline from "node:readline";
import { Innertube } from "youtubei.js";

const SENTIMENT_MODEL = "Xenova/twitter-roberta-base-sentiment-latest";

let ytReady = null;
const yt = () => (ytReady ??= Innertube.create({ retrieve_player: false, lang: "en" }).catch((e) => { ytReady = null; throw e; }));

// app/videos.py downloads the model once into MODEL_DIR; it is only ever loaded from there.
let classifierReady = null;
const classifier = () => (classifierReady ??= import("@huggingface/transformers").then(({ pipeline, env }) => {
  env.localModelPath = process.env.MODEL_DIR;
  env.allowRemoteModels = false;
  return pipeline("text-classification", SENTIMENT_MODEL, { dtype: "q8" });
}).catch((e) => { classifierReady = null; throw e; }));

async function sentiments(texts) {
  try {
    return await (await classifier())(texts, { top_k: 3 });
  } catch {
    return null;  // no model: the video is still ranked on likes, views and relevance
  }
}

const count = (text) => {
  // "486,923 views", "1.3M views", "748" -> a number
  const m = String(text || "").replace(/,/g, "").match(/([\d.]+)\s*([KMB])?/i);
  if (!m) return null;
  return Math.round(Number(m[1]) * ({ k: 1e3, m: 1e6, b: 1e9 }[(m[2] || "").toLowerCase()] || 1));
};

async function search({ query, limit = 10 }) {
  const res = await (await yt()).search(query, { type: "video" });
  return res.videos
    .filter((v) => v.type === "Video" && v.video_id && !v.is_live)
    .slice(0, limit)
    .map((v) => ({
      id: v.video_id,
      title: v.title?.text || "",
      channel: v.author?.name || "",
      duration: v.duration?.seconds || null,
      views: count(v.view_count?.text),
      published: v.published?.text || "",
      description: v.description_snippet?.text || "",
    }));
}

async function details({ video, comments = 20 }) {
  const client = await yt();
  const info = await client.getInfo(video);
  const out = { id: video, likes: info.basic_info.like_count ?? null, views: info.basic_info.view_count ?? null,
                comment_count: null, comments: 0, sentiment: null, top_comment: "" };
  let thread;
  try {
    thread = await client.getComments(video, "TOP_COMMENTS");
  } catch {
    return out;  // comments turned off
  }
  out.comment_count = count(thread.header?.comments_count?.text);
  const items = thread.contents.map((c) => c.comment).filter((c) => c?.content?.text)
    .slice(0, comments).map((c) => ({ text: c.content.text.slice(0, 280), likes: count(c.like_count) || 0 }));
  const scores = items.length ? await sentiments(items.map((c) => c.text)) : null;
  if (!scores) return out;
  // Comments other viewers liked weigh more: they speak for more people.
  const total = { positive: 0, neutral: 0, negative: 0 };
  let weight = 0, best = -1;
  items.forEach((c, i) => {
    const w = 1 + Math.log10(1 + c.likes);
    const s = Object.fromEntries(scores[i].map((x) => [x.label.toLowerCase(), x.score]));
    for (const k of Object.keys(total)) total[k] += w * (s[k] || 0);
    weight += w;
    if ((s.positive || 0) > 0.8 && c.likes > best && c.text.length > 25) {
      best = c.likes;
      out.top_comment = c.text.slice(0, 160);
    }
  });
  for (const k of Object.keys(total)) total[k] = Math.round((total[k] / weight) * 1000) / 1000;
  out.comments = items.length;
  out.sentiment = total;
  return out;
}

const OPS = { search, details };
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
