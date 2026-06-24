"""Render the structured report dict into a polished, self-contained HTML page.

Dark theme by default with a light-mode toggle. Includes a clear scoring legend,
per-question cards with labelled score bars, and full-sentence model answers.
"""
from __future__ import annotations

import html


def _esc(x) -> str:
    return html.escape(str(x))


def _norm_label(v) -> str:
    if isinstance(v, str):
        return v
    if isinstance(v, (list, tuple)):
        return ", ".join(str(x) for x in v)
    if isinstance(v, dict):
        return ", ".join(f"{k}: {x}" for k, x in v.items())
    return str(v)


def _level_class(label: str) -> str:
    return {"Strong": "lv-strong", "Developing": "lv-dev", "Weak": "lv-weak"}.get(str(label), "lv-dev")


def _score_class(v) -> str:
    try:
        v = float(v)
    except (TypeError, ValueError):
        return "sc-mid"
    return "sc-good" if v >= 8 else ("sc-mid" if v >= 5 else "sc-low")


def _bar(label: str, value, maximum: int = 10) -> str:
    try:
        pct = max(0, min(100, round(float(value) / maximum * 100)))
    except (TypeError, ValueError):
        pct, value = 0, "–"
    return (f'<div class="metric"><div class="metric-top"><span>{_esc(label)}</span>'
            f'<b>{_esc(value)}/{maximum}</b></div>'
            f'<div class="track"><div class="fill {_score_class(value)}" style="width:{pct}%"></div></div></div>')


def render_report_html(report: dict, for_mentor: bool = False) -> str:
    name = _esc(report.get("student_name", "Student"))
    overall = report.get("overall_score", 0)
    readiness = int(report.get("readiness_score", 0))

    concept_chips = "".join(
        f'<span class="chip {_level_class(_norm_label(lbl))}">{_esc(t)}: {_esc(_norm_label(lbl))}</span>'
        for t, lbl in (report.get("concept_map") or {}).items()
    ) or '<span class="muted">No topic data.</span>'

    qcards = ""
    for q in report.get("per_question", []):
        sc = q.get("scores", {})
        badge = _esc(q.get("correctness_label") or "—")
        badge_cls = ("b-good" if "Correct" in badge and "Incorrect" not in badge and "Partially" not in badge
                     else "b-mid" if "Partially" in badge or "Accurate" in badge else "b-low")
        qtext = f'<div class="qtext">❓ {_esc(q.get("question"))}</div>' if q.get("question") else ""
        ideal = (f'<div class="ideal"><span class="ideal-h">💡 Ideal answer</span>'
                 f'<p>{_esc(q.get("ideal_answer"))}</p></div>') if q.get("ideal_answer") else ""
        improve = (f'<div class="improve"><b>📈 To improve:</b> {_esc(q.get("what_to_improve"))}</div>'
                   if q.get("what_to_improve") else "")
        qcards += f"""
        <div class="qcard">
          <div class="qhead"><span class="qnum">Q{_esc(q.get('question_number'))}</span>
            <span class="qtopic">{_esc(q.get('topic'))}</span>
            <span class="badge {badge_cls}">{badge}</span></div>
          {qtext}
          <div class="metrics">
            {_bar('Correctness', sc.get('correctness'))}
            {_bar('Concept clarity', sc.get('concept_clarity'))}
            {_bar('Explanation', sc.get('explanation'))}
            {_bar('Basics', sc.get('basics'))}
          </div>
          {improve}{ideal}
        </div>"""

    improvements = "".join(f"<li>{_esc(i)}</li>" for i in report.get("top_improvements", []))
    revision = "".join(f'<span class="chip lv-dev">{_esc(t)}</span>' for t in report.get("revision_topics", []))
    mentor_notes = ""
    if report.get("mentor_notes"):
        mentor_notes = f'<div class="card note"><b>Mentor\'s note:</b> {_esc(report["mentor_notes"])}</div>'

    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Interview Report — {name}</title>
<style>
  :root{{ --bg:#0e1014; --bg2:#161a21; --card:#1b2027; --line:#2a313b; --text:#e9edf3;
          --muted:#9aa3b2; --accent:#5b8cff; --good:#34d399; --mid:#fbbf24; --low:#f87171; }}
  html.light{{ --bg:#f4f6fb; --bg2:#fff; --card:#fff; --line:#e2e7f0; --text:#1a2230;
          --muted:#5b6678; --accent:#2563eb; --good:#16a34a; --mid:#b45309; --low:#dc2626; }}
  *{{box-sizing:border-box}} body{{margin:0;background:var(--bg);color:var(--text);
     font-family:Inter,Segoe UI,Arial,sans-serif;line-height:1.55}}
  .wrap{{max-width:840px;margin:0 auto;padding:28px 20px 60px}}
  .toolbar{{position:fixed;top:14px;right:14px;display:flex;gap:8px;z-index:10}}
  .toolbar button{{font:14px Inter,Segoe UI,Arial;border:1px solid var(--line);border-radius:10px;
     padding:9px 13px;cursor:pointer;background:var(--card);color:var(--text)}}
  .toolbar button.primary{{background:var(--accent);color:#fff;border-color:transparent}}
  @media print{{.toolbar{{display:none}} body{{background:#fff;color:#000}}}}
  h1{{margin:0 0 2px;font-size:26px}} .sub{{color:var(--muted);margin:0 0 22px}}
  .stats{{display:flex;gap:14px;margin-bottom:22px;flex-wrap:wrap}}
  .stat{{flex:1;min-width:150px;background:linear-gradient(160deg,var(--card),var(--bg2));
     border:1px solid var(--line);border-radius:16px;padding:18px;text-align:center}}
  .stat .big{{font-size:34px;font-weight:800}} .stat .lbl{{color:var(--muted);font-size:13px;margin-top:4px}}
  .card{{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:18px 20px;margin-bottom:16px}}
  h2{{font-size:15px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);margin:26px 0 12px}}
  .chips{{display:flex;flex-wrap:wrap;gap:8px}}
  .chip{{padding:6px 12px;border-radius:999px;font-size:13px;font-weight:600;border:1px solid var(--line)}}
  .lv-strong{{color:var(--good);border-color:color-mix(in srgb,var(--good) 45%,transparent)}}
  .lv-dev{{color:var(--mid);border-color:color-mix(in srgb,var(--mid) 45%,transparent)}}
  .lv-weak{{color:var(--low);border-color:color-mix(in srgb,var(--low) 45%,transparent)}}
  .legend{{font-size:12.5px;color:var(--muted);margin:-6px 0 14px}}
  .qcard{{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:18px 20px;margin-bottom:14px}}
  .qhead{{display:flex;align-items:center;gap:10px;margin-bottom:8px}}
  .qnum{{font-weight:800;color:var(--accent)}} .qtopic{{font-weight:600}}
  .badge{{margin-left:auto;font-size:12px;font-weight:700;padding:4px 10px;border-radius:999px}}
  .b-good{{background:color-mix(in srgb,var(--good) 22%,transparent);color:var(--good)}}
  .b-mid{{background:color-mix(in srgb,var(--mid) 22%,transparent);color:var(--mid)}}
  .b-low{{background:color-mix(in srgb,var(--low) 22%,transparent);color:var(--low)}}
  .qtext{{color:var(--muted);font-size:14px;margin-bottom:12px}}
  .metrics{{display:grid;grid-template-columns:1fr 1fr;gap:10px 18px;margin-bottom:8px}}
  .metric-top{{display:flex;justify-content:space-between;font-size:13px;margin-bottom:4px}}
  .track{{height:7px;background:var(--bg2);border:1px solid var(--line);border-radius:99px;overflow:hidden}}
  .fill{{height:100%;border-radius:99px}}
  .sc-good{{background:var(--good)}} .sc-mid{{background:var(--mid)}} .sc-low{{background:var(--low)}}
  .improve{{font-size:14px;margin-top:12px}}
  .ideal{{margin-top:12px;background:color-mix(in srgb,var(--accent) 12%,transparent);
     border:1px solid color-mix(in srgb,var(--accent) 35%,transparent);border-radius:12px;padding:10px 14px}}
  .ideal-h{{font-weight:700;color:var(--accent);font-size:13px}} .ideal p{{margin:6px 0 0;font-size:14px}}
  ul{{margin:0;padding-left:20px}} li{{margin:5px 0}}
  .muted{{color:var(--muted)}} .note{{border-color:color-mix(in srgb,var(--accent) 40%,transparent)}}
  .enc{{background:color-mix(in srgb,var(--good) 14%,transparent);
     border:1px solid color-mix(in srgb,var(--good) 35%,transparent);border-radius:14px;padding:16px;margin-top:8px}}
</style></head>
<body>
<div class="toolbar">
  <button onclick="tg()" id="themeBtn">☀️ Light</button>
  <button onclick="window.print()">🖨 PDF</button>
  <button class="primary" onclick="dlHtml()">⬇ Download</button>
</div>
<div class="wrap">
  <h1>Mock Interview Feedback</h1>
  <p class="sub">Student: <b>{name}</b> · Maths &amp; Logical Reasoning</p>

  <div class="stats">
    <div class="stat"><div class="big">{_esc(overall)}<span style="font-size:18px;color:var(--muted)">/10</span></div><div class="lbl">Overall score</div></div>
  </div>

  <div class="card">{_esc(report.get('summary',''))}</div>

  <h2>Concept strength</h2>
  <div class="chips">{concept_chips}</div>

  <h2>Per-question breakdown</h2>
  <div class="legend">Each answer is scored 0–10 on four dimensions:
    <b>Correctness</b> (is the final answer right), <b>Concept clarity</b> (understanding the why),
    <b>Explanation</b> (how well it's communicated) and <b>Basics</b> (fundamentals).</div>
  {qcards}

  <h2>Communication</h2>
  <div class="card">{_esc(report.get('communication_assessment',''))}</div>

  <h2>Top things to work on</h2>
  <div class="card"><ul>{improvements}</ul>
    <div style="margin-top:12px"><b>Revise:</b> <span class="chips" style="display:inline-flex">{revision}</span></div></div>

  <div class="enc">{_esc(report.get('encouragement_note',''))}</div>
  {mentor_notes}
</div>
<script>
  function tg(){{const h=document.documentElement;h.classList.toggle('light');
    document.getElementById('themeBtn').textContent=h.classList.contains('light')?'🌙 Dark':'☀️ Light';}}
  function dlHtml(){{const b=new Blob([document.documentElement.outerHTML],{{type:'text/html'}});
    const a=document.createElement('a');a.href=URL.createObjectURL(b);a.download='interview_report.html';a.click();}}
</script>
</body></html>"""
