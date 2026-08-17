/**
 * Optional. Receives submissions from site/submit.html and opens a GitHub issue
 * so you have a moderation queue instead of an inbox.
 *
 * Deploy on Cloudflare Workers (free tier is far more than enough):
 *   1. dash.cloudflare.com -> Workers & Pages -> Create -> paste this
 *   2. Settings -> Variables:
 *        GH_TOKEN  (secret)  fine-grained PAT, Issues: read+write, that repo only
 *        GH_REPO   (plain)   altarcycles/altar-events
 *        ALLOWED   (plain)   https://altar.bike
 *   3. Copy the worker URL into ENDPOINT at the top of submit.html
 *
 * Without this the form falls back to a pre-filled email, which works fine —
 * it just means you're transcribing by hand.
 */

const FIELDS = [
  ["title", "Event name", true],
  ["date", "Date", true],
  ["end", "End date", false],
  ["time", "Start time", false],
  ["category", "Category", false],
  ["venue", "Location", false],
  ["city", "City", true],
  ["state", "State", false],
  ["link", "Link", true],
  ["description", "Details", false],
];

export default {
  async fetch(request, env) {
    const origin = env.ALLOWED || "*";
    const cors = {
      "Access-Control-Allow-Origin": origin,
      "Access-Control-Allow-Methods": "POST, OPTIONS",
      "Access-Control-Allow-Headers": "Content-Type",
    };
    if (request.method === "OPTIONS") return new Response(null, { headers: cors });
    if (request.method !== "POST") return json({ error: "POST only" }, 405, cors);

    let body;
    try {
      body = await request.json();
    } catch {
      return json({ error: "Expected JSON" }, 400, cors);
    }

    // Normalise the form's field names onto the issue-template headings that
    // build.py's parse_issue_body() expects.
    const value = {
      title: body.title, date: body.date, end: body.end, time: body.time,
      category: body.category, venue: body.venue, city: body.city,
      state: body.state, link: body.url, description: body.description,
    };

    for (const [key, label, required] of FIELDS) {
      if (required && !String(value[key] || "").trim()) {
        return json({ error: `Missing ${label}` }, 400, cors);
      }
    }
    if (!/^\d{4}-\d{2}-\d{2}$/.test(value.date)) {
      return json({ error: "Date must be YYYY-MM-DD" }, 400, cors);
    }
    if (String(value.title).length > 200 || String(value.description || "").length > 2000) {
      return json({ error: "Too long" }, 400, cors);
    }

    const lines = FIELDS
      .filter(([k]) => String(value[k] || "").trim())
      .map(([k, label]) => `### ${label}\n\n${String(value[k]).trim()}`);
    lines.push(`### Submitted by\n\n${clean(body.submitter)} <${clean(body.email)}>`);

    const res = await fetch(`https://api.github.com/repos/${env.GH_REPO}/issues`, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${env.GH_TOKEN}`,
        Accept: "application/vnd.github+json",
        "User-Agent": "altar-events-submit",
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        title: `[Event] ${String(value.title).slice(0, 120)} — ${value.date}`,
        body: lines.join("\n\n"),
        labels: ["event-submission"],   // you add `approved` to publish it
      }),
    });

    if (!res.ok) {
      return json({ error: "Could not file it" }, 502, cors);
    }
    const issue = await res.json();
    return json({ ok: true, number: issue.number }, 200, cors);
  },
};

const clean = (s) => String(s || "").replace(/[<>\r\n]/g, "").slice(0, 120);
const json = (obj, status, headers) =>
  new Response(JSON.stringify(obj), {
    status,
    headers: { ...headers, "Content-Type": "application/json" },
  });
