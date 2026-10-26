"""Click-through walkthrough export (Phase 6d), SOP Factory's sop-walkthrough
ported to captured sessions: one self-contained HTML page where the reader
clicks the highlighted control on each real screenshot to move forward.

Built from the same step_results, phases and annotated screenshots as every
other format, so the walkthrough and the document always match. SOP Factory
measures each hotspot by hand; here it comes from the manifest: the clicked
element's bounding_rect, mapped into the annotated crop with the same
compute_crop_box() call that produced the crop. The engine (navigation,
keyboard, clickable phase chips, ?shot=N single-step mode) follows SOP
Factory's walkthrough_template.html; no network references at all."""

import html
import json
from pathlib import Path

from PIL import Image

from pipeline.annotate import compute_crop_box
from pipeline.assembler import is_grouped, phase_starts, resolve_phases, step_heading
from pipeline.export_html import _data_uri
from pipeline.photo_build import is_synthetic_step
from pipeline.render import narrative_html_blocks
from pipeline.review_notes import step_review_notes

# Hotspot size when the element has no usable rect: a square around the
# click point, as a share of the crop's width.
_CLICK_HOTSPOT_FRACTION = 0.06


def _raw_size(screenshot_dir, step):
    if screenshot_dir is None:
        return None
    try:
        with Image.open(Path(screenshot_dir) / step.screenshot) as img:
            return img.size
    except OSError:
        return None


def hotspot_box(step, raw_size):
    """[left, top, width, height] in percent of the annotated crop, or None
    when there is nothing to point at (photo-mode step, unknown frame)."""
    if raw_size is None or is_synthetic_step(step):
        return None
    click = (step.screen.x, step.screen.y)
    cl, ct, cr, cb = compute_crop_box(raw_size, step.element.bounding_rect, click)
    cw, ch = cr - cl, cb - ct
    if cw <= 0 or ch <= 0:
        return None
    rect = step.element.bounding_rect
    full_frame = (cl, ct, cr, cb) == (0, 0, *raw_size)
    if rect and not full_frame:
        left, top = max(rect[0], cl), max(rect[1], ct)
        right, bottom = min(rect[2], cr), min(rect[3], cb)
    else:
        half = _CLICK_HOTSPOT_FRACTION * cw / 2
        left, top, right, bottom = (
            click[0] - half,
            click[1] - half,
            click[0] + half,
            click[1] + half,
        )
        left, top, right, bottom = max(left, cl), max(top, ct), min(right, cr), min(bottom, cb)
    if right <= left or bottom <= top:
        return None
    return [
        round((left - cl) / cw * 100, 2),
        round((top - ct) / ch * 100, 2),
        round((right - left) / cw * 100, 2),
        round((bottom - top) / ch * 100, 2),
    ]


def _step_html(result, notes):
    parts = [f"<p>{html.escape(result['text'])}</p>"]
    parts += [f'<div class="warn">{html.escape(note)}</div>' for note in notes]
    if result.get("narration"):
        parts.append(f'<p class="narration">{html.escape(result["narration"])}</p>')
    return "".join(parts)


def walkthrough_steps(
    manifest,
    step_results,
    annotated_paths,
    narrative_text=None,
    phases=None,
    omitted_screenshots=(),
    screenshot_dir=None,
):
    """The page's step list: an intro, one entry per manifest step (in order,
    with its id), and a closing entry. A step whose screenshot was omitted as
    an exact repeat reuses the previous step's picture."""
    title = manifest.session.title or manifest.session.id
    phase_of = {}
    for phase in resolve_phases(manifest, phases):
        for step_id in phase.step_ids:
            phase_of[step_id] = phase.name
    first_phase = next(iter(phase_starts(manifest, phases).values()), "Procedure")

    intro = "".join(narrative_html_blocks(narrative_text)) if narrative_text else ""
    intro += f"<p>{len(manifest.steps)} steps. Click the highlighted control to go on.</p>"
    entries = [
        {"id": None, "phase": first_phase, "title": title, "text": intro, "img": None, "box": None}
    ]
    last_img = last_box = None
    for n, (step, result, shot) in enumerate(
        zip(manifest.steps, step_results, annotated_paths, strict=True), start=1
    ):
        if step.id in omitted_screenshots and last_img:
            img, box = last_img, last_box
        elif shot is not None and Path(shot).exists():
            img, box = _data_uri(shot), hotspot_box(step, _raw_size(screenshot_dir, step))
        else:
            img = box = None
        last_img, last_box = img, box
        entries.append(
            {
                "id": step.id,
                "phase": phase_of[step.id],
                # The card already says "Step N of M"; drop the heading's own
                # "Step N — " prefix so it isn't said twice.
                "title": step_heading(n, step, result).partition(" — ")[2]
                or step_heading(n, step, result),
                "text": _step_html(result, step_review_notes(step)),
                "img": img,
                "box": box,
            }
        )
    entries.append(
        {
            "id": None,
            "phase": entries[-1]["phase"],
            "title": "Done",
            "text": "<p>Procedure complete.</p>",
            "img": None,
            "box": None,
        }
    )
    return entries


_STYLE = """
:root{--bg:#f4f1ec;--panel:#fffdf9;--ink:#1c1f23;--muted:#5f5d59;--line:#e3ddd3;
--accent:#c2410c;--accent-ink:#fff;--warn-bg:#fdeee4;--warn-ink:#8a2c06;--hot:#e8590c;--stage:#16191d}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#16191d;--panel:#22272e;
--ink:#e8e6e3;--muted:#a8a49e;--line:#343a43;--accent:#f97316;--accent-ink:#16191d;
--warn-bg:#3b2417;--warn-ink:#ffc9a8;--stage:#0e1013}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
font:15px/1.5 "IBM Plex Sans","Segoe UI",system-ui,sans-serif}
header{padding:16px;max-width:1180px;margin:0 auto}
h1{font-size:20px;margin:0 0 4px}
.sub{color:var(--muted);font-size:13px}
kbd{font:12px "IBM Plex Mono",Consolas,monospace;border:1px solid var(--line);border-radius:4px;padding:0 4px}
.progress{height:6px;background:var(--line);border-radius:3px;margin-top:12px;overflow:hidden}
.progress>div{height:100%;background:var(--accent);transition:width .25s}
.phases{display:flex;gap:6px;flex-wrap:wrap;margin-top:10px}
.phase{font:inherit;font-size:12px;padding:2px 10px;border-radius:99px;border:1px solid var(--line);
background:none;color:var(--muted);cursor:pointer}
.phase.on{background:var(--accent);color:var(--accent-ink);border-color:var(--accent)}
main{max-width:1180px;margin:0 auto;padding:0 16px 24px;display:grid;grid-template-columns:340px 1fr;gap:16px}
@media (max-width:820px){main{grid-template-columns:1fr}}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:16px}
.stepno{font:12px "IBM Plex Mono",Consolas,monospace;color:var(--muted);text-transform:uppercase;letter-spacing:.05em}
.card h2{font-size:18px;margin:4px 0 10px}
.card p{margin:0 0 10px}
.narration{color:var(--muted);font-style:italic}
.warn{background:var(--warn-bg);color:var(--warn-ink);border-radius:8px;padding:10px 12px;font-size:14px;margin:10px 0;font-weight:600}
.nav{display:flex;gap:8px;margin-top:14px}
.nav button{flex:1;padding:9px;border-radius:8px;font:inherit;font-size:14px;cursor:pointer;
border:1px solid var(--line);background:var(--panel);color:var(--ink)}
.nav button.primary{background:var(--accent);color:var(--accent-ink);border-color:var(--accent);font-weight:600}
.nav button:disabled{opacity:.4;cursor:default}
.stage{background:var(--stage);border-radius:10px;min-height:420px;padding:18px;display:flex;
align-items:center;justify-content:center;position:relative;overflow:hidden}
.snap{position:relative;max-width:100%;line-height:0}
.snap img{max-width:100%;max-height:72vh;border-radius:6px;box-shadow:0 10px 30px rgba(0,0,0,.4)}
.hot{position:absolute;outline:3px solid var(--hot);outline-offset:2px;border-radius:4px;cursor:pointer;
animation:pulse 1.4s infinite}
.hot:focus-visible{outline-width:4px}
@keyframes pulse{0%,100%{box-shadow:0 0 0 0 rgba(232,89,12,.7)}50%{box-shadow:0 0 0 8px rgba(232,89,12,0)}}
@media (prefers-reduced-motion:reduce){.hot{animation:none}}
.panel{color:#e8e6e3;text-align:center;max-width:420px}
.panel .big{font-size:22px;font-weight:600;margin-bottom:14px}
.wbtn{display:inline-block;padding:8px 18px;border-radius:6px;background:var(--hot);color:#fff;font-weight:600;
cursor:pointer;border:0;font:inherit}
body.shot{background:var(--stage)}
body.shot header,body.shot .card{display:none}
body.shot main{display:block;padding:0;max-width:none}
body.shot .stage{border-radius:0;min-height:100vh}
body.shot .hot{animation:none}
"""

_ENGINE = """
const $ = id => document.getElementById(id);
const phases = [...new Set(steps.map(s => s.phase))];
const q = new URLSearchParams(location.search), shot = q.get('shot');
let i = 0;
const esc = t => String(t).replace(/[&<>"]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));
function screen(s, k) {
  if (s.img) {
    const hot = s.box
      ? `<span class="hot" data-next tabindex="0" role="button" aria-label="Next step" style="left:${s.box[0]}%;top:${s.box[1]}%;width:${s.box[2]}%;height:${s.box[3]}%"></span>`
      : '';
    return `<div class="snap"><img src="${s.img}" alt="${esc(s.title)}">${hot}</div>`;
  }
  if (k === steps.length - 1)
    return `<div class="panel"><div class="big">Done</div><button class="wbtn" data-restart>Start over</button></div>`;
  return `<div class="panel"><div class="big">${esc(s.title)}</div><button class="wbtn" data-next>Start</button></div>`;
}
function render() {
  const s = steps[i];
  $('stepno').textContent = i === 0 || i === steps.length - 1
    ? s.phase : `Step ${i} of ${steps.length - 2} · ${s.phase}`;
  $('title').textContent = s.title;
  $('text').innerHTML = s.text;
  $('stage').innerHTML = screen(s, i);
  $('bar').style.width = `${(i / (steps.length - 1)) * 100}%`;
  const pb = document.querySelector('.progress');
  pb.setAttribute('aria-valuemin', 0); pb.setAttribute('aria-valuemax', steps.length - 1); pb.setAttribute('aria-valuenow', i);
  $('phases').innerHTML = phases.length < 2 ? '' : phases.map(p =>
    `<button type="button" class="phase${p === s.phase ? ' on' : ''}" data-phase="${esc(p)}">${esc(p)}</button>`).join('');
  $('back').disabled = i === 0;
  $('next').disabled = i === steps.length - 1;
  if (shot === null) try { localStorage.setItem(KEY, String(i)); } catch (e) {}
}
const go = d => { i = Math.max(0, Math.min(steps.length - 1, i + d)); render(); };
$('next').onclick = () => go(1);
$('back').onclick = () => go(-1);
document.addEventListener('click', e => {
  const ph = e.target.closest('[data-phase]');
  if (ph) { const k = steps.findIndex(s => s.id && s.phase === ph.dataset.phase); if (k >= 0) { i = k; render(); } return; }
  if (e.target.closest('[data-next]')) go(1);
  else if (e.target.closest('[data-restart]')) { i = 0; render(); }
});
document.addEventListener('keydown', e => {
  if ((e.key === 'Enter' || e.key === ' ') && e.target.closest && e.target.closest('.hot')) { e.preventDefault(); go(1); return; }
  if (e.key === 'ArrowRight') go(1);
  if (e.key === 'ArrowLeft') go(-1);
});
if (shot !== null) { document.body.classList.add('shot'); i = Math.max(0, Math.min(steps.length - 1, +shot || 0)); }
else try { const saved = +localStorage.getItem(KEY); if (saved > 0 && saved < steps.length - 1) i = saved; } catch (e) {}
render();
"""


def _script_json(value):
    """JSON safe to embed in an inline <script>: every '<' (and '>' and '&')
    is \\u-escaped, so no '</script', '<!--' or '<script' can reach the HTML
    parser from step text or a title. JSON.parse-equivalent, same values."""
    return json.dumps(value).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


def render_walkthrough(
    manifest,
    step_results,
    annotated_paths,
    narrative_text=None,
    phases=None,
    omitted_screenshots=(),
    screenshot_dir=None,
):
    """Returns one complete, self-contained HTML document string."""
    title = manifest.session.title or manifest.session.id
    steps = walkthrough_steps(
        manifest,
        step_results,
        annotated_paths,
        narrative_text=narrative_text,
        phases=phases if is_grouped(phases) else None,
        omitted_screenshots=omitted_screenshots,
        screenshot_dir=screenshot_dir,
    )
    t = html.escape(title)
    return (
        '<!doctype html>\n<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{t}</title><style>{_STYLE}</style></head><body>"
        f"<header><h1>{t}</h1>"
        '<div class="sub">Interactive walkthrough. Click the highlighted control on each '
        "screenshot, or use <kbd>Next</kbd> and <kbd>&rarr;</kbd>.</div>"
        '<div class="progress" role="progressbar" aria-label="Walkthrough progress">'
        '<div id="bar"></div></div><div class="phases" id="phases"></div></header>'
        '<main><section class="card" aria-live="polite"><div class="stepno" id="stepno"></div>'
        '<h2 id="title"></h2><div id="text"></div><div class="nav">'
        '<button id="back">&larr; Back</button><button id="next" class="primary">Next &rarr;</button>'
        '</div></section><section class="stage" id="stage"></section></main>'
        f"<script>\nconst steps = {_script_json(steps)};\n"
        f"const KEY = {_script_json('sopforge-walk-' + manifest.session.id)};\n"
        f"{_ENGINE}</script></body></html>"
    )
