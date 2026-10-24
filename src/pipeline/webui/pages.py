"""Review web UI pages ("Forge", Phase 6h): library table + drop zone,
per-session processing/steps-review/review pages, and the configuration
panel. Plain server-rendered HTML with one embedded stylesheet and a few
small inline scripts -- no build step, no Node, no CDN. The only external
asset is IBM Plex (OFL), vendored under assets/fonts/ibm-plex/ and served by
this same server (/ui/assets/fonts/...), so every page renders identically
offline. A shared _shell() wraps every page in the same graphite sidebar
chrome; light/dark follow the OS (prefers-color-scheme)."""

import html

from pipeline import __version__

# The vendored Plex faces, as (family, file stem, weight, style). server.py
# serves exactly these files (FONT_FILES) and sopforge-server.spec bundles
# them -- one list so the three can't drift apart.
_FONT_FACES = [
    ("IBM Plex Sans", "IBMPlexSans-Regular", 400, "normal"),
    ("IBM Plex Sans", "IBMPlexSans-Italic", 400, "italic"),
    ("IBM Plex Sans", "IBMPlexSans-Medium", 500, "normal"),
    ("IBM Plex Sans", "IBMPlexSans-SemiBold", 600, "normal"),
    ("IBM Plex Sans", "IBMPlexSans-Bold", 700, "normal"),
    ("IBM Plex Mono", "IBMPlexMono-Regular", 400, "normal"),
    ("IBM Plex Mono", "IBMPlexMono-Medium", 500, "normal"),
    ("IBM Plex Mono", "IBMPlexMono-SemiBold", 600, "normal"),
]
FONT_FILES = frozenset(f"{stem}.woff2" for _family, stem, _w, _s in _FONT_FACES)
FONT_URL_PREFIX = "/ui/assets/fonts/"

_FONT_CSS = "".join(
    f'@font-face{{font-family:"{family}";font-weight:{weight};font-style:{style};'
    f'font-display:swap;src:url({FONT_URL_PREFIX}{stem}.woff2) format("woff2")}}'
    for family, stem, weight, style in _FONT_FACES
)

# Colour tokens. Every text/background pair used below clears WCAG AA
# (4.5:1 body text) in both themes -- notably buttons sit on --btn
# (#c2410c, 5.2:1 under white text), not the brighter mockup ember
# (#e8590c, 3.6:1), which is kept for non-text accents only.
_STYLE = (
    _FONT_CSS
    + """
:root{color-scheme:light;
--ink:#16191d;--ink-2:#22272e;--ink-3:#2d333b;--nav-fg:#c9cdd3;--nav-muted:#8b929c;
--bg:#f4f1ec;--card:#fffdf9;--card-2:#faf7f2;--hover:#fcf8f2;--border:#e3ddd3;--border-2:#d3cbbe;
--fg:#1c1f23;--muted:#64625e;
--ember:#e8590c;--link:#b13a0a;--btn:#c2410c;--btn-hover:#9a3412;--soft:#fdeee4;--soft-fg:#b13a0a;
--ok:#276749;--ok-bg:#e3f2e9;--warn:#8a5a0b;--warn-bg:#fbf0d9;--bad:#b42323;--bad-bg:#fbe7e7;
--run:#3d5674;--run-bg:#e6edf5;
--shadow:0 1px 2px rgba(60,40,20,.06);--radius:10px;
--mono:"IBM Plex Mono",ui-monospace,Consolas,monospace;
--sans:"IBM Plex Sans","Segoe UI",system-ui,sans-serif}
@media(prefers-color-scheme:dark){:root{color-scheme:dark;
--ink:#0d0f12;--ink-2:#181b20;--ink-3:#23272d;--nav-fg:#c4c9d0;--nav-muted:#8b929c;
--bg:#131518;--card:#1b1e22;--card-2:#202328;--hover:#212429;--border:#2c3036;--border-2:#3d424a;
--fg:#e9e6e1;--muted:#a19d96;
--ember:#f97316;--link:#f2894a;--btn:#c2410c;--btn-hover:#d9530f;--soft:#3a2216;--soft-fg:#f6a26e;
--ok:#6fcf97;--ok-bg:#16301f;--warn:#f0b65a;--warn-bg:#352812;--bad:#f28b82;--bad-bg:#3a1b1b;
--run:#9db7d9;--run-bg:#1d2836;--shadow:none}}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.55 var(--sans)}
a{color:var(--link);text-decoration:none}
a:hover{text-decoration:underline}
:focus-visible{outline:2px solid var(--ember);outline-offset:2px;border-radius:4px}
code,kbd{font-family:var(--mono);font-size:.92em;background:var(--card-2);border:1px solid var(--border);
border-radius:4px;padding:0 4px}
.app{display:grid;grid-template-columns:236px minmax(0,1fr);min-height:100vh;
background:linear-gradient(to right,var(--ink) 236px,transparent 236px)}
.side{background:var(--ink);color:var(--nav-fg);padding:20px 14px 16px;display:flex;flex-direction:column;
gap:2px;position:sticky;top:0;height:100vh}
.logo{display:flex;align-items:center;gap:10px;padding:2px 8px 22px;color:#fff;font-weight:600;
font-size:16px;letter-spacing:.2px}
.logo:hover{text-decoration:none}
.mark{width:28px;height:28px;flex:none;border-radius:7px;background:linear-gradient(135deg,#f97316,#c2410c);
position:relative;box-shadow:inset 0 -3px 0 rgba(0,0,0,.25)}
.mark::after{content:"";position:absolute;left:7px;right:7px;top:8px;height:3px;background:#fff;
border-radius:2px;box-shadow:0 5px 0 #fff,0 10px 0 rgba(255,255,255,.6)}
.side nav{display:flex;flex-direction:column;gap:2px}
.side nav a{display:flex;align-items:center;gap:10px;padding:8px 10px;border-radius:7px;color:var(--nav-fg);
font-size:13.5px}
.side nav a:hover{background:var(--ink-2);text-decoration:none;color:#fff}
.side nav a[aria-current=page]{background:var(--ink-3);color:#fff;box-shadow:inset 3px 0 0 var(--ember)}
.side nav svg{width:16px;height:16px;flex:none;opacity:.85}
.side .count{margin-left:auto;font:600 11px/18px var(--mono);background:var(--btn);color:#fff;
border-radius:5px;padding:0 6px;min-width:20px;text-align:center}
.side .sec{font:500 10.5px var(--mono);letter-spacing:1.2px;text-transform:uppercase;color:var(--nav-muted);
padding:18px 10px 6px}
.side .foot{margin-top:auto;font:11px/1.7 var(--mono);color:var(--nav-muted);padding:10px}
.side .foot b{font-weight:500;color:var(--nav-fg)}
.dot{width:7px;height:7px;border-radius:50%;background:#8b929c;display:inline-block;margin-right:7px;
vertical-align:1px}
.dot.ok{background:#48bb78;box-shadow:0 0 0 3px rgba(72,187,120,.18)}
.dot.warn{background:#ecc94b;box-shadow:0 0 0 3px rgba(236,201,75,.18)}
.dot.error{background:#f56565;box-shadow:0 0 0 3px rgba(245,101,101,.2)}
main{padding:28px 40px 72px;max-width:1240px;width:100%}
.top{display:flex;align-items:flex-end;justify-content:space-between;gap:16px 20px;flex-wrap:wrap;
margin-bottom:22px}
.top>div:first-child{min-width:0}
.eyebrow{font:500 11px var(--mono);letter-spacing:1.4px;text-transform:uppercase;color:var(--link)}
.eyebrow a{color:inherit}
h1{font-size:26px;line-height:1.25;margin:4px 0 0;font-weight:600;letter-spacing:-.3px;overflow-wrap:anywhere}
h2{font-size:16px;font-weight:600;margin:0 0 10px}
h3{font-size:14px;font-weight:600;margin:0}
.lede{color:var(--muted);margin:6px 0 0;max-width:72ch}
.muted,small{color:var(--muted)}
.mono{font-family:var(--mono)}
.toolbar{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
.card{background:var(--card);border:1px solid var(--border);border-radius:var(--radius);padding:18px 20px;
margin:0 0 16px;box-shadow:var(--shadow)}
.card>h2:first-child{margin-top:0}
.card.flat{box-shadow:none}
section[data-status]{border-left:4px solid var(--border-2)}
section[data-status="green"]{border-left-color:var(--ok)}
section[data-status="yellow"]{border-left-color:var(--warn)}
section[data-status="red"]{border-left-color:var(--bad)}
.note{border-left:4px solid var(--warn)}
.note.ok{border-left-color:var(--ok)}
.note.bad{border-left-color:var(--bad)}
button,.btn{font:inherit;font-weight:600;font-size:13.5px;line-height:20px;display:inline-flex;align-items:center;
gap:8px;padding:8px 16px;border:1px solid transparent;border-radius:7px;background:var(--btn);color:#fff;
cursor:pointer;text-decoration:none;white-space:nowrap}
button:hover,.btn:hover{background:var(--btn-hover);text-decoration:none}
button:disabled{opacity:.6;cursor:progress}
button.secondary,.btn.ghost{background:var(--card);color:var(--fg);border-color:var(--border-2)}
button.secondary:hover,.btn.ghost:hover{background:var(--card-2);border-color:var(--muted)}
button.danger{background:transparent;color:var(--bad);border-color:var(--border-2)}
button.danger:hover{background:var(--bad-bg);border-color:var(--bad)}
.actions{display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin:10px 0}
.actions form{margin:0}
.field{margin:0 0 14px}
.field:last-child{margin-bottom:0}
label{display:block;font-weight:600;font-size:13px;margin-bottom:6px}
label small{font-weight:400}
label.check{display:flex;gap:8px;align-items:center;font-weight:600}
label.check input{width:16px;height:16px;margin:0;accent-color:var(--btn)}
input[type=text],input[type=number],input[type=search],input[type=file],select,textarea{font:inherit;
padding:8px 11px;border:1px solid var(--border-2);border-radius:7px;background:var(--card);color:var(--fg);
width:100%;max-width:460px}
input[type=number]{width:5.5em}
textarea{min-height:6em;max-width:100%;resize:vertical;line-height:1.5}
::placeholder{color:var(--muted);opacity:1}
input[type=file]{padding:5px 6px;color:var(--muted);cursor:pointer}
input[type=file]::file-selector-button{font:inherit;font-weight:600;font-size:13px;margin-right:10px;
padding:5px 12px;border:1px solid var(--border-2);border-radius:6px;background:var(--card-2);color:var(--fg);
cursor:pointer}
input[type=file]::file-selector-button:hover{border-color:var(--muted)}
input:focus,select:focus,textarea:focus{outline:2px solid var(--ember);outline-offset:0;border-color:transparent}
.help{color:var(--muted);font-size:12.5px;margin:6px 0 0;max-width:80ch}
.pill{display:inline-flex;align-items:center;gap:6px;font-size:12px;font-weight:500;line-height:18px;
padding:2px 9px;border-radius:999px;white-space:nowrap;background:var(--card-2);color:var(--muted)}
.pill::before{content:"";width:6px;height:6px;border-radius:50%;background:currentColor;flex:none}
.pill.ok,.pill.ready{color:var(--ok);background:var(--ok-bg)}
.pill.warn,.pill.review,.pill.staged,.pill.edited{color:var(--warn);background:var(--warn-bg)}
.pill.run,.pill.generating,.pill.processing,.pill.queued{color:var(--run);background:var(--run-bg)}
.pill.bad,.pill.error{color:var(--bad);background:var(--bad-bg)}
.pill.edited::before{display:none}
.spin{display:inline-block;width:12px;height:12px;border:2px solid currentColor;border-right-color:transparent;
border-radius:50%;animation:s .8s linear infinite}
.pill .spin{margin-left:-2px}
.pill:has(.spin)::before{display:none}
@keyframes s{to{transform:rotate(360deg)}}
@media(prefers-reduced-motion:reduce){.spin{animation-duration:2.4s}}
progress{width:100%;height:10px;border:0;border-radius:6px;background:var(--card-2);accent-color:var(--btn);
margin:14px 0 6px;overflow:hidden}
progress::-webkit-progress-bar{background:var(--card-2)}
progress::-webkit-progress-value{background:var(--btn);border-radius:6px}
/* library */
.search{display:flex;gap:8px;align-items:center;background:var(--card);border:1px solid var(--border-2);
border-radius:7px;padding:0 4px 0 12px;width:340px;max-width:100%}
.search:focus-within{outline:2px solid var(--ember)}
.search svg{width:15px;height:15px;color:var(--muted);flex:none}
.search input[type=text]{border:0;padding:8px 0;background:transparent;max-width:none}
.search input[type=text]:focus{outline:0}
.search button{padding:4px 10px;font-size:12.5px}
.stats{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin-bottom:20px}
.stat{background:var(--card);border:1px solid var(--border);border-radius:var(--radius);padding:14px 16px;
box-shadow:var(--shadow)}
.stat b{display:block;font:600 22px/1.3 var(--mono);letter-spacing:-.5px}
.stat span{color:var(--muted);font-size:12.5px}
.stat span i{font-style:normal;font-family:var(--mono)}
.drop{position:relative;border:1.5px dashed var(--border-2);border-radius:12px;
background:repeating-linear-gradient(-45deg,transparent 0 10px,rgba(232,89,12,.03) 10px 20px),var(--card);
padding:20px 22px;display:flex;align-items:center;gap:18px;flex-wrap:wrap;margin-bottom:24px;
transition:border-color .15s,background-color .15s}
.drop:target,.drop.over{border-color:var(--ember);background-color:var(--soft)}
.drop.over{border-style:solid}
.drop .ico{width:44px;height:44px;flex:none;border-radius:9px;background:var(--soft);display:grid;
place-items:center;color:var(--soft-fg)}
.drop .ico svg{width:22px;height:22px}
.drop .txt{flex:1 1 300px;min-width:0}
.drop h2{margin:0;font-size:15px}
.drop p{margin:2px 0 0;color:var(--muted);font-size:13px}
.drop .toolbar{margin-left:auto}
.plan{flex-basis:100%;border-top:1px solid var(--border);padding-top:16px;margin-top:2px}
.plan[hidden]{display:none}
.plan .kind{display:flex;flex-wrap:wrap;gap:8px 16px;align-items:center;margin-bottom:12px}
.plan ul{margin:0 0 12px;padding:0;list-style:none;display:flex;flex-wrap:wrap;gap:6px}
.plan li{font:12px var(--mono);border:1px solid var(--border);background:var(--card-2);border-radius:5px;
padding:2px 7px;color:var(--muted)}
.plan .err{color:var(--bad);font-weight:500;margin:8px 0 0}
.plan .err:empty{display:none}
.tablewrap{background:var(--card);border:1px solid var(--border);border-radius:var(--radius);overflow-x:auto;
box-shadow:var(--shadow)}
table{width:100%;border-collapse:collapse}
th{text-align:left;font:500 10.5px var(--mono);letter-spacing:1.1px;text-transform:uppercase;color:var(--muted);
padding:11px 16px;background:var(--card-2);border-bottom:1px solid var(--border);white-space:nowrap}
td{padding:12px 16px;border-bottom:1px solid var(--border);vertical-align:middle}
tbody tr:last-child td{border-bottom:0}
table.lib tbody tr:hover td{background:var(--hover)}
.proc{display:flex;align-items:center;gap:12px;min-width:240px}
.thumb{width:60px;height:38px;flex:none;border-radius:5px;border:1px solid var(--border);background:var(--card-2);
object-fit:cover;object-position:top left;display:block}
.thumb.none{display:grid;place-items:center;color:var(--muted)}
.thumb.none svg{width:18px;height:18px}
.proc a{font-weight:600;color:var(--fg);display:block;line-height:1.35}
.proc a:hover{color:var(--link)}
.proc small{font:11.5px var(--mono)}
td.num{font-family:var(--mono);color:var(--muted);white-space:nowrap}
.fmt{display:flex;gap:4px;flex-wrap:nowrap}
.fmt a,.fmt span{font:500 10.5px/16px var(--mono);padding:1px 6px;border:1px solid var(--border-2);
border-radius:4px;color:var(--muted)}
.fmt a:hover{color:var(--link);border-color:var(--link);text-decoration:none}
.empty{text-align:center;padding:40px 16px;color:var(--muted)}
.empty b{display:block;color:var(--fg);font-size:15px;margin-bottom:2px}
/* session + steps */
.layout{display:grid;grid-template-columns:minmax(0,1.25fr) minmax(0,1fr);gap:20px;align-items:start}
.layout>*{min-width:0}
.preview{position:sticky;top:20px}
.preview .card{padding:0;overflow:hidden}
.preview .bar{display:flex;align-items:center;justify-content:space-between;gap:10px;padding:10px 14px;
border-bottom:1px solid var(--border);background:var(--card-2);font-size:12.5px;color:var(--muted)}
iframe{display:block;width:100%;height:calc(100vh - 170px);min-height:460px;border:0;background:#fff}
.meta{display:flex;flex-wrap:wrap;gap:6px 16px;margin-top:6px;color:var(--muted);font:12px var(--mono)}
ul.dl{list-style:none;padding:0;margin:0;display:flex;flex-wrap:wrap;gap:8px}
ul.dl a{display:inline-flex;align-items:center;gap:6px;padding:7px 12px;border:1px solid var(--border-2);
border-radius:7px;background:var(--card);color:var(--fg);font-weight:500;font-size:13px}
ul.dl a:hover{border-color:var(--link);color:var(--link);text-decoration:none}
ul.dl svg{width:14px;height:14px}
ul.sessions{list-style:none;padding:0;margin:0}
ul.sessions li{padding:7px 0;border-bottom:1px solid var(--border);font:12px var(--mono);color:var(--muted);
overflow-wrap:anywhere}
ul.sessions li:last-child{border-bottom:0}
section ul{margin:0;padding-left:18px}
blockquote.narration{margin:8px 0;padding:8px 14px;border-left:3px solid var(--ember);background:var(--soft);
border-radius:0 8px 8px 0}
.finding{display:flex;gap:14px;align-items:flex-start;padding:10px 0;border-bottom:1px solid var(--border)}
.finding:last-child{border-bottom:0}
.finding>div{min-width:0;flex:1}
.finding img.shot{width:150px;max-width:40%;border-radius:6px;border:1px solid var(--border);flex-shrink:0}
.finding .muted{font-size:13px;margin-top:2px}
details.stepedit{border-bottom:1px solid var(--border);padding:8px 0}
details.stepedit:last-child{border-bottom:0}
details.stepedit summary{cursor:pointer;display:flex;gap:8px;align-items:baseline;list-style-position:outside}
details.stepedit summary .muted{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;min-width:0}
details.stepedit[open] summary{margin-bottom:6px}
.sid{font:600 12.5px var(--mono);white-space:nowrap}
ol.phases,ul.restore{list-style:none;margin:0;padding:0}
ol.phases li{padding:10px 0;border-bottom:1px solid var(--border)}
ol.phases li:last-child,ul.restore li:last-child{border-bottom:0}
ol.phases form{display:flex;gap:8px;align-items:center}
ol.phases .letter{font:600 13px var(--mono);color:var(--muted);min-width:2ch}
ol.phases input{max-width:none;flex:1}
.phase-steps{display:flex;flex-wrap:wrap;gap:4px 10px;margin:6px 0 0 calc(2ch + 8px)}
ul.restore li{display:flex;justify-content:space-between;align-items:center;gap:10px;padding:6px 0;
border-bottom:1px solid var(--border)}
ul.restore form{margin:0}
img.shot{cursor:zoom-in}
#lightbox{position:fixed;inset:0;background:rgba(10,10,12,.82);display:flex;align-items:center;
justify-content:center;z-index:100}
#lightbox[hidden]{display:none}
#lightbox img{max-width:95vw;max-height:90vh;object-fit:contain;border-radius:8px}
#lightbox .close{position:absolute;top:18px;right:24px;color:#fff;font-size:2rem;line-height:1;cursor:pointer;
user-select:none}
.steps{display:grid;gap:10px}
label.step{display:grid;grid-template-columns:auto 200px minmax(0,1fr) auto;gap:16px;align-items:center;
font-weight:400;font-size:14px;margin:0;padding:12px 16px;cursor:pointer}
label.step:has(input[name=keep]:not(:checked)){background:var(--card-2);border-style:dashed}
label.step:has(input[name=keep]:not(:checked)) img.shot{opacity:.4}
label.step:has(input[name=keep]:not(:checked)) .what>strong,
label.step:has(input[name=keep]:not(:checked)) .act{text-decoration:line-through}
label.step input[name=keep]{width:18px;height:18px;margin:0;accent-color:var(--btn)}
label.step img.shot{width:200px;height:112px;object-fit:cover;object-position:top left;border-radius:6px;
border:1px solid var(--border);display:block}
.what{min-width:0}
.what strong{font-family:var(--mono);font-size:12.5px;margin-right:6px}
.what .act{font-weight:600}
.what .muted{display:block;font-size:13px;margin-top:2px;overflow-wrap:anywhere}
.pos{text-align:center}
.pos span{display:block;font:500 10.5px var(--mono);letter-spacing:1px;text-transform:uppercase;
color:var(--muted);margin-bottom:4px}
.stickybar{position:sticky;bottom:0;z-index:5;margin-top:16px;padding:12px 16px;display:flex;gap:12px;
align-items:center;justify-content:space-between;flex-wrap:wrap;background:var(--card);
border:1px solid var(--border);border-radius:var(--radius);box-shadow:0 -6px 20px rgba(0,0,0,.08)}
.grid2{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}
.grid2>.card{margin:0}
.cfg .card h2{display:flex;justify-content:space-between;align-items:center;gap:10px}
.cfg .row{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:12px}
.cfg .field input[type=text],.cfg .field select{max-width:none}
.cfg .row .wide{grid-column:1/-1}
.test{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
.test .muted{font-size:12.5px;overflow-wrap:anywhere}
table.rec td,table.rec th{white-space:nowrap}
table.rec td{font:12.5px var(--mono)}
table.rec td:first-child{font-family:var(--sans);font-weight:600}
@media(max-width:1180px){.layout{grid-template-columns:1fr}.preview{position:static}
iframe{height:560px}}
@media(max-width:1000px){.stats{grid-template-columns:repeat(2,minmax(0,1fr))}.grid2{grid-template-columns:1fr}}
@media(max-width:860px){
.app{grid-template-columns:minmax(0,1fr);background:none}
.side{position:static;height:auto;flex-direction:row;flex-wrap:wrap;align-items:center;gap:4px 8px;
padding:10px 12px}
.logo{padding:0 8px 0 2px}
.side nav{flex-direction:row;flex-wrap:wrap;gap:2px}
.side .sec{display:none}
.side .foot{margin:0;padding:2px 4px;flex-basis:100%;display:flex;gap:4px 14px;flex-wrap:wrap}
.side .foot br{display:none}
main{padding:20px 16px 56px}
.search{width:100%}
label.step{grid-template-columns:auto minmax(0,1fr) auto}
label.step img.shot{grid-column:2/4;grid-row:2;width:100%;height:auto;max-height:200px}
.cfg .row{grid-template-columns:1fr}
}
@media(max-width:520px){.stats{grid-template-columns:1fr 1fr}.stat b{font-size:19px}h1{font-size:22px}}
"""
)

# Minimal stroke icons (24-unit viewBox, currentColor) -- inline SVG so the
# page needs no icon font or image requests.
_ICON_PATHS = {
    "library": '<path d="M4 5h16M4 12h16M4 19h10"/>',
    "plus": '<path d="M12 5v14M5 12h14"/>',
    "queue": '<path d="M4 13h4l2 3h4l2-3h4"/><path d="M5 13 7 5h10l2 8v6H5z"/>',
    "gear": '<circle cx="12" cy="12" r="3"/><path d="M12 2v3M12 19v3M4.2 4.2l2.1 2.1M17.7 17.7l2.1 2.1'
    'M2 12h3M19 12h3M4.2 19.8l2.1-2.1M17.7 6.3l2.1-2.1"/>',
    "search": '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>',
    "upload": '<path d="M12 16V4M7 9l5-5 5 5"/><path d="M4 16v4h16v-4"/>',
    "image": '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="m3 16 5-5 4 4 3-3 6 6"/>',
    "download": '<path d="M12 4v12M7 11l5 5 5-5"/><path d="M4 20h16"/>',
}


def _icon(name):
    return (
        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" '
        f'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{_ICON_PATHS[name]}</svg>'
    )


# Sidebar LLM status: one cheap probe of the [steps] section through the
# existing POST /ui/config/test route, cached per browser tab for a minute
# so the auto-refreshing processing page doesn't re-probe every 3 seconds.
# Fire-and-forget: the page renders immediately and the footer fills in.
_STATUS_SCRIPT = """<script>
(function(){
  var el = document.getElementById('llm-status');
  if (!el) return;
  var KEY = 'sopforge.llm-status';
  function name(p){ return {ollama:'Ollama', openrouter:'OpenRouter', openai:'OpenAI',
    anthropic:'Anthropic'}[p] || p || 'LLM'; }
  function paint(d){
    var dot = el.querySelector('.dot'), txt = el.querySelector('b');
    if (!d){ dot.className = 'dot error'; txt.textContent = 'LLM status unknown'; return; }
    var word = d.status === 'ok' ? 'connected' : d.status === 'warn' ? 'model missing' : 'unreachable';
    dot.className = 'dot ' + d.status;
    txt.textContent = name(d.provider) + ' ' + word;
    el.title = (d.model || '') + (d.detail ? ' \\u2014 ' + d.detail : '');
  }
  try {
    var c = JSON.parse(sessionStorage.getItem(KEY) || 'null');
    if (c && Date.now() - c.t < 60000){ paint(c.d); return; }
  } catch (e) {}
  fetch('/ui/config/test', {method: 'POST', body: new URLSearchParams({section: 'steps'})})
    .then(function(r){ return r.ok ? r.json() : null; })
    .then(function(d){
      paint(d);
      if (d) try { sessionStorage.setItem(KEY, JSON.stringify({t: Date.now(), d: d})); } catch (e) {}
    })
    .catch(function(){ paint(null); });
})();
</script>"""


def _sidebar(active, nav):
    nav = nav or {}
    count = nav.get("review_count")
    badge = f'<span class="count" aria-label="{count} need review">{count}</span>' if count else ""

    def item(key, href, icon, label, extra=""):
        current = ' aria-current="page"' if key == active else ""
        return f'<a href="{href}"{current}>{_icon(icon)}<span>{label}</span>{extra}</a>'

    return (
        '<aside class="side"><a class="logo" href="/ui"><span class="mark" aria-hidden="true"></span>'
        "SOPForge</a>"
        '<nav aria-label="Main">'
        + item("library", "/ui", "library", "Library")
        + item("new", "/ui#new", "plus", "New SOP")
        + item("review", "/ui?view=review", "queue", "Review queue", badge)
        + '<div class="sec">System</div>'
        + item("config", "/ui/config", "gear", "Configuration")
        + "</nav>"
        '<div class="foot"><span id="llm-status" role="status">'
        '<span class="dot"></span><b>Checking LLM&hellip;</b></span><br>'
        f"<span>v{html.escape(__version__)} &middot; Built by CWI AI</span></div>"
        "</aside>"
    )


def _shell(title, body, active="library", nav=None, head_extra=""):
    """Wrap page body in the shared Forge chrome + stylesheet."""
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<meta name="color-scheme" content="light dark">'
        f"{head_extra}"
        f"<title>{html.escape(title)}</title><style>{_STYLE}</style></head><body>"
        f'<div class="app">{_sidebar(active, nav)}<main>{body}</main></div>'
        f"{_STATUS_SCRIPT}</body></html>"
    )


def _header(eyebrow, heading, lede="", actions=""):
    lede_html = f'<p class="lede">{lede}</p>' if lede else ""
    actions_html = f'<div class="toolbar">{actions}</div>' if actions else ""
    return (
        f'<div class="top"><div><div class="eyebrow">{eyebrow}</div>'
        f"<h1>{heading}</h1>{lede_html}</div>{actions_html}</div>"
    )


_BACK = '<a href="/ui">&larr; Back to library</a>'


def _color_for(category, count):
    if count == 0:
        return "green"
    return "red" if category in ("template_fallback_steps", "caption_fallback_steps") else "yellow"


def _section(title, category, items):
    color = _color_for(category, len(items))
    if items:
        body = "<ul>" + "".join(f"<li>{html.escape(str(item))}</li>" for item in items) + "</ul>"
    else:
        body = '<p class="muted">None.</p>'
    # data-status + <h2> immediately after the section open tag is a contract
    # the review tests assert against -- keep the heading first, no nested tag
    # between the attribute and the <h2>.
    return (
        f'<section class="card" data-status="{color}"><h2>{html.escape(title)}</h2>{body}</section>'
    )


def _finding_row(session_id, step_id, entry):
    """One report finding (a flagged step_id) rendered as a thumbnail +
    generated text + a link into the doc preview iframe at that step's
    anchor (render_html's id="{step_id}", task-06), plus a link to that
    step's editor (#edit-{step_id}, task-06's _step_editor) and an "edited"
    badge when the step already carries a manual edit. `entry` is the
    matching steps.json record (or None if there wasn't one for this
    step_id -- degrades to a text-only row rather than a broken <img>)."""
    sid = html.escape(session_id)
    sid_step = html.escape(step_id)
    screenshot = (entry or {}).get("screenshot") or ""
    thumb = (
        f'<img class="shot" loading="lazy" '
        f'src="/sessions/{sid}/{html.escape(screenshot)}" alt="{sid_step}">'
        if screenshot
        else ""
    )
    text = html.escape((entry or {}).get("text") or "")
    text_html = f'<div class="muted">{text}</div>' if text else ""
    edited_badge = (
        '<span class="pill edited">edited</span>' if (entry or {}).get("manually_edited") else ""
    )
    return (
        f'<div class="finding">{thumb}'
        f'<div><a class="sid" href="/sessions/{sid}/doc.html#{sid_step}" target="docpreview">'
        f"<strong>{sid_step}</strong></a> "
        f'<a class="muted" href="#edit-{sid_step}">Edit</a> {edited_badge}'
        f"{text_html}</div></div>"
    )


def _step_section(title, category, step_ids, session_id, steps_by_id):
    """_section's counterpart for step-id findings: same card/color/heading
    contract (data-status + <h2> first, no nested tag between them -- see
    _section's own comment), but thumbnail rows instead of bare <li>s when
    a per-step index (steps.json, task-05) is available. Falls back to
    _section's plain list for a session generated before steps.json
    existed, or when steps_by_id is None."""
    if steps_by_id is None:
        return _section(title, category, step_ids)
    color = _color_for(category, len(step_ids))
    if step_ids:
        body = "".join(
            _finding_row(session_id, step_id, steps_by_id.get(step_id)) for step_id in step_ids
        )
    else:
        body = '<p class="muted">None.</p>'
    return (
        f'<section class="card" data-status="{color}"><h2>{html.escape(title)}</h2>{body}</section>'
    )


def _step_editor(session_id, step_id, entry, can_regenerate):
    """One collapsed <details> per step: a thumbnail, a textarea holding
    its current text (editable via POST .../steps/{step_id}), and an
    optional "Regenerate with AI" button (POST .../steps/{step_id}/
    regenerate) -- omitted entirely when can_regenerate is False (photo-
    mode sessions have no manifest ground truth to regenerate from)."""
    sid = html.escape(session_id)
    sid_step = html.escape(step_id)
    screenshot = (entry or {}).get("screenshot") or ""
    thumb = (
        f'<img class="shot" loading="lazy" '
        f'src="/sessions/{sid}/{html.escape(screenshot)}" alt="{sid_step}">'
        if screenshot
        else ""
    )
    text = html.escape((entry or {}).get("text") or "")
    summary_text = (entry or {}).get("text") or ""
    summary_preview = html.escape(
        summary_text if len(summary_text) <= 80 else summary_text[:77] + "..."
    )
    regenerate_form = (
        f'<form method="post" action="/ui/sessions/{sid}/steps/{sid_step}/regenerate">'
        '<button type="submit" class="secondary">Regenerate with AI</button></form>'
        if can_regenerate
        else ""
    )
    edited_badge = (
        ' <span class="pill edited">edited</span>' if (entry or {}).get("manually_edited") else ""
    )
    return (
        f'<details class="stepedit" id="edit-{sid_step}">'
        f'<summary><strong class="sid">{sid_step}</strong>{edited_badge} '
        f'<span class="muted">{summary_preview}</span></summary>'
        f'<div class="finding">{thumb}'
        "<div>"
        f'<form method="post" action="/ui/sessions/{sid}/steps/{sid_step}">'
        f'<div class="field"><label for="text-{sid_step}">Step text</label>'
        f'<textarea name="text" id="text-{sid_step}" rows="4" required>{text}</textarea></div>'
        '<div class="actions"><button type="submit">Save &amp; re-export</button>'
        f"{regenerate_form}</div>"
        "</form>"
        "</div></div></details>"
    )


def _phases_card(sid, phases, steps_by_id):
    """The document's phases (6c), each with a rename form and the steps
    under it. Rendered from report.json's "phases"; capture sessions only.
    `sid` is already escaped."""
    if not isinstance(phases, list) or not phases:
        return ""
    rows = []
    for index, phase in enumerate(phases):
        if not isinstance(phase, dict):
            continue
        name = html.escape(str(phase.get("name") or ""))
        step_links = "".join(
            f'<a class="sid" href="#edit-{html.escape(str(i))}" title="'
            f'{html.escape(((steps_by_id or {}).get(i) or {}).get("text") or "")}">'
            f"{html.escape(str(i))}</a>"
            for i in phase.get("step_ids") or []
        )
        rows.append(
            f'<li><form method="post" action="/ui/sessions/{sid}/phases/{index}">'
            f'<span class="letter">{chr(65 + index) if index < 26 else index + 1})</span>'
            f'<input type="text" name="name" value="{name}" maxlength="60" required '
            f'aria-label="Name of phase {index + 1}">'
            '<button type="submit" class="secondary">Rename</button></form>'
            f'<div class="phase-steps">{step_links}</div></li>'
        )
    return (
        '<div class="card"><h2>Phases</h2>'
        '<p class="help" style="margin:-4px 0 8px">Renaming a phase re-exports every format '
        "without calling the AI.</p>"
        f'<ol class="phases">{"".join(rows)}</ol></div>'
    )


_META_FIELDS = (
    ("title", "Title"),
    ("subtitle", "Subtitle"),
    ("doc_no", "Document no."),
    ("author", "Author"),
    ("revision", "Revision"),
)


def _document_card(sid, document):
    """6g's resolved title/subtitle/doc no./author/revision, editable. Posts
    to /ui/sessions/{id}/metadata; a blank field returns to its inferred
    default. Rendered only when report.json carries "document"."""
    if not isinstance(document, dict):
        return ""
    fields = "".join(
        f'<div class="field{" wide" if key == "title" else ""}">'
        f'<label for="meta-{key}">{label}</label>'
        f'<input type="text" name="{key}" id="meta-{key}" maxlength="120" '
        f'value="{html.escape(str(document.get(key) or ""))}"'
        + (' pattern="\\d{1,3}(\\.\\d{1,3}){0,2}" placeholder="1.0"' if key == "revision" else "")
        + "></div>"
        for key, label in _META_FIELDS
    )
    return (
        '<div class="card"><h2>Document details</h2>'
        '<p class="help" style="margin:-4px 0 10px">Shown on the title page, revision table '
        "and SPL footer, and used to name downloads. Clear a field to go back to the "
        "inferred value. Saving re-exports every format without calling the AI.</p>"
        f'<form method="post" action="/ui/sessions/{sid}/metadata" class="cfg">'
        f'<div class="row">{fields}</div>'
        '<div class="actions" style="margin-bottom:0"><button type="submit" class="secondary">'
        "Save details</button></div></form></div>"
    )


def _edit_steps_card(session_id, steps, can_regenerate):
    """A card listing every step as a collapsed editor -- not only flagged
    ones, since "the phrasing is just fine except this one step" is not
    necessarily a flagged condition. Returns "" when steps is None (a
    session generated before steps.json existed)."""
    if steps is None:
        return ""
    rows = "".join(
        _step_editor(session_id, e["step_id"], e, can_regenerate)
        for e in steps
        if isinstance(e, dict) and "step_id" in e
    )
    return (
        '<div class="card" id="edit-steps"><h2>Edit steps</h2>'
        '<p class="help" style="margin:-4px 0 6px">Edits are saved immediately and survive a '
        "later re-render. Regenerating a step discards its manual edit and asks the AI again."
        + ("" if can_regenerate else " Regenerate isn't available for this build mode.")
        + f"</p>{rows}</div>"
    )


def _fallback_rows(entries):
    """Minimal table rows straight from library-index entries -- used when
    the caller didn't pass enriched rows (library_view.session_row)."""
    return [
        {
            "session_id": e["session_id"],
            "title": e.get("title") or e["session_id"],
            "date": str(e.get("date") or ""),
            "mode": "",
            "doc_no": None,
            "steps": None,
            "state": "ready",
            "status_label": "Ready",
            "needs_review": False,
            "exports": [],
            "thumb": None,
            "updated": str(e.get("date") or "")[:10],
        }
        for e in entries
    ]


def _library_row(row):
    sid = html.escape(row["session_id"])
    title = html.escape(str(row["title"]))
    if row.get("thumb"):
        thumb = f'<img class="thumb" loading="lazy" src="{html.escape(row["thumb"])}" alt="">'
    else:
        thumb = f'<span class="thumb none">{_icon("image")}</span>'
    sub = " &middot; ".join(
        html.escape(part) for part in (row.get("mode") or "", row.get("date", "")[:10]) if part
    )
    state = html.escape(row["state"])
    spin = '<span class="spin" aria-hidden="true"></span>' if state == "generating" else ""
    if row.get("exports"):
        fmt = "".join(
            f'<a href="{html.escape(href)}" title="Download {html.escape(label)}">'
            f"{html.escape(label)}</a>"
            for label, href in row["exports"]
        )
    else:
        fmt = '<span aria-label="none">&mdash;</span>'
    steps = "&mdash;" if row.get("steps") is None else str(row["steps"])
    doc_no = html.escape(row["doc_no"]) if row.get("doc_no") else "&mdash;"
    return (
        f'<tr data-state="{state}"><td><div class="proc">{thumb}<div>'
        f'<a href="/ui/sessions/{sid}">{title}</a><small class="muted">{sub}</small></div></div></td>'
        f'<td class="num">{doc_no}</td><td class="num">{steps}</td>'
        f'<td><span class="pill {state}">{spin}{html.escape(row["status_label"])}</span></td>'
        f'<td><div class="fmt">{fmt}</div></td>'
        f'<td class="num">{html.escape(row.get("updated") or "")}</td></tr>'
    )


def _stat_tiles(stats):
    steps, ai = stats["steps"], stats["ai_steps"]
    if steps:
        ai_value = f"{round(100 * ai / steps)}%"
        ai_label = f"Steps AI-written <i>({ai} of {steps})</i>"
    else:
        ai_value, ai_label = "&mdash;", "Steps AI-written"
    tiles = (
        (str(stats["procedures"]), "Procedures"),
        (str(stats["need_review"]), "Need review"),
        (str(steps), "Steps captured"),
        (ai_value, ai_label),
    )
    return (
        '<div class="stats">'
        + "".join(f'<div class="stat"><b>{v}</b><span>{label}</span></div>' for v, label in tiles)
        + "</div>"
    )


# The one drop zone. Sorts whatever was dropped/picked into the two existing
# build routes: a manifest.json anywhere in the set means a capture (POST
# /ui/upload: manifest_file + files + optional transcript/narration wav);
# otherwise it's a screenshots build (POST /ui/build: title + images in
# natural filename order + optional transcript). Folders are walked via
# webkitGetAsEntry. The POST is a plain multipart fetch; the routes' own
# 303 redirect lands on the new session's steps-review page.
_DROP_SCRIPT = """<script>
(function(){
  var zone = document.getElementById('new');
  if (!zone) return;
  var plan = zone.querySelector('.plan'), err = zone.querySelector('.err');
  var go = zone.querySelector('[data-go]'), kind = zone.querySelector('[data-kind]');
  var list = zone.querySelector('[data-files]'), titleRow = zone.querySelector('[data-title]');
  var IMG = /\\.(png|jpe?g|gif|bmp|webp)$/i, TXT = /\\.(txt|md|json)$/i, picked = null;

  function walk(entry){
    return new Promise(function(done){
      if (entry.isFile) return entry.file(function(f){ done([f]); }, function(){ done([]); });
      if (!entry.isDirectory) return done([]);
      var reader = entry.createReader(), all = [];
      (function more(){
        reader.readEntries(function(batch){
          if (!batch.length){
            return Promise.all(all.map(walk)).then(function(l){ done([].concat.apply([], l)); });
          }
          all = all.concat([].slice.call(batch)); more();
        }, function(){ done([]); });
      })();
    });
  }
  function classify(files){
    var p = {manifest: null, images: [], texts: [], wav: null, skipped: []};
    files.forEach(function(f){
      var n = f.name.toLowerCase();
      if (n === 'manifest.json' && !p.manifest) p.manifest = f;
      else if (IMG.test(n)) p.images.push(f);
      else if (TXT.test(n)) p.texts.push(f);
      else if (/\\.wav$/.test(n) && !p.wav) p.wav = f;
      else p.skipped.push(f);
    });
    p.images.sort(function(a, b){
      return a.name.localeCompare(b.name, undefined, {numeric: true});
    });
    p.mode = p.manifest ? 'capture' : 'photo';
    p.transcript = p.texts[0] || null;
    return p;
  }
  function chip(text){ var li = document.createElement('li'); li.textContent = text; return li; }
  function show(files){
    picked = classify(files);
    err.textContent = '';
    list.innerHTML = '';
    var p = picked, n = p.images.length;
    kind.innerHTML = '';
    var pill = document.createElement('span');
    pill.className = 'pill ' + (p.mode === 'capture' ? 'run' : 'warn');
    pill.textContent = p.mode === 'capture' ? 'Capture session' : 'Screenshots build';
    var what = document.createElement('span');
    what.className = 'muted';
    what.textContent = p.mode === 'capture'
      ? 'manifest.json found \\u2014 steps come from the recorded clicks.'
      : 'No manifest.json \\u2014 each image becomes one step, in filename order.';
    kind.appendChild(pill); kind.appendChild(what);
    if (p.manifest) list.appendChild(chip('manifest.json'));
    list.appendChild(chip(n + (n === 1 ? ' screenshot' : ' screenshots')));
    if (p.transcript) list.appendChild(chip('transcript: ' + p.transcript.name));
    if (p.texts.length > 1) list.appendChild(chip((p.texts.length - 1) + ' other text file(s) ignored'));
    if (p.wav && p.mode === 'capture') list.appendChild(chip('narration: ' + p.wav.name));
    if (p.skipped.length) list.appendChild(chip(p.skipped.length + ' other file(s) ignored'));
    titleRow.hidden = p.mode !== 'photo';
    plan.hidden = false;
    go.disabled = false;
    if (!n && p.mode === 'photo'){ err.textContent = 'No screenshots found \\u2014 drop PNG/JPG images or a capture folder.'; go.disabled = true; }
    go.focus();
  }
  function submit(){
    var p = picked, fd = new FormData(), url;
    if (!p) return;
    if (p.mode === 'capture'){
      url = '/ui/upload';
      fd.append('manifest_file', p.manifest, 'manifest.json');
      if (p.wav) fd.append('narration_wav_file', p.wav, p.wav.name);
    } else {
      url = '/ui/build';
      fd.append('title', zone.querySelector('[name=title]').value);
    }
    p.images.forEach(function(f){ fd.append('files', f, f.name); });
    if (p.transcript) fd.append('transcript_file', p.transcript, p.transcript.name);
    go.disabled = true; go.textContent = 'Uploading\\u2026'; err.textContent = '';
    fetch(url, {method: 'POST', body: fd}).then(function(r){
      if (r.ok){ location.href = r.url; return; }
      return r.json().catch(function(){ return {}; }).then(function(d){
        var detail = d && d.detail;
        err.textContent = 'Upload failed: ' + (typeof detail === 'string' ? detail : 'HTTP ' + r.status);
        go.disabled = false; go.textContent = 'Build document';
      });
    }).catch(function(){
      err.textContent = 'Upload failed: the server could not be reached.';
      go.disabled = false; go.textContent = 'Build document';
    });
  }
  ['dragenter', 'dragover'].forEach(function(t){
    zone.addEventListener(t, function(e){ e.preventDefault(); zone.classList.add('over'); });
  });
  zone.addEventListener('dragleave', function(e){
    if (!zone.contains(e.relatedTarget)) zone.classList.remove('over');
  });
  zone.addEventListener('drop', function(e){
    e.preventDefault(); zone.classList.remove('over');
    var items = e.dataTransfer.items, files = [].slice.call(e.dataTransfer.files);
    var entries = items && items.length && items[0].webkitGetAsEntry
      ? [].slice.call(items).map(function(i){ return i.webkitGetAsEntry(); }).filter(Boolean)
      : [];
    // Entries let a dropped folder be walked; a drop without them (or a
    // synthetic one) still carries plain files.
    if (!entries.length) return show(files);
    Promise.all(entries.map(walk)).then(function(l){ show([].concat.apply([], l)); });
  });
  zone.querySelectorAll('input[type=file]').forEach(function(input){
    input.addEventListener('change', function(){
      if (input.files.length) show([].slice.call(input.files));
      input.value = '';
    });
  });
  zone.querySelectorAll('[data-pick]').forEach(function(b){
    b.addEventListener('click', function(){ document.getElementById(b.dataset.pick).click(); });
  });
  go.addEventListener('click', submit);
  zone.querySelector('[data-cancel]').addEventListener('click', function(){
    picked = null; plan.hidden = true;
  });
})();
</script>"""

# The two classic multipart forms, for a browser with JS off. Same routes and
# field names as before the drop zone existed.
_NOSCRIPT_FORMS = (
    '<noscript><div class="grid2" style="margin-bottom:24px">'
    '<div class="card"><h2>Upload a capture session</h2>'
    '<form method="post" action="/ui/upload" enctype="multipart/form-data">'
    '<div class="field"><label>Manifest (manifest.json)</label>'
    '<input type="file" name="manifest_file" accept=".json" required></div>'
    '<div class="field"><label>Screenshots (select every PNG)</label>'
    '<input type="file" name="files" multiple required></div>'
    '<div class="field"><label>Narration transcript &mdash; optional (.txt or .md)</label>'
    '<input type="file" name="transcript_file" accept=".txt,.md,.json"></div>'
    '<button type="submit">Upload</button></form></div>'
    '<div class="card"><h2>Build from screenshots + transcript</h2>'
    '<form method="post" action="/ui/build" enctype="multipart/form-data">'
    '<div class="field"><label>Title (optional)</label>'
    '<input type="text" name="title" placeholder="My procedure"></div>'
    '<div class="field"><label>Screenshots / images (in order)</label>'
    '<input type="file" name="files" accept="image/*" multiple required></div>'
    '<div class="field"><label>Transcript &mdash; optional (.txt or .md)</label>'
    '<input type="file" name="transcript_file" accept=".txt,.md,.json"></div>'
    '<button type="submit">Build document</button></form></div>'
    "</div></noscript>"
)


def _drop_zone():
    return (
        '<div class="drop" id="new">'
        f'<div class="ico">{_icon("upload")}</div>'
        '<div class="txt"><h2>Drop a capture folder or screenshots</h2>'
        "<p>manifest.json + PNGs, or images with an optional transcript (.txt, .md, .json). "
        "SOPForge works out which.</p></div>"
        '<div class="toolbar">'
        '<button type="button" class="secondary" data-pick="drop-files">Browse files</button>'
        '<button type="button" class="secondary" data-pick="drop-folder">Choose folder</button>'
        '<input type="file" id="drop-files" multiple hidden '
        'accept=".json,.png,.jpg,.jpeg,.gif,.bmp,.webp,.txt,.md,.wav,image/*">'
        '<input type="file" id="drop-folder" webkitdirectory multiple hidden></div>'
        '<div class="plan" hidden><div class="kind" data-kind></div><ul data-files></ul>'
        '<div class="field" data-title hidden><label for="drop-title">Title '
        "<small>(optional &mdash; inferred when blank)</small></label>"
        '<input type="text" id="drop-title" name="title" placeholder="My procedure"></div>'
        '<div class="actions"><button type="button" data-go>Build document</button>'
        '<button type="button" class="secondary" data-cancel>Cancel</button></div>'
        '<p class="err" role="alert"></p></div>'
        "</div>" + _NOSCRIPT_FORMS + _DROP_SCRIPT
    )


def render_library_page(entries, query=None, rows=None, stats=None, nav=None, view=None):
    """entries: library-index entries (library.search). rows/stats: the
    enriched table rows + stat tiles from webui/library_view.py; without
    them the table degrades to title/date from `entries`. view="review"
    renders the review queue (rows needing attention) instead."""
    if rows is None:
        rows = _fallback_rows(entries)
    review = view == "review"
    if review:
        rows = [r for r in rows if r.get("needs_review")]
    if rows:
        body_rows = "".join(_library_row(r) for r in rows)
    else:
        if review:
            empty = "<b>Nothing to review.</b>Every finished procedure is clean."
        elif query:
            empty = (
                "<b>No sessions yet.</b>Nothing in the library matches "
                f"&ldquo;{html.escape(query)}&rdquo;."
            )
        else:
            empty = "<b>No sessions yet.</b>Drop a capture folder or screenshots above to start."
        body_rows = f'<tr><td colspan="6" class="empty">{empty}</td></tr>'
    query_value = html.escape(query) if query else ""
    view_field = '<input type="hidden" name="view" value="review">' if review else ""
    search = (
        '<search><form method="get" action="/ui">'
        f'<label class="search" style="margin:0">{_icon("search")}'
        f'<input type="text" name="q" value="{query_value}" aria-label="Search procedures" '
        f'placeholder="Search title, date, doc no.">{view_field}'
        '<button type="submit" class="secondary">Search</button></label></form></search>'
    )
    if review:
        header = _header(
            "Procedures",
            "Review queue",
            "Steps awaiting your check before generation, documents whose review report "
            "flags something, and failed builds.",
            search,
        )
    else:
        header = _header(
            "Procedures", "SOP Library", "", search + '<a class="btn" href="#new">New SOP</a>'
        )
    body = (
        header
        + (_stat_tiles(stats) if stats else "")
        + ("" if review else _drop_zone())
        + '<div class="tablewrap"><table class="lib"><thead><tr><th scope="col">Procedure</th>'
        '<th scope="col">Doc no.</th><th scope="col">Steps</th><th scope="col">Status</th>'
        '<th scope="col">Exports</th><th scope="col">Updated</th></tr></thead>'
        f"<tbody>{body_rows}</tbody></table></div>"
    )
    return _shell(
        "SOPForge Review queue" if review else "SOPForge Library",
        body,
        active="review" if review else "library",
        nav=nav,
    )


def render_session_processing_page(session_id, status, nav=None, title=None):
    # While the background job is still running, auto-refresh every few seconds
    # so the page turns into the finished review page on its own the moment
    # generation completes -- without this the user is left staring at a stale
    # "processing" snapshot forever. A terminal "error" status stops refreshing.
    is_pending = status["status"] in ("queued", "processing")
    state = html.escape(status["status"])
    spin = '<span class="spin" aria-hidden="true"></span>' if is_pending else ""
    pending_note = (
        '<p class="muted">Generating your SOP&hellip; this page updates automatically '
        "when it&rsquo;s ready.</p>"
        if is_pending
        else ""
    )
    err = (
        f'<p class="muted">{html.escape(status.get("error", ""))}</p>'
        if status["status"] == "error"
        else ""
    )
    progress = status.get("progress")
    progress_bar = ""
    if is_pending and progress and progress.get("total"):
        current, total = progress["current"], progress["total"]
        pct = round(100 * current / total)
        progress_bar = (
            '<label style="font-weight:400;margin:0">'
            f'<progress value="{current}" max="{total}"></progress>'
            f'<span class="muted mono">{current} / {total} steps ({pct}%)</span></label>'
        )
    heading = "Generation failed" if status["status"] == "error" else "Generating document"
    body = (
        _header(
            _BACK,
            html.escape(title) if title else heading,
            f'<span class="mono">{html.escape(session_id)}</span>',
        )
        + f'<div class="card{" note bad" if status["status"] == "error" else ""}" '
        'style="max-width:640px">'
        f"<h2>{heading}</h2>"
        f'<p data-status="{state}"><span class="pill {state}">{spin}Status: {state}</span></p>'
        + progress_bar
        + pending_note
        + err
        + "</div>"
    )
    refresh_meta = '<meta http-equiv="refresh" content="3">' if is_pending else ""
    return _shell("SOPForge Review", body, nav=nav, head_extra=refresh_meta)


# One shared lightbox overlay + its click/Escape handlers -- used by both
# the steps-review page (a click on any .shot thumbnail there) and the
# session/report page (task-07's thumbnail report rows). Hoisted to module
# level so both pages emit the exact same markup/script instead of two
# copies drifting apart. preventDefault() on the .shot click is what stops
# a surrounding <label> (steps-review's cards) from also toggling its
# checkbox -- a plain click handler alone wouldn't, since <img> inside a
# <label> activates the label by default; harmless no-op on pages (like the
# session page) where .shot isn't inside a <label>. Inline attribute
# handlers on the overlay itself, one small <script> for the rest -- same
# "considered exception" pattern as /ui/config's datalist JS, not a
# framework or build step.
_LIGHTBOX_HTML = (
    '<div id="lightbox" hidden '
    "onclick=\"if(event.target===this)this.hidden=true,this.querySelector('img').src=''\">"
    "<span class=\"close\" onclick=\"lightbox.hidden=true;lightbox.querySelector('img').src=''\">"
    "&times;</span>"
    '<img alt=""></div>'
)
_LIGHTBOX_SCRIPT = (
    "<script>"
    "document.addEventListener('click',function(e){"
    "if(!e.target.classList.contains('shot'))return;"
    "e.preventDefault();"
    "var lb=document.getElementById('lightbox');"
    "lb.querySelector('img').src=e.target.src;"
    "lb.querySelector('img').alt=e.target.alt;"
    "lb.hidden=false;"
    "});"
    "document.addEventListener('keydown',function(e){"
    "if(e.key==='Escape'){"
    "var lb=document.getElementById('lightbox');"
    "lb.hidden=true;lb.querySelector('img').src='';"
    "}});"
    "</script>"
)

# Live "N of M kept" counter on the steps-review sticky bar.
_KEEP_COUNT_SCRIPT = (
    "<script>(function(){var out=document.getElementById('keepcount');if(!out)return;"
    "var boxes=document.querySelectorAll('input[name=keep]');"
    "function n(){var k=0;boxes.forEach(function(b){if(b.checked)k++});"
    "out.textContent=k+' of '+boxes.length+' steps kept'}"
    "boxes.forEach(function(b){b.addEventListener('change',n)});n()})();</script>"
)


def render_steps_review_page(session_id, manifest, nav=None):
    # Shown once, right after upload/build and before generation: a checklist
    # of every captured step so the user can drop mis-clicks (wrong element,
    # accidental double-click) before the doc gets built from them. Checked by
    # default -- this is an opt-out ("uncheck the wrong ones"), not opt-in.
    # Each card also carries a position number, editable to reorder steps --
    # decimals are allowed (e.g. "2.5" inserts between 2 and 3), so moving one
    # step never requires renumbering every other card; the server stable-
    # sorts on whatever values get submitted.
    sid = html.escape(session_id)
    cards = []
    for i, step in enumerate(manifest.steps, start=1):
        step_id = html.escape(step.id)
        detail = step.button if step.action == "click" else step.text_summary
        action_line = (
            f'<span class="act">{html.escape(step.action)}</span> ({html.escape(detail or "")})'
        )
        # Empty UIA fields (common for Chrome/Electron) are left out rather
        # than rendered as a dangling "·" or "()".
        window_line = " &middot; ".join(
            html.escape(part) for part in (step.window.title, step.element.name) if part
        )
        if step.element.control_type:
            window_line += f" ({html.escape(step.element.control_type)})"
        window_line = window_line or "No window or element metadata captured"
        shot_src = f"/sessions/{sid}/raw/{html.escape(step.screenshot)}"
        cards.append(
            '<label class="card step">'
            f'<input type="checkbox" name="keep" value="{step_id}" checked>'
            f'<img class="shot" src="{shot_src}" alt="{step_id}" loading="lazy">'
            f'<span class="what"><strong>{step_id}</strong>{action_line}'
            f'<span class="muted">{window_line}</span></span>'
            '<span class="pos"><span>Position</span>'
            f'<input type="number" name="pos-{step_id}" value="{i}" step="any" min="0" '
            f'aria-label="Position of {step_id}">'
            "</span>"
            "</label>"
        )
    total = len(manifest.steps)
    title = manifest.session.title or ""
    # One shared lightbox for every card (not one overlay per card) -- a click
    # on any .shot thumbnail sets its src and shows it. Closes via the X,
    # clicking the dimmed backdrop, or Escape; clearing the src on close
    # avoids holding a large decoded image in memory between views.
    body = (
        _header(
            _BACK + (f" &middot; {html.escape(title)}" if title else ""),
            "Review captured steps",
            "Uncheck any wrong or accidental clicks before generating the "
            "document. Edit a step's position number to move it &mdash; decimals (e.g. "
            "&ldquo;2.5&rdquo;) insert between two steps without renumbering the rest. "
            "Click a screenshot to see it full size.",
        )
        + f'<form method="post" action="/ui/sessions/{sid}/confirm-steps">'
        + '<div class="steps">'
        + "".join(cards)
        + "</div>"
        + '<div class="stickybar"><span class="muted mono" id="keepcount">'
        f"{total} steps</span>"
        '<div class="actions" style="margin:0"><button type="submit">'
        "Keep selected steps &amp; generate document</button></div></div></form>"
        + _LIGHTBOX_HTML
        + _LIGHTBOX_SCRIPT
        + _KEEP_COUNT_SCRIPT
    )
    return _shell("SOPForge Review", body, nav=nav)


_PROVIDERS = ["ollama", "openrouter", "openai", "anthropic"]
# Vision goes through the OpenAI-compatible image path, which excludes anthropic
# (see config.VisionProvider) -- so the vision row offers only these three.
_VISION_PROVIDERS = ["ollama", "openrouter", "openai"]
_KEY_ENV = {
    "openrouter": "OPENROUTER_API_KEY",
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
}
_RECOMMENDED = {
    "steps": {
        "ollama": "qwen3:32b",
        "openrouter": "anthropic/claude-sonnet-5.5",
        "openai": "gpt-5.4-mini",
        "anthropic": "claude-sonnet-5-5",
    },
    "narrative": {
        "ollama": "qwen3.6:27b",
        "openrouter": "anthropic/claude-opus-5.5",
        "openai": "gpt-5.5",
        "anthropic": "claude-opus-5-5",
    },
    "vision": {
        "ollama": "qwen2.5vl:7b",
        "openrouter": "anthropic/claude-sonnet-5.5",
        "openai": "gpt-4o",
    },
    "polish": {
        "ollama": "gemma3:12b",
        "openrouter": "anthropic/claude-sonnet-5.5",
        "openai": "gpt-5.4-mini",
        "anthropic": "claude-sonnet-5-5",
    },
}
_MODEL_SUGGESTIONS = {
    "steps": {
        "ollama": ["qwen3:32b", "qwen3.6:27b", "qwen3:14b"],
        "openrouter": [
            "anthropic/claude-sonnet-5.5",
            "anthropic/claude-haiku-5.5",
            "anthropic/claude-opus-5.5",
            "openai/gpt-5.4-mini",
        ],
        "openai": ["gpt-5.4-mini", "gpt-5.4-nano"],
        "anthropic": [
            "claude-sonnet-5-5",
            "claude-haiku-5-5",
            "claude-opus-5-5",
            "claude-fable-5-1",
        ],
    },
    "narrative": {
        "ollama": ["qwen3.6:27b", "qwen3:32b"],
        "openrouter": [
            "anthropic/claude-opus-5.5",
            "anthropic/claude-sonnet-5.5",
            "openai/gpt-5.5",
        ],
        "openai": ["gpt-5.5", "gpt-5.4"],
        "anthropic": ["claude-opus-5-5", "claude-sonnet-5-5", "claude-fable-5-1"],
    },
    "vision": {
        "ollama": ["qwen2.5vl:7b", "qwen3-vl:8b", "qwen3.6:27b"],
        "openrouter": [
            "anthropic/claude-sonnet-5.5",
            "anthropic/claude-opus-5.5",
            "openai/gpt-5.5",
        ],
        "openai": ["gpt-5.5", "gpt-4o"],
        # deliberately no "anthropic" key -- vision excludes bare anthropic (see _VISION_PROVIDERS)
    },
    "polish": {
        "ollama": ["gemma3:12b", "gemma3:27b"],
        "openrouter": ["anthropic/claude-sonnet-5.5", "anthropic/claude-haiku-5.5"],
        "openai": ["gpt-5.4-mini", "gpt-5.4-nano"],
        "anthropic": ["claude-sonnet-5-5", "claude-haiku-5-5"],
    },
}


def _provider_select(name, current, key, providers=None):
    opts = "".join(
        f'<option value="{p}"{" selected" if p == current else ""}>{p}</option>'
        for p in (providers or _PROVIDERS)
    )
    # Swap the canonical datalist's contents to the newly-selected provider's
    # per-provider datalist. Defensive null-check: every field's providers
    # currently have a per-provider datalist entry, but a missing one should
    # no-op instead of throwing on a null getElementById.
    onchange = (
        "(function(s){"
        f"var t=document.getElementById('{key}_model_suggestions');"
        f"var d=document.getElementById('{key}_model_suggestions_'+s.value);"
        "if(d)t.innerHTML=d.innerHTML;"
        "})(this)"
    )
    return f'<select name="{name}" id="{name}" onchange="{onchange}">{opts}</select>'


def _model_datalist(key, current_provider):
    canonical_id = f"{key}_model_suggestions"
    per_provider = _MODEL_SUGGESTIONS.get(key, {})
    canonical_options = "".join(
        f'<option value="{html.escape(m)}">' for m in per_provider.get(current_provider, [])
    )
    extra_datalists = "".join(
        f'<datalist id="{key}_model_suggestions_{p}">'
        + "".join(f'<option value="{html.escape(m)}">' for m in models)
        + "</datalist>"
        for p, models in per_provider.items()
    )
    return (
        canonical_id,
        f'<datalist id="{canonical_id}">{canonical_options}</datalist>{extra_datalists}',
    )


def _config_row(key, heading, values, extra="", providers=None):
    suggestions_id, datalist = _model_datalist(key, values["provider"])
    return (
        f'<div class="card"><h2>{heading}</h2>'
        '<div class="row">'
        f'<div class="field"><label for="{key}_provider">Provider</label>'
        f"{_provider_select(f'{key}_provider', values['provider'], key, providers)}</div>"
        f'<div class="field"><label for="{key}_model">Model</label>'
        f'<input type="text" name="{key}_model" id="{key}_model" '
        f'value="{html.escape(values["model"])}" '
        f'list="{suggestions_id}" '
        f"onfocus=\"this.dataset.prev=this.value;this.value=''\" "
        f"onblur=\"if(!this.value)this.value=this.dataset.prev||''\">{datalist}</div>"
        "</div>"
        f'<div class="field"><label for="{key}_endpoint">Endpoint '
        "<small>(Ollama / custom only)</small></label>"
        f'<input type="text" name="{key}_endpoint" id="{key}_endpoint" '
        f'value="{html.escape(values["endpoint"])}"></div>'
        f'<div class="field test"><button type="button" class="secondary" '
        f'data-test-section="{key}">Test connection</button> '
        f'<span class="muted" id="{key}_test_result" role="status"></span></div>'
        f"{extra}</div>"
    )


# One delegated click listener for every "Test connection" button, appended
# once to render_config_page's body. Reads the section's own provider/
# endpoint/model inputs at click time -- the model field clears on focus and
# restores on blur (see _config_row's onfocus/onblur above), and blur always
# fires before this click handler, so .value is the field's real content by
# the time this reads it.
_CONFIG_TEST_SCRIPT = """<script>
document.addEventListener('click', function(e) {
  var btn = e.target.closest('[data-test-section]');
  if (!btn) return;
  var key = btn.getAttribute('data-test-section');
  var result = document.getElementById(key + '_test_result');
  var field = function(suffix) {
    var el = document.querySelector('[name="' + key + suffix + '"]');
    return el ? el.value : '';
  };
  result.textContent = 'Testing...';
  var body = new URLSearchParams({
    section: key,
    provider: field('_provider'),
    endpoint: field('_endpoint'),
    model: field('_model')
  });
  fetch('/ui/config/test', {method: 'POST', body: body})
    .then(function(r) { return r.json().then(function(d) { return {ok: r.ok, d: d}; }); })
    .then(function(res) {
      var d = res.d;
      if (!res.ok) {
        result.textContent = 'error: ' + (d.detail || 'request rejected');
        return;
      }
      var latency = (d.latency_ms !== null && d.latency_ms !== undefined) ?
        ' (' + d.latency_ms + 'ms)' : '';
      result.textContent = d.status + ': ' + d.detail + latency;
    })
    .catch(function() { result.textContent = 'Test failed'; });
});
</script>"""


def render_config_page(config, keystatus, saved=False, nav=None):
    steps, narr, vis, polish = (
        config["steps"],
        config["narrative"],
        config["vision"],
        config["polish"],
    )
    doc = config.get("document", {})
    transcription = config.get("transcription", {})
    saved_note = (
        '<div class="card note ok" data-status="green" role="status">'
        "<strong>Saved.</strong> Changes take effect on the next generation.</div>"
        if saved
        else ""
    )
    checked = " checked" if vis.get("enabled") else ""
    vision_extra = (
        '<div class="field"><label class="check"><input type="checkbox" name="vision_enabled"'
        f"{checked}> Enable vision captioning</label></div>"
        '<div class="field"><label for="vision_max_concurrency">Max concurrency '
        "<small>(concurrent vision calls)</small></label>"
        f'<input type="text" name="vision_max_concurrency" id="vision_max_concurrency" '
        f'value="{vis.get("max_concurrency", 4)}"></div>'
    )
    polish_checked = " checked" if polish.get("enabled") else ""
    polish_extra = (
        '<div class="field"><label class="check"><input type="checkbox" name="polish_enabled"'
        f"{polish_checked}> Enable polish pass</label>"
        '<p class="help">A single formatting/tone pass over the finished document. '
        "Off by default: the local backend only produces a usable rewrite roughly a "
        "quarter to half of the time (falls back to the original text otherwise, never "
        "wrong, just unchanged) — review its output on a real document before relying "
        "on it. Covers all six export formats identically. Can also be overridden "
        "per-job via <code>?polish=off|local|haiku</code> on the rerender endpoint.</p></div>"
    )
    document_card = (
        '<div class="card"><h2>Document</h2>'
        '<div class="field"><label for="document_author">Author <small>(shown on the title '
        "page / revision table)</small></label>"
        f'<input type="text" name="document_author" id="document_author" '
        f'value="{html.escape(doc.get("author", "SOPForge"))}"></div>'
        '<div class="field"><label for="document_doc_no_prefix">Document number prefix '
        '<small>(e.g. "SOP" — blank omits the document number entirely)</small></label>'
        f'<input type="text" name="document_doc_no_prefix" id="document_doc_no_prefix" '
        f'value="{html.escape(doc.get("doc_no_prefix", ""))}"></div>'
        "</div>"
    )
    transcription_checked = " checked" if transcription.get("enabled") else ""
    transcription_device = transcription.get("device", "cpu")
    device_opts = "".join(
        f'<option value="{d}"{" selected" if d == transcription_device else ""}>{d}</option>'
        for d in ("cpu", "cuda", "auto")
    )
    transcription_card = (
        '<div class="card"><h2>Narration transcription</h2>'
        '<div class="field"><label class="check"><input type="checkbox" '
        f'name="transcription_enabled"{transcription_checked}> Transcribe recorded narration '
        "audio</label>"
        '<p class="help">On by default. When a capture session\'s optional tray '
        '"Record narration (mic)" toggle produced a narration.wav, and no transcript '
        "was uploaded by hand, enabling this runs it through a local speech-to-text "
        "model (faster-whisper) before generation — nothing leaves your network. A "
        "missing model or unavailable hardware just skips transcription for that "
        "session; the document still generates from the steps alone.</p></div>"
        '<div class="row">'
        '<div class="field"><label for="transcription_model_size">Model size</label>'
        f'<input type="text" name="transcription_model_size" id="transcription_model_size" '
        f'value="{html.escape(transcription.get("model_size", "base"))}"></div>'
        '<div class="field"><label for="transcription_device">Device</label>'
        f'<select name="transcription_device" id="transcription_device">{device_opts}</select>'
        "</div></div>"
        '<div class="field"><label for="transcription_compute_type">Compute type</label>'
        f'<input type="text" name="transcription_compute_type" id="transcription_compute_type" '
        f'value="{html.escape(transcription.get("compute_type", "int8"))}"></div>'
        "</div>"
    )
    passes_extra = (
        '<div class="field"><label for="narrative_passes">Passes</label>'
        f'<input type="text" name="narrative_passes" id="narrative_passes" '
        f'value="{narr.get("passes", 1)}"></div>'
    )
    steps_extra = (
        '<div class="field"><label for="steps_max_concurrency">Max concurrency</label>'
        f'<input type="text" name="steps_max_concurrency" id="steps_max_concurrency" '
        f'value="{steps.get("max_concurrency", 1)}">'
        '<p class="help">Concurrent LLM calls — see config/models.toml\'s comment; raising '
        "this only helps against an Ollama server tuned for parallel requests.</p></div>"
    )

    key_rows = "".join(
        f"<li>{html.escape(_KEY_ENV.get(p, p))}: "
        + ('<span class="pill ok">set</span>' if ok else '<span class="pill">not set</span>')
        + "</li>"
        for p, ok in sorted(keystatus.items())
    )
    key_panel = (
        '<div class="card"><h2>API keys</h2><p class="help" style="margin-top:0">Keys are '
        "read from environment variables and never stored in the config — this page can only "
        "show whether one is set, never edit or reveal it. To set one: PowerShell "
        "<code>setx ANTHROPIC_API_KEY &quot;sk-ant-...&quot;</code> (swap in the variable name "
        "below for your provider), or Windows Settings → search &quot;Environment Variables&quot; "
        "→ Edit environment variables for your account → New. Then restart the server."
        f'</p><ul class="sessions">{key_rows or "<li>All chosen providers are local (Ollama) — no key needed.</li>"}</ul></div>'
    )

    rec_rows = "".join(
        f"<tr><td>{html.escape(task)}</td>"
        + "".join(f"<td>{html.escape(_RECOMMENDED[task].get(p, '—'))}</td>" for p in _PROVIDERS)
        + "</tr>"
        for task in ("steps", "narrative", "vision", "polish")
    )
    rec_table = (
        '<div class="card"><h2>Recommended models</h2>'
        '<div class="tablewrap" style="box-shadow:none"><table class="rec">'
        '<thead><tr><th scope="col">Task</th>'
        + "".join(f'<th scope="col">{p}</th>' for p in _PROVIDERS)
        + f"</tr></thead><tbody>{rec_rows}</tbody></table></div></div>"
    )

    body = (
        _header(
            "System",
            "Configuration",
            "Pick the AI provider and model for each task. <strong>Ollama</strong> is local and "
            "private (no key, nothing leaves your network). Other providers use an API key "
            "from an environment variable.",
        )
        + saved_note
        + '<form method="post" action="/ui/config" class="cfg">'
        '<div class="grid2">'
        f"{_config_row('steps', 'Steps', steps, extra=steps_extra)}"
        f"{_config_row('narrative', 'Narration', narr, extra=passes_extra)}"
        f"{_config_row('vision', 'Vision (screenshot captions)', vis, extra=vision_extra, providers=_VISION_PROVIDERS)}"
        f"{_config_row('polish', 'Polish (optional 4th stage)', polish, extra=polish_extra)}"
        f"{document_card}"
        f"{transcription_card}"
        "</div>"
        '<div class="stickybar"><span class="muted">Saved to your per-user models.toml.</span>'
        '<button type="submit">Save configuration</button></div></form>'
        f'<div style="margin-top:24px">{rec_table}{key_panel}</div>'
        f"{_CONFIG_TEST_SCRIPT}"
    )
    return _shell("SOPForge Configuration", body, active="config", nav=nav)


def render_session_page(
    session_id,
    title,
    date,
    report,
    config,
    steps=None,
    can_regenerate=True,
    nav=None,
    walkthrough=False,
    spl_deck=False,
):
    """steps: the steps.json sidecar's list (task-05), or None for a
    session generated before it existed -- report rows for
    template-fallback/empty-metadata steps show a thumbnail + the step's
    actual text, deep-linked into the doc preview, when available; None
    degrades to the original plain step-id list (_step_section). Also
    drives the "Edit steps" card (task-06) -- every step gets a collapsed
    editor there, not only flagged ones.

    can_regenerate: whether the per-step "Regenerate with AI" button
    appears -- False for screenshots+transcript ("photo mode") sessions,
    which have no manifest ground truth to regenerate a step from."""
    # Guard against a malformed entry (not a dict, or missing "step_id") in
    # a damaged steps.json -- "corrupt degrades like missing" is the
    # contract everywhere else this file is read (server.py's
    # _load_step_state/_load_step_index), so a bad individual entry is
    # dropped here rather than raising and 500ing the whole review page.
    steps_by_id = (
        {e["step_id"]: e for e in steps if isinstance(e, dict) and "step_id" in e}
        if steps is not None
        else None
    )
    verify_items = [
        f"{c['claim_id']}: {c['text']}" if c.get("text") else c["claim_id"]
        for c in report.get("verify_claims", [])
    ]
    sections = "".join(
        [
            _step_section(
                "Template-fallback steps",
                "template_fallback_steps",
                report.get("template_fallback_steps", []),
                session_id,
                steps_by_id,
            ),
            # Photo builds only (their report always carries the key, even
            # empty); capture sessions keep the fixed three sections.
            _section(
                "Caption-fallback steps",
                "caption_fallback_steps",
                report["caption_fallback_steps"],
            )
            if "caption_fallback_steps" in report
            else "",
            _section("Verify claims", "verify_claims", verify_items),
            _step_section(
                "Empty-metadata steps",
                "empty_metadata_steps",
                report.get("empty_metadata_steps", []),
                session_id,
                steps_by_id,
            ),
        ]
    )

    def _fmt_config(values):
        # Render any section shape (steps/narrative carry anthropic/passes;
        # vision carries enabled) -- endpoint/model first, then the rest.
        ordered = [k for k in ("endpoint", "model") if k in values]
        ordered += [k for k in values if k not in ("endpoint", "model")]
        return ", ".join(f"{k}={values[k]}" for k in ordered)

    config_rows = "".join(
        f"<li><b>{html.escape(section)}</b>: {html.escape(_fmt_config(values))}</li>"
        for section, values in config.items()
    )
    sid = html.escape(session_id)
    transcript_text = report.get("transcript") or ""
    if "WARNING" in transcript_text:
        transcript_note = (
            '<section class="card" data-status="yellow"><h2>Transcript placement</h2>'
            f"<p>{html.escape(transcript_text)}</p></section>"
        )
    elif transcript_text:
        transcript_note = f'<p class="muted">Transcript: {html.escape(transcript_text)}</p>'
    else:
        transcript_note = ""
    narration_transcription_text = report.get("narration_transcription") or ""
    if "could not be transcribed" in narration_transcription_text:
        narration_transcription_note = (
            '<section class="card" data-status="yellow"><h2>Narration transcription</h2>'
            f"<p>{html.escape(narration_transcription_text)}</p></section>"
        )
    elif narration_transcription_text:
        narration_transcription_note = (
            f'<p class="muted">{html.escape(narration_transcription_text)}</p>'
        )
    else:
        narration_transcription_note = ""
    preflight = report.get("llm_preflight")
    if preflight:
        # A div, deliberately NOT a <section data-status=...> -- the review
        # page's UI-smoke test asserts an exact count of those for the three
        # fixed sidecar-report categories, and this isn't one of them.
        _preflight_color = {"ok": "--ok", "warn": "--warn", "error": "--bad"}.get(
            preflight.get("status"), "--warn"
        )
        latency = preflight.get("latency_ms")
        latency_text = f" ({latency}ms)" if latency is not None else ""
        preflight_note = (
            f'<div class="card" style="border-left:4px solid var({_preflight_color})">'
            "<strong>LLM preflight</strong> &mdash; "
            f"{html.escape(str(preflight.get('provider', '')))}/"
            f"{html.escape(str(preflight.get('model', '')))}: "
            f"{html.escape(str(preflight.get('status', '')))}{latency_text} "
            f"&mdash; {html.escape(str(preflight.get('detail', '')))}</div>"
        )
    else:
        preflight_note = ""
    manually_edited_ids = report.get("manually_edited_steps") or []
    if manually_edited_ids:
        # A div, deliberately NOT a <section data-status=...> -- same reason
        # as preflight_note above: the fixed three sidecar-report categories
        # keep their exact section count, new info is added alongside them.
        manually_edited_note = (
            '<div class="card" style="border-left:4px solid var(--warn)">'
            "<strong>Manually edited steps</strong> &mdash; "
            "these steps carry human-written text, not gated by the round-trip "
            f"check: {html.escape(', '.join(manually_edited_ids))}</div>"
        )
    else:
        manually_edited_note = ""
    regenerate_declined_ids = report.get("regenerate_declined_steps") or []
    if regenerate_declined_ids:
        # Same "div, not a data-status section" reasoning as preflight_note/
        # manually_edited_note above.
        regenerate_declined_note = (
            '<div class="card" style="border-left:4px solid var(--warn)">'
            "<strong>Regenerate didn&rsquo;t produce a result</strong> &mdash; "
            "the AI attempt fell back to the template wording, so your manual "
            f"edit was kept instead: {html.escape(', '.join(regenerate_declined_ids))}</div>"
        )
    else:
        regenerate_declined_note = ""
    uncaptured_ids = report.get("uncaptured_value_steps") or []
    if uncaptured_ids:
        # Same "div, not a data-status section" reasoning as preflight_note:
        # the UI-smoke contract counts exactly the three fixed categories.
        uncaptured_note = (
            '<div class="card" style="border-left:4px solid var(--warn)">'
            "<strong>Values not captured</strong> &mdash; these steps typed a value the "
            "capture couldn&rsquo;t record; the document marks each one "
            f"[REVIEW NEEDED]: {html.escape(', '.join(uncaptured_ids))}</div>"
        )
    else:
        uncaptured_note = ""
    omitted_ids = report.get("omitted_screenshot_steps") or []
    if omitted_ids:
        restore_rows = "".join(
            f'<li><a class="sid" href="#edit-{html.escape(i)}">{html.escape(i)}</a>'
            f'<form method="post" action="/ui/sessions/{sid}/steps/{html.escape(i)}'
            '/keep-screenshot"><button type="submit" class="secondary">Show screenshot'
            "</button></form></li>"
            for i in omitted_ids
        )
        omitted_note = (
            '<div class="card"><h2>Omitted screenshots</h2>'
            '<p class="help" style="margin:-4px 0 8px">Left out because they repeat the '
            "previous screen exactly. Show one to put it back in every export.</p>"
            f'<ul class="restore">{restore_rows}</ul></div>'
        )
    else:
        omitted_note = ""
    phases_card = _phases_card(sid, report.get("phases"), steps_by_id)
    document_card = _document_card(sid, report.get("document"))
    spl_overflow = [str(t) for t in report.get("spl_text_overflow") or []]
    spl_note = ""
    if report.get("spl_error"):
        spl_note = (
            '<div class="card" style="border-left:4px solid var(--bad)">'
            "<strong>SPL deck not built</strong> &mdash; the other exports are fine: "
            f"{html.escape(str(report['spl_error']))}</div>"
        )
    elif spl_overflow:
        items = "".join(f"<li>{html.escape(t)}</li>" for t in spl_overflow)
        spl_note = (
            '<div class="card" style="border-left:4px solid var(--warn)">'
            "<strong>SPL deck: text still too long at 12pt</strong> &mdash; the deck "
            f"ships, but check these boxes in PowerPoint:<ul>{items}</ul></div>"
        )
    downloads = "".join(
        f'<li><a href="/sessions/{sid}/{path}" data-download="{label}">'
        f"{_icon('download')}{label}</a></li>"
        for path, label in (
            ("doc.docx", "docx"),
            ("doc.pdf", "pdf"),
            ("doc.single.html", "single-file html"),
            ("export.md.zip", "markdown bundle (zip)"),
        )
    )
    # The 6d click-through walkthrough and 6f SPL deck, when this session has
    # them. Deliberately no data-download attribute: that marks the four
    # document formats.
    if walkthrough:
        downloads += (
            f'<li><a href="/sessions/{sid}/walkthrough.html" title="Click-through walkthrough '
            f'(single HTML file)">{_icon("download")}walkthrough</a></li>'
        )
    if spl_deck:
        downloads += (
            f'<li><a href="/sessions/{sid}/spl.pptx" title="Single Point Lesson deck '
            f'(PowerPoint)">{_icon("download")}SPL deck</a></li>'
        )
    actions = (
        f'<form method="post" action="/ui/sessions/{sid}/rerender">'
        '<button type="submit" class="secondary">Re-render</button></form>'
        + (
            f'<form method="post" action="/ui/sessions/{sid}/rerender?discard_edits=1">'
            '<button type="submit" class="secondary">Re-render from scratch '
            "(discard my edits)</button></form>"
            if manually_edited_ids
            else ""
        )
        + f'<form method="post" action="/ui/sessions/{sid}/delete">'
        '<button type="submit" class="danger">Delete</button></form>'
    )
    report_col = (
        '<div class="card"><h2>Downloads</h2>'
        f'<ul class="dl">{downloads}</ul></div>'
        f"{document_card}"
        '<p class="help" style="margin:0 0 16px">Every recorded step is included &mdash; the '
        "document has one step per captured action, nothing skipped. The report below only "
        "flags steps worth a second look: <em>template-fallback</em> steps are still complete "
        "and factually correct, just written from the captured data rather than the language "
        "model.</p>"
        f"{preflight_note}"
        f"{manually_edited_note}"
        f"{regenerate_declined_note}"
        f"{transcript_note}"
        f"{narration_transcription_note}"
        f"{uncaptured_note}"
        f"{spl_note}"
        f"{sections}"
        f"{phases_card}"
        f"{omitted_note}"
        f"{_edit_steps_card(session_id, steps, can_regenerate)}"
        '<div class="card"><h2>Narration transcript</h2>'
        f'<form method="post" action="/ui/sessions/{sid}/transcript" enctype="multipart/form-data">'
        '<div class="field"><label for="transcript_file">Add or replace a transcript (.txt or '
        ".md), then re-render</label>"
        '<input type="file" name="transcript_file" id="transcript_file" accept=".txt,.md,.json" '
        "required>"
        '<p class="help">Label blocks &ldquo;Step 1:&rdquo;, &ldquo;1.&rdquo; or &ldquo;## Step '
        "1&rdquo; to place each under its step, or one line (or paragraph) per step, in "
        "order.</p></div>"
        '<button type="submit" class="secondary">Add transcript &amp; re-render</button></form>'
        "</div>"
        '<div class="card"><h2>Config (read-only)</h2>'
        f'<ul class="sessions">{config_rows}</ul></div>'
    )
    body = (
        _header(
            _BACK,
            html.escape(title),
            f'<span class="meta"><span>{html.escape(date)}</span><span>{sid}</span></span>',
            actions,
        )
        + '<div class="layout">'
        '<div class="preview"><div class="card"><div class="bar"><span>Document preview</span>'
        f'<a href="/sessions/{sid}/doc.html" target="_blank" rel="noopener">Open in new tab</a>'
        "</div>"
        f'<iframe name="docpreview" src="/sessions/{sid}/doc.html" title="Document preview">'
        "</iframe></div></div>"
        f"<div>{report_col}</div>"
        "</div>" + _LIGHTBOX_HTML + _LIGHTBOX_SCRIPT
    )
    return _shell("SOPForge Review", body, nav=nav)
