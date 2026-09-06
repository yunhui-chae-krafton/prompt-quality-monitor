"""Self-contained HTML dashboard.

The centrepiece is the annotated specimen list: real prompts with their vague
references and concrete referents marked in place, the way a copy editor would
mark a manuscript. Everything else on the page exists to give those specimens
a scale to sit on.

Charts are hand-drawn SVG. There is no chart library and no runtime data
fetch; the numbers are baked in at generation time.
"""

import datetime
import html
import re
import statistics
from collections import Counter

from .rubric import (
    _CONCRETE,
    _VAGUE_PHRASES,
    _VAGUE_TOKENS,
    SCORED_KINDS,
)

# --------------------------------------------------------------------------
# Annotation
# --------------------------------------------------------------------------

# Korean particles trail the noun, so the vague token is matched with an
# optional suffix and the mark covers the whole word as written.
_VAGUE_RE = re.compile(
    r"(?<![가-힣A-Za-z])("
    + "|".join(sorted((re.escape(t) for t in _VAGUE_TOKENS), key=len, reverse=True))
    + r")(?![가-힣A-Za-z])",
    re.IGNORECASE,
)


def _spans(text):
    found = []
    for match in _VAGUE_RE.finditer(text):
        found.append((match.start(), match.end(), "vague"))
    for match in _VAGUE_PHRASES.finditer(text):
        found.append((match.start(), match.end(), "vague"))
    for pattern in _CONCRETE:
        for match in pattern.finditer(text):
            found.append((match.start(), match.end(), "concrete"))

    found.sort(key=lambda s: (s[0], -(s[1] - s[0])))
    kept, cursor = [], -1
    for start, end, kind in found:
        if start >= cursor and end > start:
            kept.append((start, end, kind))
            cursor = end
    return kept


def annotate(text, limit=420):
    """Escape the prompt and wrap its marked regions in spans."""
    if len(text) > limit:
        text = text[:limit].rstrip() + " …"

    out, cursor = [], 0
    for start, end, kind in _spans(text):
        if start >= len(text):
            break
        end = min(end, len(text))
        out.append(html.escape(text[cursor:start]))
        out.append(f'<mark class="m-{kind}">{html.escape(text[start:end])}</mark>')
        cursor = end
    out.append(html.escape(text[cursor:]))
    return "".join(out).replace("\n", "<br>")


# --------------------------------------------------------------------------
# SVG charts
# --------------------------------------------------------------------------


def _svg_trend(weekly):
    """Two series on one time axis: mean score (line) and rework rate (bars)."""
    if len(weekly) < 2:
        return '<p class="empty">주 단위 추이를 그릴 만큼 데이터가 없습니다.</p>'

    w, h = 720, 236
    pad_l, pad_r, pad_t, pad_b = 44, 46, 18, 40
    plot_w, plot_h = w - pad_l - pad_r, h - pad_t - pad_b
    n = len(weekly)
    step = plot_w / max(n - 1, 1)

    max_rework = max(0.2, max(row["rework_rate"] for row in weekly) * 1.25)
    x_of = lambda i: pad_l + i * step
    y_score = lambda v: pad_t + plot_h * (1 - v / 10.0)
    y_rework = lambda v: pad_t + plot_h * (1 - v / max_rework)

    parts = [f'<svg viewBox="0 0 {w} {h}" role="img" '
             f'aria-label="주별 평균 점수와 재작업률 추이">']

    for frac in (0, 0.25, 0.5, 0.75, 1):
        y = pad_t + plot_h * frac
        parts.append(f'<line class="grid" x1="{pad_l}" y1="{y:.1f}" '
                     f'x2="{w - pad_r}" y2="{y:.1f}"/>')
        parts.append(f'<text class="tick" x="{pad_l - 8}" y="{y + 4:.1f}" '
                     f'text-anchor="end">{10 * (1 - frac):.0f}</text>')
        parts.append(f'<text class="tick alt" x="{w - pad_r + 8}" y="{y + 4:.1f}">'
                     f'{max_rework * (1 - frac) * 100:.0f}%</text>')

    bar_w = min(20.0, step * 0.42)
    for i, row in enumerate(weekly):
        y = y_rework(row["rework_rate"])
        parts.append(f'<rect class="bar" x="{x_of(i) - bar_w / 2:.1f}" y="{y:.1f}" '
                     f'width="{bar_w:.1f}" height="{pad_t + plot_h - y:.1f}" rx="2"/>')

    line = " ".join(f"{x_of(i):.1f},{y_score(row['mean_score']):.1f}"
                    for i, row in enumerate(weekly))
    parts.append(f'<polyline class="series" points="{line}"/>')
    for i, row in enumerate(weekly):
        parts.append(f'<circle class="dot" cx="{x_of(i):.1f}" '
                     f'cy="{y_score(row["mean_score"]):.1f}" r="3.5"/>')

    for i, row in enumerate(weekly):
        if n > 8 and i % 2:
            continue
        label = datetime.date.fromisoformat(row["week"]).strftime("%m/%d")
        parts.append(f'<text class="tick" x="{x_of(i):.1f}" y="{h - pad_b + 18}" '
                     f'text-anchor="middle">{label}</text>')

    parts.append("</svg>")
    return "".join(parts)


def _svg_scatter(pairs):
    """Deterministic score against LLM rubric score, with the agreement line."""
    if len(pairs) < 8:
        return '<p class="empty">비교할 채점 표본이 부족합니다.</p>'

    w, h = 360, 300
    pad = 40
    plot = min(w, h) - pad - 18
    x_of = lambda v: pad + plot * (v / 10.0)
    y_of = lambda v: pad + plot * (1 - v / 10.0)

    parts = [f'<svg viewBox="0 0 {w} {h}" role="img" '
             f'aria-label="정규식 점수와 LLM 루브릭 점수 비교">']
    for tick in (0, 2.5, 5, 7.5, 10):
        parts.append(f'<line class="grid" x1="{x_of(tick):.1f}" y1="{pad}" '
                     f'x2="{x_of(tick):.1f}" y2="{pad + plot}"/>')
        parts.append(f'<line class="grid" x1="{pad}" y1="{y_of(tick):.1f}" '
                     f'x2="{pad + plot}" y2="{y_of(tick):.1f}"/>')
    parts.append(f'<line class="agree" x1="{x_of(0):.1f}" y1="{y_of(0):.1f}" '
                 f'x2="{x_of(10):.1f}" y2="{y_of(10):.1f}"/>')

    for det, llm in pairs:
        parts.append(f'<circle class="pt" cx="{x_of(det):.1f}" '
                     f'cy="{y_of(llm):.1f}" r="3"/>')

    parts.append(f'<text class="tick" x="{pad + plot / 2:.0f}" y="{h - 6}" '
                 f'text-anchor="middle">정규식 점수 (백분위)</text>')
    parts.append(f'<text class="tick" x="14" y="{pad + plot / 2:.0f}" '
                 f'text-anchor="middle" transform="rotate(-90 14 '
                 f'{pad + plot / 2:.0f})">LLM 루브릭 점수</text>')
    parts.append("</svg>")
    return "".join(parts)


def _svg_hist(values, label, color_class):
    if not values:
        return ""
    w, h = 224, 132
    pad_l, pad_b, pad_t = 26, 26, 12
    plot_w, plot_h = w - pad_l - 10, h - pad_b - pad_t

    counts = Counter(int(round(v)) for v in values)
    top = max(counts.values())
    bar_w = plot_w / 11.0

    parts = [f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="{label} 분포">']
    for score in range(11):
        count = counts.get(score, 0)
        bar_h = plot_h * count / top if top else 0
        x = pad_l + score * bar_w
        parts.append(f'<rect class="{color_class}" x="{x + 1.2:.1f}" '
                     f'y="{pad_t + plot_h - bar_h:.1f}" width="{bar_w - 2.4:.1f}" '
                     f'height="{bar_h:.1f}" rx="1.5"/>')
    parts.append(f'<line class="axis" x1="{pad_l}" y1="{pad_t + plot_h}" '
                 f'x2="{w - 10}" y2="{pad_t + plot_h}"/>')
    for score in (0, 5, 10):
        parts.append(f'<text class="tick" x="{pad_l + score * bar_w + bar_w / 2:.1f}" '
                     f'y="{h - 8}" text-anchor="middle">{score}</text>')
    parts.append(f'<text class="tick" x="{pad_l - 6}" y="{pad_t + 8}" '
                 f'text-anchor="end">{top}</text>')
    parts.append("</svg>")
    return "".join(parts)


# --------------------------------------------------------------------------
# Page
# --------------------------------------------------------------------------

_CSS = """
:root {
  color-scheme: light;
  --paper:      #F2F4F7;
  --surface:    #FBFCFD;
  --ink:        #171A20;
  --ink-soft:   #565E6C;
  --ink-faint:  #8D95A3;
  --rule:       #DCE1E8;
  --accent:     #1B3A5C;
  --accent-dim: #4A6C91;
  --flag:       #A93226;
  --flag-wash:  #F6E3E0;
  --affirm:     #2C6650;
  --affirm-wash:#E0EDE7;
  --amber:      #96690F;
  --shadow:     0 1px 2px rgba(23, 26, 32, .06);
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    color-scheme: dark;
    --paper:      #121519;
    --surface:    #191D23;
    --ink:        #E5E8ED;
    --ink-soft:   #A2ABB8;
    --ink-faint:  #6E7885;
    --rule:       #2A303A;
    --accent:     #8FB4DC;
    --accent-dim: #6E8FB4;
    --flag:       #E58A7E;
    --flag-wash:  #3A2523;
    --affirm:     #7FC0A4;
    --affirm-wash:#1E322A;
    --amber:      #D8A94A;
    --shadow:     0 1px 2px rgba(0, 0, 0, .3);
  }
}
:root[data-theme="dark"] {
  color-scheme: dark;
  --paper:      #121519;
  --surface:    #191D23;
  --ink:        #E5E8ED;
  --ink-soft:   #A2ABB8;
  --ink-faint:  #6E7885;
  --rule:       #2A303A;
  --accent:     #8FB4DC;
  --accent-dim: #6E8FB4;
  --flag:       #E58A7E;
  --flag-wash:  #3A2523;
  --affirm:     #7FC0A4;
  --affirm-wash:#1E322A;
  --amber:      #D8A94A;
  --shadow:     0 1px 2px rgba(0, 0, 0, .3);
}

* { box-sizing: border-box; }
body {
  margin: 0;
  background: var(--paper);
  color: var(--ink);
  font-family: "IBM Plex Sans", ui-sans-serif, system-ui, "Apple SD Gothic Neo", sans-serif;
  font-size: 15px;
  line-height: 1.62;
  -webkit-font-smoothing: antialiased;
}
.wrap { max-width: 1080px; margin: 0 auto; padding: 56px 28px 96px; }

.masthead { border-bottom: 2px solid var(--ink); padding-bottom: 20px; }
.eyebrow {
  font-family: "IBM Plex Mono", ui-monospace, monospace;
  font-size: 11px; letter-spacing: .14em; text-transform: uppercase;
  color: var(--accent-dim); margin: 0 0 10px;
}
h1 {
  font-family: Newsreader, Georgia, "Apple SD Gothic Neo", serif;
  font-weight: 500; font-size: clamp(30px, 4.4vw, 44px);
  line-height: 1.14; margin: 0; text-wrap: balance;
}
.dek { color: var(--ink-soft); margin: 12px 0 0; max-width: 62ch; }

.scoreband {
  display: grid; grid-template-columns: auto 1fr; gap: 0;
  margin: 26px 0 0; border: 1px solid var(--rule); border-radius: 3px;
  background: var(--surface); box-shadow: var(--shadow); overflow: hidden;
}
.scoreband .overall {
  padding: 18px 26px 16px; border-right: 1px solid var(--rule);
  background: linear-gradient(to bottom, transparent, var(--paper));
}
.scoreband .overall dt {
  font-size: 11px; letter-spacing: .12em; text-transform: uppercase;
  color: var(--ink-faint); margin-bottom: 2px;
}
.scoreband .overall dd {
  margin: 0; font-family: "IBM Plex Mono", ui-monospace, monospace;
  font-size: 46px; line-height: 1; color: var(--accent);
  font-variant-numeric: tabular-nums;
}
.scoreband .overall dd span {
  font-size: 15px; color: var(--ink-faint); margin-left: 2px;
}
.scoreband .overall p {
  margin: 8px 0 0; font-size: 11.5px; color: var(--ink-faint);
}
.scoreband .axes {
  display: grid; grid-template-columns: repeat(3, 1fr); gap: 0; margin: 0;
}
.scoreband .axis { padding: 18px 20px 16px; border-right: 1px solid var(--rule); }
.scoreband .axis:last-child { border-right: 0; }
.scoreband .axis dt {
  font-size: 11px; letter-spacing: .06em; color: var(--ink-faint);
  margin-bottom: 3px;
}
.scoreband .axis dd {
  margin: 0 0 9px; font-family: "IBM Plex Mono", monospace;
  font-size: 24px; line-height: 1; font-variant-numeric: tabular-nums;
}
.scoreband .axis dd span { font-size: 11px; color: var(--ink-faint); }
.scoreband .track {
  height: 4px; background: var(--rule); border-radius: 2px; overflow: hidden;
}
.scoreband .track i { display: block; height: 100%; background: var(--accent-dim); }
.scoreband.empty-band { display: block; padding: 16px 22px; }
.scoreband.empty-band p { margin: 0; font-size: 13.5px; color: var(--ink-soft); }
.scoreband.empty-band code {
  font-family: "IBM Plex Mono", monospace; font-size: 12px;
  background: var(--paper); padding: 1px 6px; border-radius: 2px;
  border: 1px solid var(--rule);
}

.strip {
  display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
  gap: 0; margin: 14px 0 0; border: 1px solid var(--rule);
  background: var(--surface); border-radius: 3px; overflow: hidden;
}
.strip div { padding: 14px 18px; border-right: 1px solid var(--rule); }
.strip div:last-child { border-right: 0; }
.strip dt {
  font-size: 11px; letter-spacing: .08em; text-transform: uppercase;
  color: var(--ink-faint); margin: 0 0 4px;
}
.strip dd {
  margin: 0; font-family: "IBM Plex Mono", ui-monospace, monospace;
  font-size: 21px; font-variant-numeric: tabular-nums; color: var(--ink);
}
.strip dd span { font-size: 12px; color: var(--ink-faint); margin-left: 3px; }

section { margin-top: 52px; }
h2 {
  font-family: Newsreader, Georgia, serif; font-weight: 500;
  font-size: 25px; margin: 0 0 6px; text-wrap: balance;
}
.sub { color: var(--ink-soft); margin: 0 0 22px; max-width: 68ch; font-size: 14px; }

.verdict {
  border-left: 3px solid var(--amber); background: var(--surface);
  padding: 18px 22px; border-radius: 0 3px 3px 0; box-shadow: var(--shadow);
}
.verdict p { margin: 0 0 10px; }
.verdict p:last-child { margin-bottom: 0; }
.verdict strong { color: var(--ink); }
.verdict p.caveat {
  font-size: 13px; color: var(--ink-soft);
  border-top: 1px solid var(--rule); padding-top: 10px; margin-top: 14px;
}

.composition { display: flex; height: 34px; border-radius: 3px; overflow: hidden; }
.composition span {
  display: grid; place-items: center; font-size: 11px;
  font-family: "IBM Plex Mono", monospace; color: var(--surface);
}
.legend {
  display: flex; flex-wrap: wrap; gap: 16px; margin-top: 12px;
  font-size: 12.5px; color: var(--ink-soft);
}
.legend b { font-weight: 500; color: var(--ink); }
.swatch {
  display: inline-block; width: 9px; height: 9px; border-radius: 2px;
  margin-right: 6px; vertical-align: baseline;
}

table { border-collapse: collapse; width: 100%; font-size: 14px; }
.scroll { overflow-x: auto; }
th, td { text-align: left; padding: 9px 12px; border-bottom: 1px solid var(--rule); }
th {
  font-size: 11px; letter-spacing: .07em; text-transform: uppercase;
  color: var(--ink-faint); font-weight: 500; border-bottom: 1px solid var(--ink);
}
td.num, th.num {
  text-align: right; font-family: "IBM Plex Mono", monospace;
  font-variant-numeric: tabular-nums;
}
tr.sig td { color: var(--ink); }
tr.sig td.num:first-of-type { color: var(--affirm); font-weight: 600; }
td.null { color: var(--ink-faint); }

.bars td { padding: 6px 12px; }
.meter { position: relative; height: 8px; background: var(--rule); border-radius: 4px; }
.meter i { position: absolute; inset: 0 auto 0 0; background: var(--accent-dim); border-radius: 4px; }

.specimens { display: grid; gap: 12px; }
/* The grid display would otherwise beat the [hidden] attribute and both tab
   panels would render at once in the standalone file. */
.specimens[hidden] { display: none; }
.spec {
  background: var(--surface); border: 1px solid var(--rule);
  border-radius: 3px; padding: 14px 16px; box-shadow: var(--shadow);
}
.spec.low { border-left: 3px solid var(--flag); }
.spec.high { border-left: 3px solid var(--affirm); }
.spec-text {
  font-family: "IBM Plex Mono", ui-monospace, monospace;
  font-size: 13px; line-height: 1.75; word-break: break-word;
}
mark { background: none; padding: 0 1px; }
mark.m-vague {
  color: var(--flag); background: var(--flag-wash);
  text-decoration: underline wavy var(--flag) 1px;
  text-underline-offset: 3px;
}
mark.m-concrete {
  color: var(--affirm); background: var(--affirm-wash);
  text-decoration: underline solid var(--affirm) 1px;
  text-underline-offset: 3px;
}
.spec-meta {
  display: flex; flex-wrap: wrap; gap: 8px 14px; align-items: center;
  margin-top: 11px; padding-top: 10px; border-top: 1px dashed var(--rule);
  font-size: 12px; color: var(--ink-soft);
}
.pill {
  font-family: "IBM Plex Mono", monospace; font-size: 11px;
  padding: 2px 7px; border-radius: 2px; border: 1px solid var(--rule);
  color: var(--ink-soft); font-variant-numeric: tabular-nums;
}
.pill.bad { border-color: var(--flag); color: var(--flag); }
.pill.ok { border-color: var(--affirm); color: var(--affirm); }
.issue { color: var(--ink-soft); font-size: 13px; margin: 8px 0 0; }
.issue b { color: var(--flag); font-weight: 500; }
.rewrite {
  margin: 8px 0 0; padding: 9px 12px; border-radius: 2px;
  background: var(--affirm-wash); color: var(--ink);
  font-size: 13px; line-height: 1.6;
}
.rewrite b {
  display: block; font-size: 10.5px; letter-spacing: .1em;
  text-transform: uppercase; color: var(--affirm); margin-bottom: 3px;
}

.tabs { display: flex; gap: 6px; margin-bottom: 16px; }
.tabs button {
  font: inherit; font-size: 13px; padding: 5px 13px; cursor: pointer;
  background: transparent; color: var(--ink-soft);
  border: 1px solid var(--rule); border-radius: 2px;
}
.tabs button[aria-selected="true"] {
  background: var(--accent); border-color: var(--accent); color: var(--surface);
}
.tabs button:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }

.split { display: grid; grid-template-columns: 1.6fr 1fr; gap: 28px; align-items: start; }
.trio { display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: 18px; }
.panel {
  background: var(--surface); border: 1px solid var(--rule);
  border-radius: 3px; padding: 16px 18px; box-shadow: var(--shadow);
}
.panel h3 {
  margin: 0 0 2px; font-size: 13px; font-weight: 600; letter-spacing: .01em;
}
.panel p.hint { margin: 0 0 10px; font-size: 12px; color: var(--ink-faint); }
svg { display: block; width: 100%; height: auto; }
.grid { stroke: var(--rule); stroke-width: 1; }
.axis { stroke: var(--ink-faint); stroke-width: 1; }
.tick { fill: var(--ink-faint); font-size: 10.5px;
        font-family: "IBM Plex Mono", monospace; }
.tick.alt { fill: var(--accent-dim); }
.series { fill: none; stroke: var(--accent); stroke-width: 2;
          stroke-linejoin: round; stroke-linecap: round; }
.dot { fill: var(--accent); }
.bar { fill: var(--flag); opacity: .3; }
.agree { stroke: var(--ink-faint); stroke-width: 1; stroke-dasharray: 4 4; }
.pt { fill: var(--accent); opacity: .45; }
.h-coh { fill: var(--accent-dim); }
.h-cpx { fill: var(--amber); }
.h-clr { fill: var(--affirm); }
.empty { color: var(--ink-faint); font-size: 13px; }

.terms { display: flex; flex-wrap: wrap; gap: 7px; }
.term {
  display: inline-flex; align-items: baseline; gap: 7px;
  border: 1px solid var(--rule); background: var(--surface);
  border-radius: 2px; padding: 4px 10px; font-size: 13px;
}
.term b { font-weight: 500; }
.term i {
  font-style: normal; font-family: "IBM Plex Mono", monospace;
  font-size: 11px; color: var(--flag); font-variant-numeric: tabular-nums;
}

footer {
  margin-top: 64px; padding-top: 20px; border-top: 1px solid var(--rule);
  font-size: 12.5px; color: var(--ink-faint);
}
footer p { margin: 0 0 7px; max-width: 76ch; }
footer code {
  font-family: "IBM Plex Mono", monospace; font-size: 11.5px;
  background: var(--surface); padding: 1px 5px; border-radius: 2px;
  border: 1px solid var(--rule);
}
@media (max-width: 760px) {
  .split { grid-template-columns: 1fr; }
  .scoreband { grid-template-columns: 1fr; }
  .scoreband .overall { border-right: 0; border-bottom: 1px solid var(--rule); }
  .wrap { padding: 36px 18px 64px; }
}
@media (prefers-reduced-motion: reduce) {
  * { animation: none !important; transition: none !important; }
}
"""

_JS = """
document.querySelectorAll('[data-tabs]').forEach(function (group) {
  var buttons = group.querySelectorAll('button[data-panel]');
  buttons.forEach(function (button) {
    button.addEventListener('click', function () {
      buttons.forEach(function (other) {
        var target = document.getElementById(other.dataset.panel);
        var active = other === button;
        other.setAttribute('aria-selected', active ? 'true' : 'false');
        if (target) { target.hidden = !active; }
      });
    });
  });
});
"""

_KIND_META = [
    ("refine", "후속 지시", "var(--accent)"),
    ("question", "질문", "var(--accent-dim)"),
    ("correct", "정정", "var(--flag)"),
    ("initiate", "과제 개시", "var(--affirm)"),
    ("approve", "승인", "var(--ink-faint)"),
    ("status", "상태 보고", "var(--amber)"),
]

_CORR_ROWS = [
    ("prompt_score_vs_rework", "프롬프트 점수 → 직후 재작업"),
    ("specificity_vs_rework", "구체성 → 직후 재작업"),
    ("constraints_vs_rework", "제약 명시 → 직후 재작업"),
    ("prompt_score_vs_agent_turns", "프롬프트 점수 → 에이전트 작업량"),
    ("llm_score_vs_rework", "LLM 루브릭 점수 → 직후 재작업"),
    ("llm_clarity_vs_rework", "LLM 지시 명확성 → 직후 재작업"),
    ("llm_vs_regex_score", "LLM 루브릭 ↔ 정규식 (두 채점의 일치도)"),
    ("session_mtld_vs_rework", "세션 MTLD → 재작업률"),
    ("session_hdd_vs_rework", "세션 HD-D → 재작업률"),
    ("session_score_vs_rework_partial", "세션 평균 점수 → 재작업률 (턴수 통제)"),
    ("session_turns_vs_hdd", "세션 턴수 → HD-D"),
]


def _esc(value):
    return html.escape(str(value))


def render(result):
    prompts = result["prompts"]
    scored = [r for r in prompts if r["kind"] in SCORED_KINDS and "scores" in r]
    sessions = result["sessions"]
    # A prompt text can recur as both an instruction and an approval, and the
    # grade cache is keyed by text, so require the rubric score too.
    graded = [r for r in scored
              if r.get("llm") and r["llm"].get("mean") is not None
              and not r.get("has_attachment")]
    stamps = sorted(r["timestamp"] for r in prompts if r.get("timestamp"))
    period = (f"{stamps[0][:10]} – {stamps[-1][:10]}" if stamps else "기간 미상")

    body = [
        '<div class="wrap">',
        _masthead(prompts, scored, sessions, period, graded),
        _verdict(result["correlations"], scored, graded),
        _composition(result["kind_counts"], len(prompts)),
        _habits(scored),
        _specimens(scored, graded),
        _terms(result["vague_top"]),
        _charts(result, scored, graded),
        _correlations(result["correlations"]),
        _footer(period, len(graded)),
        "</div>",
        f"<script>{_JS}</script>",
    ]

    return (
        "<title>프롬프트 품질 모니터</title>\n"
        '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>\n'
        '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?'
        "family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans:wght@400;500;600&"
        'family=Newsreader:opsz,wght@6..72,400;6..72,500&display=swap">\n'
        f"<style>{_CSS}</style>\n" + "\n".join(body)
    )


def _masthead(prompts, scored, sessions, period, graded):
    """Headline the score, then the corpus it was computed over.

    The deterministic score is a percentile inside this corpus, so its mean is
    5 whatever the prompts look like — putting it up here would be a number
    that cannot move. The rubric axes are absolute 0-10, so they are the only
    honest headline, and the page says plainly when they are missing.
    """
    return (
        '<header class="masthead">'
        '<p class="eyebrow">Prompt Quality Monitor</p>'
        "<h1>내 프롬프트는 시간이 지나며 나아지고 있는가</h1>"
        f'<p class="dek">Claude Code 세션 기록에서 직접 타이핑한 프롬프트만 골라내, '
        f"Thorgeirsson·Weidmann·Su (CHI ’26)의 전문가 루브릭을 적용한 결과. {_esc(period)}.</p>"
        "</header>"
        + _scoreband(graded)
        + _corpus_strip(prompts, scored, sessions)
    )


def _scoreband(graded):
    axes = []
    for axis, label in (("coherence", "일관성"), ("complexity", "적정 정보량"),
                        ("clarity", "지시 명확성")):
        values = [r["llm"][axis] for r in graded
                  if isinstance(r["llm"].get(axis), (int, float))]
        if values:
            axes.append((label, statistics.fmean(values)))

    if not axes:
        return (
            '<div class="scoreband empty-band">'
            '<p><strong>아직 점수가 없습니다.</strong> 아래 습관 지표는 전량 집계된 '
            "결정론적 값이고, 0–10 절대 점수는 루브릭 채점을 돌려야 나옵니다 — "
            "<code>pqm.py grade --limit 240</code>.</p></div>"
        )

    overall = statistics.fmean(value for _, value in axes)
    n = len(graded)
    dials = "".join(
        f'<div class="axis">'
        f"<dt>{_esc(label)}</dt>"
        f'<dd>{value:.1f}<span>/10</span></dd>'
        f'<div class="track"><i style="width:{value * 10:.1f}%"></i></div>'
        "</div>"
        for label, value in axes
    )
    return (
        '<div class="scoreband">'
        '<div class="overall">'
        '<dt>종합</dt>'
        f'<dd>{overall:.1f}<span>/10</span></dd>'
        f'<p>루브릭 채점 {n}개 표본</p>'
        "</div>"
        f'<dl class="axes">{dials}</dl>'
        "</div>"
    )


def _corpus_strip(prompts, scored, sessions):
    usable = [s for s in sessions if s.get("mtld")]
    mtld = statistics.median(s["mtld"] for s in usable) if usable else 0
    corrections = sum(1 for r in prompts if r["kind"] == "correct")

    cells = [
        ("입력 프롬프트", f"{len(prompts):,}", "개"),
        ("세션", f"{len(sessions):,}", "개"),
        ("지시 턴", f"{len(scored):,}", "개"),
        ("재작업 턴", f"{100.0 * corrections / max(len(prompts), 1):.1f}", "%"),
        ("세션 MTLD 중앙값", f"{mtld:.0f}", ""),
    ]
    strip = "".join(
        f"<div><dt>{_esc(label)}</dt><dd>{value}<span>{unit}</span></dd></div>"
        for label, value, unit in cells
    )
    return f'<dl class="strip">{strip}</dl>'


def _verdict(corrs, scored, graded):
    """Lead with whichever scorer actually predicted something."""
    regex = corrs.get("prompt_score_vs_rework")
    judge = corrs.get("llm_score_vs_rework")
    agree = corrs.get("llm_vs_regex_score")
    lines = []

    if judge and judge["p"] < 0.05 and judge["r"] < 0:
        lines.append(
            f"<p><strong>루브릭으로 채점한 프롬프트 품질은 재작업을 예측한다</strong> "
            f"(r = {judge['r']:+.3f}, p = {judge['p']:.3f}, n = {judge['n']}). "
            f"음의 상관이므로 방향은 기대한 대로다 — 잘 특정된 지시일수록 "
            f"다음 한두 턴 안에 “아니, 그게 아니라”가 덜 따라온다.</p>"
        )
    elif judge:
        lines.append(
            f"<p>루브릭 채점 점수와 재작업 사이에서도 유의한 관계가 나오지 않았다 "
            f"(r = {judge['r']:+.3f}, p = {judge['p']:.2f}, n = {judge['n']}).</p>"
        )

    if regex and regex["p"] >= 0.05:
        note = ""
        if agree:
            note = (f" 두 채점 방식의 일치도 자체가 r = {agree['r']:+.3f} "
                    f"(p = {agree['p']:.2f})로 사실상 없다시피 하다.")
        lines.append(
            f"<p>반면 <strong>정규식 지표는 아무것도 예측하지 못했다</strong> "
            f"(r = {regex['r']:+.3f}, p = {regex['p']:.2f}, n = {regex['n']:,}).{note} "
            f"경로·식별자·수치를 세는 것과 “지시가 명확한가”는 서로 다른 것을 재고 있다는 뜻이다. "
            f"정규식 층은 예측이 아니라 습관 프로파일링 — 어떤 모호어를 쓰는지, 제약을 얼마나 "
            f"명시하는지 — 에만 쓰는 것이 맞다.</p>"
        )

    if graded:
        axes = {}
        for axis, label in (("coherence", "일관성"), ("complexity", "적정 정보량"),
                            ("clarity", "지시 명확성")):
            values = [r["llm"][axis] for r in graded
                      if isinstance(r["llm"].get(axis), (int, float))]
            if values:
                axes[label] = statistics.fmean(values)
        if len(axes) == 3:
            best = max(axes, key=axes.get)
            worst = min(axes, key=axes.get)
            lines.append(
                f"<p>축별로 보면 약한 곳이 분명하다. {best} {axes[best]:.1f}점에 견줘 "
                f"{worst}이 {axes[worst]:.1f}점이다. 말이 꼬이는 것이 문제가 아니라 "
                f"<strong>대상과 완료 조건을 특정하지 않는 것</strong>이 문제다.</p>"
            )

    lines.append(
        "<p class=\"caveat\">단, 성과의 대리 지표는 <em>재작업</em>, 즉 다음 한두 턴 안에 "
        "사람이 정정을 넣었는지 여부다. 논문은 채점된 결과물을 썼지만 세션 로그에는 그 정답이 없다. "
        "재작업은 과제 난이도·환경 문제·모델 실수에도 함께 반응하므로 위 r 값은 하한으로 읽어야 한다.</p>"
    )
    return ("<section><h2>한 줄 결론</h2>"
            f'<div class="verdict">{"".join(lines)}</div></section>')


def _composition(kind_counts, total):
    segments, legend = [], []
    for kind, label, color in _KIND_META:
        count = kind_counts.get(kind, 0)
        if not count:
            continue
        pct = 100.0 * count / total
        text = f"{pct:.0f}%" if pct >= 7 else ""
        segments.append(
            f'<span style="flex:{pct:.4f};background:{color}">{text}</span>'
        )
        legend.append(
            f'<span><i class="swatch" style="background:{color}"></i>'
            f"<b>{_esc(label)}</b> {count:,}</span>"
        )
    return (
        "<section><h2>무엇을 입력하고 있나</h2>"
        '<p class="sub">전체 입력의 구성. 승인·상태 보고는 지시가 아니므로 루브릭에서 제외했다. '
        "이 분리를 하지 않으면 “응”, “오케이 진행해줘” 같은 턴이 구체성 0점으로 집계되어 "
        "점수 전체가 말투에 끌려간다.</p>"
        f'<div class="composition">{"".join(segments)}</div>'
        f'<div class="legend">{"".join(legend)}</div></section>'
    )


def _habits(scored):
    if not scored:
        return ""
    n = len(scored)
    has = lambda key: sum(1 for r in scored if r["features"][key] > 0)
    rows = [
        ("구체적 참조를 포함", has("concrete_hits"),
         "파일명·경로·식별자·수치·인용부호"),
        ("제약 조건을 명시", has("constraint_hits"),
         "“~하지 말고”, “~까지만”, “반드시”, 예외 처리"),
        ("출력 형식을 지정", has("format_hits"), "JSON·표·마크다운 등"),
        ("여러 줄로 작성", sum(1 for r in scored if r["features"]["lines"] > 1),
         "줄바꿈이 있는 프롬프트"),
        ("목록·번호를 사용", has("list_items"), "- 또는 1. 로 항목 분리"),
        ("모호어를 포함", has("vague_hits"), "이거·그거·좀·잘·대충 등"),
        ("붙여넣은 첨부를 참조", sum(1 for r in scored if r.get("has_attachment")),
         "[Image #N] — 지시 대상이 텍스트 밖에 있어 LLM 채점에서 제외"),
    ]
    body = "".join(
        f"<tr><td>{_esc(label)}<br>"
        f'<small style="color:var(--ink-faint)">{_esc(note)}</small></td>'
        f'<td style="width:46%"><div class="meter">'
        f'<i style="width:{100.0 * count / n:.1f}%"></i></div></td>'
        f'<td class="num">{100.0 * count / n:.1f}%</td></tr>'
        for label, count, note in rows
    )
    median_len = statistics.median(r["chars"] for r in scored)
    return (
        "<section><h2>지시 턴의 습관</h2>"
        f'<p class="sub">지시 턴 {n:,}개 기준. 중앙값 길이 {median_len:.0f}자.</p>'
        f'<table class="bars"><tbody>{body}</tbody></table></section>'
    )


def _spec_card(rec, css_class):
    pills = [f'<span class="pill">정규식 {rec["scores"]["total"]:.1f}</span>']
    issue = rewrite = ""
    if rec.get("llm") and rec["llm"].get("mean") is not None:
        llm = rec["llm"]
        tone = "bad" if llm["mean"] < 5 else "ok"
        pills.append(f'<span class="pill {tone}">LLM {llm["mean"]:.1f}</span>')
        pills.append(
            f'<span class="pill">일관 {llm.get("coherence", "-")} · '
            f'정보량 {llm.get("complexity", "-")} · 명확 {llm.get("clarity", "-")}</span>'
        )
        if (llm.get("issue") or "").strip():
            issue = f'<p class="issue"><b>지적</b> {_esc(llm["issue"])}</p>'
        if (llm.get("rewrite") or "").strip():
            rewrite = (f'<p class="rewrite"><b>개선안</b>'
                       f'{_esc(llm["rewrite"][:340])}</p>')

    stamp = (rec.get("timestamp") or "")[:10]
    return (
        f'<article class="spec {css_class}">'
        f'<div class="spec-text">{annotate(rec["text"])}</div>'
        f'<div class="spec-meta">{"".join(pills)}'
        f'<span style="margin-left:auto">{_esc(stamp)}</span></div>'
        f"{issue}{rewrite}</article>"
    )


def _specimens(scored, graded):
    pool = graded if len(graded) >= 12 else scored
    key = ((lambda r: r["llm"]["mean"]) if pool is graded
           else (lambda r: r["scores"]["total"]))
    # The same prompt gets retyped across sessions; showing it six times
    # would spend the panel on one habit.
    seen, unique = set(), []
    for rec in sorted(pool, key=key):
        marker = rec["text"].strip()
        if marker in seen:
            continue
        seen.add(marker)
        unique.append(rec)

    low = "".join(_spec_card(r, "low") for r in unique[:6])
    high = "".join(_spec_card(r, "high") for r in unique[-6:][::-1])

    return (
        "<section><h2>표본</h2>"
        '<p class="sub">실제 입력한 프롬프트에 표시를 넣었다. '
        '<mark class="m-vague">모호한 지시어</mark>와 '
        '<mark class="m-concrete">구체적 참조</mark>가 어디에 붙는지 보라.</p>'
        '<div class="tabs" data-tabs role="tablist">'
        '<button data-panel="spec-low" role="tab" aria-selected="true">낮게 평가된 6개</button>'
        '<button data-panel="spec-high" role="tab" aria-selected="false">높게 평가된 6개</button>'
        "</div>"
        f'<div class="specimens" id="spec-low">{low}</div>'
        f'<div class="specimens" id="spec-high" hidden>{high}</div>'
        "</section>"
    )


def _terms(vague_top):
    if not vague_top:
        return ""
    chips = "".join(
        f'<span class="term"><b>{_esc(row["term"])}</b><i>{row["count"]}</i></span>'
        for row in vague_top[:20]
    )
    return (
        "<section><h2>가장 자주 쓰는 모호어</h2>"
        '<p class="sub">지시 턴에서의 출현 횟수. 이 목록이 곧 개인화된 금지어 후보이며, '
        "대체어는 그 자리에서 가리키려던 파일·함수·화면의 이름 그 자체다.</p>"
        f'<div class="terms">{chips}</div></section>'
    )


def _charts(result, scored, graded):
    pairs = [(r["scores"]["total"], r["llm"]["mean"]) for r in graded
             if "scores" in r]
    hist = ""
    if graded:
        panels = []
        for axis, label, css in (("coherence", "일관성", "h-coh"),
                                 ("complexity", "적정 정보량", "h-cpx"),
                                 ("clarity", "지시 명확성", "h-clr")):
            values = [r["llm"][axis] for r in graded
                      if isinstance(r["llm"].get(axis), (int, float))]
            if not values:
                continue
            panels.append(
                f'<div class="panel"><h3>{label}</h3>'
                f'<p class="hint">평균 {statistics.fmean(values):.1f} · '
                f"표본 {len(values)}</p>{_svg_hist(values, label, css)}</div>"
            )
        if panels:
            hist = (
                "<section><h2>LLM 루브릭 축별 분포</h2>"
                '<p class="sub">0–10 절대 점수. 정규식 점수와 달리 백분위가 아니므로 '
                "분포 자체를 읽을 수 있다.</p>"
                f'<div class="trio">{"".join(panels)}</div></section>'
            )

    return (
        "<section><h2>추이와 검증</h2>"
        '<p class="sub">왼쪽은 주별 흐름, 오른쪽은 두 채점 방식이 어디서 갈라지는지. '
        "대각선 위쪽 점은 정규식이 과소평가한 프롬프트, 아래쪽은 과대평가한 프롬프트다.</p>"
        '<div class="split">'
        '<div class="panel"><h3>주별 평균 점수와 재작업률</h3>'
        '<p class="hint">선 = 평균 점수(왼쪽 축, 백분위) · 막대 = 재작업 발생률(오른쪽 축)</p>'
        f'{_svg_trend(result.get("weekly", []))}</div>'
        '<div class="panel"><h3>정규식 vs LLM</h3>'
        f'<p class="hint">표본 {len(pairs)}개 · 점선은 완전 일치선</p>'
        f"{_svg_scatter(pairs)}</div>"
        "</div></section>" + hist
    )


def _correlations(corrs):
    rows = []
    for key, label in _CORR_ROWS:
        corr = corrs.get(key)
        if not corr:
            continue
        significant = corr["p"] < 0.05
        rows.append(
            f'<tr class="{"sig" if significant else ""}">'
            f"<td>{_esc(label)}</td>"
            f'<td class="num{"" if significant else " null"}">{corr["r"]:+.3f}</td>'
            f'<td class="num{"" if significant else " null"}">{corr["p"]:.3g}</td>'
            f'<td class="num">{corr["n"]:,}</td>'
            f'<td>{"유의" if significant else "—"}</td></tr>'
        )
    return (
        "<section><h2>상관 분석 전체</h2>"
        '<p class="sub">유의하지 않은 결과도 그대로 싣는다. 유의한 것만 골라 보이면 '
        "이 표는 분석이 아니라 광고가 된다. p &lt; 0.05를 유의로 표시했고, "
        "다중 비교 보정은 하지 않았다.</p>"
        '<div class="scroll"><table><thead><tr><th>관계</th>'
        '<th class="num">r</th><th class="num">p</th><th class="num">n</th>'
        "<th>판정</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table></div></section>"
    )


def _footer(period, graded_count):
    return (
        "<footer>"
        f"<p><b>방법.</b> <code>~/.claude/projects</code>의 세션 JSONL에서 "
        f"<code>promptSource=\"typed\"</code>이고 <code>origin.kind=\"human\"</code>인 "
        f"레코드만 추출했다 ({_esc(period)}). 도구 결과·붙여넣기·에이전트 간 메시지는 제외된다. "
        f"형태소 분석은 kiwipiepy, 없으면 정규식 근사로 대체한다. "
        f"MTLD·HD-D는 McCarthy &amp; Jarvis(2010)를 직접 구현했고, URL·경로·식별자는 "
        f"각각 하나의 타입으로 접어 어휘 다양성이 부풀지 않게 했다.</p>"
        f"<p><b>한계.</b> 정규식 점수는 코퍼스 내 백분위이므로 전체 평균은 정의상 5다. "
        f"절대 실력이 아니라 상대 위치만 뜻한다. LLM 채점은 층화 표본 {graded_count}개에만 "
        f"적용했고, 이전 대화 맥락 없이 프롬프트 단독으로 평가했다. 재작업 판정은 한국어 정정 "
        f"표현의 정규식 탐지이므로 재현율·정밀도가 검증되지 않았다.</p>"
        f"<p><b>출처.</b> Thorgeirsson, Weidmann &amp; Su. "
        f"<i>Computer Science Achievement and Writing Skills Predict Vibe Coding "
        f"Proficiency.</i> CHI ’26. 논문의 전문가 루브릭 축(coherence / "
        f"task-appropriate complexity / instructional clarity)을 그대로 사용했다.</p>"
        "</footer>"
    )
