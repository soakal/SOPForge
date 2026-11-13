"""Click-through walkthrough export (Phase 6d), SOP Factory's sop-walkthrough
ported to captured sessions: one self-contained HTML page where the reader
clicks the highlighted control on each real screenshot to move forward.

Built from the same step_results, phases and annotated screenshots as every
other format, so the walkthrough and the document always match. SOP Factory
measures each hotspot by hand; here it comes from the manifest: the clicked
element's bounding_rect, mapped into the annotated crop with the same
compute_crop_box() call that produced the crop. The engine (navigation,
keyboard, clickable phase chips, ?shot=N single-step mode) follows SOP
Factory's walkthrough_template.html; no network references at all.

Phase 7 authoring (authoring.py) adds: copyable commands, bullets, warnings
and tables on a step's card; authored blocks as their own entries (id None)
with a mock terminal/note screen; reader-editable {{name}} parameters,
substituted live in the browser; and alternative paths, each showing only
its own entries. Steps never change -- blocks are extra entries around them."""

import base64
import html
import json
import re
import zipfile
from functools import lru_cache
from pathlib import Path

from PIL import Image

from pipeline import authoring as auth
from pipeline.annotate import compute_crop_box
from pipeline.assembler import is_grouped, phase_starts, resolve_phases, step_heading
from pipeline.docx_assembler import sop_factory_2_dir
from pipeline.export_html import _data_uri
from pipeline.photo_build import is_synthetic_step
from pipeline.render import narrative_html_blocks
from pipeline.review_notes import step_review_notes

# Hotspot size when the element has no usable rect: a square around the
# click point, as a share of the crop's width.
_CLICK_HOTSPOT_FRACTION = 0.06

# Screenshots from these get a browser-window frame (SOP Factory's browserShot).
_BROWSERS = {"chrome.exe", "msedge.exe", "firefox.exe", "brave.exe"}


@lru_cache(maxsize=4)
def _logo_uri(template):
    try:
        with zipfile.ZipFile(template) as z:
            rels = z.read("word/_rels/header1.xml.rels").decode("utf-8", "replace")
            m = re.search(r'Target="(media/[^"]+\.(png|jpe?g|gif))"', rels, re.IGNORECASE)
            if not m:
                return None
            data = z.read("word/" + m.group(1))
    except (OSError, KeyError, zipfile.BadZipFile):
        return None
    ext = m.group(2).lower()
    mime = "jpeg" if ext in ("jpg", "jpeg") else ext
    return f"data:image/{mime};base64,{base64.b64encode(data).decode('ascii')}"


def logo_data_uri():
    """The VRSI header logo from the vendored SOP template, as a data URI;
    None when the template or its header image is missing."""
    return _logo_uri(str(sop_factory_2_dir() / "SOP_TEMPLATE_WITH_PHOTOS.docx"))


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


def _inline(text):
    """**bold** / `code` markup as HTML, every piece escaped first."""
    out = []
    for piece, style in auth.inline_parts(text):
        piece = html.escape(piece)
        if style == "bold":
            piece = f"<strong>{piece}</strong>"
        elif style == "code":
            piece = f"<code>{piece}</code>"
        out.append(piece)
    return "".join(out)


def _paragraphs(text):
    return "".join(
        "<p>" + _inline(para.strip()).replace("\n", "<br>") + "</p>"
        for para in re.split(r"\n\s*\n", text or "")
        if para.strip()
    )


def _authored_html(extra):
    """A step extra's or a block's card content. {{name}} placeholders stay
    in (inside escaped HTML); the page substitutes the reader's values."""
    if not extra:
        return ""
    parts = [_paragraphs(extra.get("text"))]
    if extra["bullets"]:
        parts.append("<ul>" + "".join(f"<li>{_inline(b)}</li>" for b in extra["bullets"]) + "</ul>")
    parts += [
        f'<div class="cmd"><code>{html.escape(c)}</code>'
        '<button type="button" data-copy>Copy</button></div>'
        for c in extra["commands"]
    ]
    if extra["warning"]:
        parts.append(f'<div class="warn">{_inline(extra["warning"])}</div>')
    table = extra["table"]
    if table:
        head = "".join(f"<th>{html.escape(h)}</th>" for h in table["headers"])
        rows = "".join(
            "<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in row) + "</tr>"
            for row in table["rows"]
        )
        parts.append(f'<table class="tbl"><tr>{head}</tr>{rows}</table>')
    return "".join(parts)


def _block_stage(block, title):
    """The mock screen for an authored block (it has no screenshot)."""
    nxt = '<button type="button" class="wbtn" data-next>Next</button>'
    prompt = '<span class="prompt">PS C:\\&gt;</span> '
    if block["screen"] == "terminal" and block["commands"]:
        lines = "\n".join(
            f'{prompt}<span class="typed">{html.escape(c)}</span>' for c in block["commands"]
        )
        return (
            '<div class="termwrap"><div class="win"><div class="bar"><span>Windows PowerShell</span>'
            f'<span>&#8212; &#9633; &#10005;</span></div><div class="term">{lines}\n{prompt}'
            f'<span class="cursor">&nbsp;</span></div></div><p>{nxt}</p></div>'
        )
    placeholder = (
        f'<div class="ph">{html.escape(block["placeholder"])}</div>' if block["placeholder"] else ""
    )
    return (
        f'<div class="panel"><div class="big">{html.escape(title)}</div>'
        f"{_paragraphs(block['text'])}{placeholder}{nxt}</div>"
    )


def _entry(
    id_, phase, title, text, img=None, box=None, atext="", stage=None, path="", tag="", browser=None
):
    """One walkthrough entry. text: HTML shown as-is; atext/stage: authored
    HTML, {{name}} substituted in the page; path: "" = on every path;
    browser: the window title for a browser-frame mock, else None."""
    return {
        "id": id_,
        "phase": phase,
        "title": title,
        "text": text,
        "img": img,
        "box": box,
        "atext": atext,
        "stage": stage,
        "path": path,
        "tag": tag,
        "browser": browser,
    }


def _intro_controls(a):
    out = ""
    if a["params"]:
        out += (
            '<div class="params">'
            + "".join(
                f'<label>{html.escape(p["label"])}<input type="text" '
                f'data-param="{html.escape(p["name"])}" value="{html.escape(p["default"])}"></label>'
                for p in a["params"]
            )
            + "</div>"
        )
    if a["paths"]:
        out += (
            '<div class="paths"><span>Choose your path:</span>'
            + "".join(
                f'<button type="button" class="pathbtn" data-path="{html.escape(p)}">'
                f"{html.escape(p)}</button>"
                for p in a["paths"]
            )
            + "</div>"
        )
    return out


def walkthrough_steps(
    manifest,
    step_results,
    annotated_paths,
    narrative_text=None,
    phases=None,
    omitted_screenshots=(),
    screenshot_dir=None,
    authoring=None,
):
    """The page's step list: an intro, one entry per manifest step (in order,
    with its id), and a closing entry -- plus one entry (id None) per
    authored block, right after its `after` step ("" = right after the
    intro). A step whose screenshot was omitted as an exact repeat reuses the
    previous step's picture."""
    a = authoring or auth.EMPTY
    title = manifest.session.title or manifest.session.id
    phase_of = {}
    for phase in resolve_phases(manifest, phases):
        for step_id in phase.step_ids:
            phase_of[step_id] = phase.name
    starts = phase_starts(manifest, phases)
    first_phase = next(iter(starts.values()), "Procedure")

    intro = "".join(narrative_html_blocks(narrative_text)) if narrative_text else ""
    intro += f"<p>{len(manifest.steps)} steps. Click the highlighted control to go on.</p>"
    entries = [_entry(None, first_phase, title, intro + _intro_controls(a))]
    # An authored block's `section` relabels the phase chip until the next
    # manifest phase starts.
    state = {"section": None, "phase": first_phase}

    def add_blocks(after):
        for block in auth.blocks_after(a, after):
            if block["section"]:
                state["section"] = block["section"]
            heading = block["heading"] or block["section"] or "Note"
            entries.append(
                _entry(
                    None,
                    state["section"] or state["phase"],
                    heading,
                    "",
                    atext=_authored_html(block),
                    stage=_block_stage(block, heading),
                    path=block["path"],
                    tag=auth.tag_of(block),
                )
            )

    add_blocks("")
    last_img = last_box = None
    for n, (step, result, shot) in enumerate(
        zip(manifest.steps, step_results, annotated_paths, strict=True), start=1
    ):
        if step.id in starts:
            state["section"] = None
        state["phase"] = phase_of[step.id]
        if step.id in omitted_screenshots and last_img:
            img, box = last_img, last_box
        elif shot is not None and Path(shot).exists():
            img, box = _data_uri(shot), hotspot_box(step, _raw_size(screenshot_dir, step))
        else:
            img = box = None
        last_img, last_box = img, box
        extra = auth.step_extra(a, step.id)
        heading = step_heading(n, step, result)
        entries.append(
            _entry(
                step.id,
                state["section"] or state["phase"],
                # The card already says "Step N of M"; drop the heading's own
                # "Step N — " prefix so it isn't said twice.
                heading.partition(" — ")[2] or heading,
                _step_html(result, step_review_notes(step)),
                img=img,
                box=box,
                atext=_authored_html(extra),
                path=(extra or {}).get("path", ""),
                tag=auth.tag_of(extra),
                browser=step.window.title if step.window.process.lower() in _BROWSERS else None,
            )
        )
        add_blocks(step.id)
    entries.append(_entry(None, entries[-1]["phase"], "Done", "<p>Procedure complete.</p>"))
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
header{padding:12px 24px}
.brand{display:inline-block;background:#fff;border-radius:8px;padding:4px 8px;margin-bottom:8px}
.brand img{display:block;height:40px;width:auto}
h1{font-size:20px;margin:0 0 4px}
.sub{color:var(--muted);font-size:13px}
kbd,code{font:12px "IBM Plex Mono",Consolas,monospace}
kbd{border:1px solid var(--line);border-radius:4px;padding:0 4px}
.progress{height:6px;background:var(--line);border-radius:3px;margin-top:12px;overflow:hidden}
.progress>div{height:100%;background:var(--accent);transition:width .25s}
.phases{display:flex;gap:6px;flex-wrap:wrap;margin-top:10px}
.phase{font:inherit;font-size:12px;padding:2px 10px;border-radius:99px;border:1px solid var(--line);
background:none;color:var(--muted);cursor:pointer}
.phase.on{background:var(--accent);color:var(--accent-ink);border-color:var(--accent)}
main{padding:0 24px 24px;display:grid;grid-template-columns:minmax(320px,26%) 1fr;gap:16px}
main>*{min-width:0}
@media (max-width:820px){header{padding:12px}main{grid-template-columns:1fr;padding:0 12px 16px}}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:16px}
.stepno{font:12px "IBM Plex Mono",Consolas,monospace;color:var(--muted);text-transform:uppercase;letter-spacing:.05em}
.card h2{font-size:18px;margin:4px 0 10px}
.card p{margin:0 0 10px}
.card ul{margin:0 0 10px;padding-left:20px}
.tag{display:inline-block;font-size:11px;font-weight:600;padding:1px 8px;border-radius:99px;
background:#b91c1c;color:#fff;margin-bottom:6px}
.tag[hidden]{display:none}
.narration{color:var(--muted);font-style:italic}
.warn{background:var(--warn-bg);color:var(--warn-ink);border-radius:8px;padding:10px 12px;font-size:14px;margin:10px 0;font-weight:600}
.cmd{display:flex;gap:6px;align-items:stretch;margin:8px 0}
.cmd code{flex:1;background:var(--bg);border:1px solid var(--line);border-radius:6px;padding:6px 8px;
font-size:13px;white-space:pre-wrap;overflow-wrap:anywhere}
.cmd button,.pathbtn{border:1px solid var(--line);background:var(--panel);color:var(--ink);border-radius:6px;
font:inherit;font-size:12px;cursor:pointer;padding:0 10px}
.tbl{border-collapse:collapse;font-size:13px;margin:8px 0;width:100%}
.tbl th,.tbl td{border:1px solid var(--line);padding:4px 8px;text-align:left;vertical-align:top}
.params{display:grid;gap:8px;margin:10px 0}
.params label{display:grid;gap:2px;font-size:13px;color:var(--muted)}
.params input{font:inherit;padding:6px 8px;border:1px solid var(--line);border-radius:6px;background:var(--bg);color:var(--ink)}
.paths{display:flex;gap:6px;flex-wrap:wrap;align-items:center;margin:10px 0;font-size:13px}
.pathbtn{padding:4px 12px}
.pathbtn.on{background:var(--accent);color:var(--accent-ink);border-color:var(--accent)}
.nav{display:flex;gap:8px;margin-top:14px}
.nav button{flex:1;padding:9px;border-radius:8px;font:inherit;font-size:14px;cursor:pointer;
border:1px solid var(--line);background:var(--panel);color:var(--ink)}
.nav button.primary{background:var(--accent);color:var(--accent-ink);border-color:var(--accent);font-weight:600}
.nav button:disabled{opacity:.4;cursor:default}
.stage{background:var(--stage);border-radius:10px;min-height:max(420px,calc(100vh - 240px));padding:18px;display:flex;
align-items:center;justify-content:center;position:relative;overflow:hidden}
@media (max-width:820px){.stage{min-height:300px;padding:10px}}
.snap{position:relative;max-width:100%;line-height:0}
.snap img{max-width:100%;max-height:72vh;border-radius:6px;box-shadow:0 10px 30px rgba(0,0,0,.4)}
.hot{position:absolute;outline:3px solid var(--hot);outline-offset:2px;border-radius:4px;cursor:pointer;
animation:pulse 1.4s infinite}
.hot:focus-visible{outline-width:4px}
@keyframes pulse{0%,100%{box-shadow:0 0 0 0 rgba(232,89,12,.7)}50%{box-shadow:0 0 0 8px rgba(232,89,12,0)}}
@media (prefers-reduced-motion:reduce){.hot,.cursor{animation:none}}
.browser{width:100%;background:#fff;border-radius:8px;color:#1a1a1a;font:12px "Segoe UI",sans-serif;
box-shadow:0 0 0 1px rgba(0,0,0,.25),0 18px 40px rgba(0,0,0,.45)}
.browser .tabs{display:flex;align-items:flex-end;gap:8px;background:#dfe3e8;padding:6px 10px 0;border-radius:8px 8px 0 0}
.browser .tab{background:#fff;border-radius:8px 8px 0 0;padding:6px 12px;min-width:120px;max-width:220px;
white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.browser .ctl{margin-left:auto;align-self:center;letter-spacing:14px;color:#555;padding-bottom:4px}
.browser .addr{display:flex;align-items:center;gap:10px;padding:5px 10px;border-bottom:1px solid #d6d9de;color:#666}
.browser .url{flex:1;background:#f1f3f4;border-radius:99px;padding:4px 12px;color:#1a1a1a;
white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.browser .snap{max-width:none}
.browser .snap img{display:block;width:100%;max-height:none;border-radius:0 0 8px 8px;box-shadow:none}
.termwrap{width:100%;max-width:760px;text-align:center}
.win{background:#fff;color:#1a1a1a;border-radius:8px;box-shadow:0 10px 30px rgba(0,0,0,.4);overflow:hidden;
font-size:13px;text-align:left}
.bar{background:#f0f0f0;padding:6px 10px;display:flex;justify-content:space-between;font-size:12px}
.term{background:#012456;color:#eee;font:13px/1.45 Consolas,monospace;padding:10px 12px;min-height:260px;
white-space:pre-wrap;overflow-wrap:anywhere}
.term .typed{color:#fff;font-weight:600}
.term .prompt{color:#ccc}
.cursor{display:inline-block;width:8px;background:#ccc;animation:blink 1s steps(1) infinite}
@keyframes blink{50%{opacity:0}}
.panel{color:#e8e6e3;text-align:center;max-width:520px}
.panel .big{font-size:22px;font-weight:600;margin-bottom:14px}
.panel p{margin:0 0 10px}
.panel .ph{border:2px dashed #5f5d59;border-radius:8px;padding:24px;margin:0 0 14px;color:#a8a49e}
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
const q = new URLSearchParams(location.search), shot = q.get('shot');
let i = 0;
const esc = t => String(t).replace(/[&<>"]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));
const vals = {};
PARAMS.forEach(p => { vals[p.name] = p.default; });
try {
  const saved = JSON.parse(localStorage.getItem(KEY + '-params')) || {};
  PARAMS.forEach(p => { if (typeof saved[p.name] === 'string') vals[p.name] = saved[p.name]; });
} catch (e) {}
const fill = (t, asHtml) => String(t).replace(/\\{\\{\\s*([A-Za-z_][A-Za-z0-9_]*)\\s*\\}\\}/g, (m, n) => {
  const p = PARAMS.find(p => p.name === n);
  if (!p) return m;
  const v = vals[n] || `<${p.label}>`;
  return asHtml ? esc(v) : v;
});
let path = PATHS.includes(q.get('path')) ? q.get('path') : (PATHS[0] || '');
const vis = () => steps.filter(s => !s.path || s.path === path);
function screen(s, k, last) {
  if (s.stage) return fill(s.stage, true);
  if (s.img) {
    const hot = s.box
      ? `<span class="hot" data-next tabindex="0" role="button" aria-label="Next step" style="left:${s.box[0]}%;top:${s.box[1]}%;width:${s.box[2]}%;height:${s.box[3]}%"></span>`
      : '';
    const snap = `<div class="snap"><img src="${s.img}" alt="${esc(s.title)}">${hot}</div>`;
    if (!s.browser) return snap;
    return `<div class="browser"><div class="tabs"><span class="tab">${esc(s.browser)}</span><span class="ctl">&#8212;&#9744;&#10005;</span></div>`
      + `<div class="addr">&#8592; &#8594; &#8635;<span class="url">${esc(s.browser)}</span>&#8943;</div>${snap}</div>`;
  }
  if (k === last)
    return `<div class="panel"><div class="big">Done</div><button class="wbtn" data-restart>Start over</button></div>`;
  return `<div class="panel"><div class="big">${esc(fill(s.title))}</div><button class="wbtn" data-next>Start</button></div>`;
}
function render() {
  const V = vis(), last = V.length - 1;
  i = Math.max(0, Math.min(last, i));
  const s = V[i];
  $('stepno').textContent = i === 0 || i === last
    ? s.phase : `Step ${i} of ${last - 1} · ${s.phase}`;
  $('title').textContent = s.id ? s.title : fill(s.title);
  $('tag').textContent = s.tag || '';
  $('tag').hidden = !s.tag;
  $('text').innerHTML = s.text + fill(s.atext || '', true);
  document.querySelectorAll('[data-param]').forEach(el => { el.value = vals[el.dataset.param] || ''; });
  document.querySelectorAll('[data-path]').forEach(el => el.classList.toggle('on', el.dataset.path === path));
  $('stage').innerHTML = screen(s, i, last);
  $('bar').style.width = `${last ? (i / last) * 100 : 100}%`;
  const pb = document.querySelector('.progress');
  pb.setAttribute('aria-valuemin', 0); pb.setAttribute('aria-valuemax', last); pb.setAttribute('aria-valuenow', i);
  const phases = [...new Set(V.map(s => s.phase))];
  $('phases').innerHTML = phases.length < 2 ? '' : phases.map(p =>
    `<button type="button" class="phase${p === s.phase ? ' on' : ''}" data-phase="${esc(p)}">${esc(p)}</button>`).join('');
  $('back').disabled = i === 0;
  $('next').disabled = i === last;
  if (shot === null) try {
    localStorage.setItem(KEY, String(i));
    if (PATHS.length) localStorage.setItem(KEY + '-path', path);
  } catch (e) {}
}
const go = d => { i += d; render(); };
function copy(btn) {
  const txt = btn.previousElementSibling.textContent;
  const done = msg => { btn.textContent = msg; setTimeout(() => { btn.textContent = 'Copy'; }, 1400); };
  const fallback = () => {
    const ta = document.createElement('textarea');
    ta.value = txt; ta.setAttribute('readonly', ''); ta.style.position = 'fixed'; ta.style.opacity = '0';
    document.body.appendChild(ta); ta.select();
    let ok = false;
    try { ok = document.execCommand('copy'); } catch (e) {}
    ta.remove();
    done(ok ? 'Copied' : 'Select + Ctrl+C');
  };
  if (navigator.clipboard && window.isSecureContext)
    navigator.clipboard.writeText(txt).then(() => done('Copied'), fallback);
  else fallback();
}
$('next').onclick = () => go(1);
$('back').onclick = () => go(-1);
document.addEventListener('click', e => {
  const t = e.target;
  if (!t.closest) return;
  const cp = t.closest('[data-copy]');
  if (cp) { copy(cp); return; }
  const pt = t.closest('[data-path]');
  if (pt) { path = pt.dataset.path; render(); return; }
  const ph = t.closest('[data-phase]');
  if (ph) {
    const V = vis(), k = V.findIndex((s, j) => j > 0 && j < V.length - 1 && s.phase === ph.dataset.phase);
    if (k >= 0) { i = k; render(); }
    return;
  }
  if (t.closest('[data-next]')) go(1);
  else if (t.closest('[data-restart]')) { i = 0; render(); }
});
document.addEventListener('input', e => {
  const el = e.target.closest && e.target.closest('[data-param]');
  if (!el) return;
  vals[el.dataset.param] = el.value;
  try { localStorage.setItem(KEY + '-params', JSON.stringify(vals)); } catch (err) {}
});
document.addEventListener('keydown', e => {
  const t = e.target.closest ? e.target : document.body;
  if ((e.key === 'Enter' || e.key === ' ') && t.closest('.hot')) { e.preventDefault(); go(1); return; }
  if (t.closest('input,textarea,select')) return;
  if (e.key === 'ArrowRight' || (e.key === 'Enter' && !t.closest('button'))) go(1);
  if (e.key === 'ArrowLeft') go(-1);
});
if (shot !== null) { document.body.classList.add('shot'); i = +shot || 0; }
else try {
  const sp = localStorage.getItem(KEY + '-path');
  if (!q.get('path') && PATHS.includes(sp)) path = sp;
  const saved = +localStorage.getItem(KEY);
  if (saved > 0 && saved < vis().length - 1) i = saved;
} catch (e) {}
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
    authoring=None,
    doc_no=None,
    revision=None,
    version_stamp=None,
):
    """Returns one complete, self-contained HTML document string."""
    a = authoring or auth.EMPTY
    title = manifest.session.title or manifest.session.id
    steps = walkthrough_steps(
        manifest,
        step_results,
        annotated_paths,
        narrative_text=narrative_text,
        phases=phases if is_grouped(phases) else None,
        omitted_screenshots=omitted_screenshots,
        screenshot_dir=screenshot_dir,
        authoring=a,
    )
    t = html.escape(title)
    logo = logo_data_uri()
    brand = f'<div class="brand"><img src="{logo}" alt="VRSI"></div>' if logo else ""
    meta = " · ".join(
        part for part in (doc_no, f"Rev {revision}" if revision else None, version_stamp) if part
    )
    meta_html = f'<div class="sub">{html.escape(meta)}</div>' if meta else ""
    return (
        '<!doctype html>\n<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{t}</title><style>{_STYLE}</style></head><body>"
        f"<header>{brand}<h1>{t}</h1>{meta_html}"
        '<div class="sub">Interactive walkthrough. Click the highlighted control on each '
        "screenshot, or use <kbd>Next</kbd>, <kbd>&rarr;</kbd> or <kbd>Enter</kbd>.</div>"
        '<div class="progress" role="progressbar" aria-label="Walkthrough progress">'
        '<div id="bar"></div></div><div class="phases" id="phases"></div></header>'
        '<main><section class="card" aria-live="polite"><div class="stepno" id="stepno"></div>'
        '<h2 id="title"></h2><span class="tag" id="tag" hidden></span><div id="text"></div>'
        '<div class="nav"><button id="back">&larr; Back</button>'
        '<button id="next" class="primary">Next &rarr;</button>'
        '</div></section><section class="stage" id="stage"></section></main>'
        f"<script>\nconst steps = {_script_json(steps)};\n"
        f"const KEY = {_script_json('sopforge-walk-' + manifest.session.id)};\n"
        f"const PARAMS = {_script_json(a['params'])};\n"
        f"const PATHS = {_script_json(a['paths'])};\n"
        f"{_ENGINE}</script></body></html>"
    )
