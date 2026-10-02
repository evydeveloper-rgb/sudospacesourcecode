"""Turn the Stitch 'WiFi Setup (Sudo Branded)' export into the live portal.

Markup is kept verbatim. Only three things change:
  1. CDN <script>/<link> -> vendored /assets (the Pi has no internet here)
  2. the remote wordmark <img> -> the local SVG
  3. the four hardcoded network rows -> one container the JS fills, reusing
     the exact class strings lifted out of this same file
"""
import pathlib, re, sys
sys.path.insert(0, str(pathlib.Path(__file__).parent))
from icons import svg, ICONS

SRC = pathlib.Path(__file__).resolve().parent / "stitch-source.html"
OUT = pathlib.Path(__file__).resolve().parent / "templates" / "index.html"

h = SRC.read_text(encoding="utf-8")

# --- pull the exact row classes out of the design, so JS renders identically
rows = re.findall(r'<button class="(flex items-center justify-between p-lg[^"]*)"', h)
SEL_CLS = next(c for c in rows if "secondary-container/50" in c)
IDLE_CLS = next(c for c in rows if "surface-variant/50" in c)

# --- head: drop CDN + inline tailwind config, point at vendored assets
h = re.sub(r'<script src="https://cdn\.tailwindcss\.com[^"]*"></script>\s*', '', h)
h = re.sub(r'<link[^>]*fonts\.googleapis\.com[^>]*>\s*', '', h)
h = re.sub(r'<script id="tailwind-config">.*?</script>\s*', '', h, flags=re.S)
h = h.replace('</head>',
    '<link rel="stylesheet" href="/assets/fonts.css">\n'
    '<link rel="stylesheet" href="/assets/stitch.css">\n'
    '<meta name="theme-color" content="#fbfbfe">\n'
    '</head>')

# --- drop the export's fixed page height ---------------------------------
# Stitch emits min-height: max(884px, 100dvh). iOS shows a captive portal in a
# short sheet, so an 884px floor makes the page taller than the viewport and
# pushes the sticky header and the mt-auto password block out of place.
h = h.replace("min-height: max(884px, 100dvh);", "min-height: 100dvh;")

# --- local wordmark instead of the googleusercontent one
h = re.sub(r'<img alt="Sudo Wordmark"([^>]*?)src="https://[^"]*"([^>]*)>',
           r'<img alt="sudo"\1src="/assets/sudo-wordmark.svg"\2>', h)

# --- our own options, styled with the design's own tokens ---------------
# Error banner: app.py substitutes {{ERROR}}/{{ERROR_CLASS}}. Without this a
# wrong password fails silently, which is how it behaved before.
ERR = ('<div class="{{ERROR_CLASS}} rounded-xl border border-error bg-error-container '
       'text-on-error-container px-lg py-md font-body-md text-body-md" id="err" '
       'role="alert">{{ERROR}}</div>\n')
h = h.replace('<!-- Network List -->', ERR + '<!-- Network List -->')

# Rescan: scanning in AP mode is unreliable, so the user needs a way to retry
# without reloading. Matches the design's icon-button treatment.
h = h.replace(
    '<h2 class="font-label-mono text-label-mono text-outline mb-xs">AVAILABLE NETWORKS</h2>',
    '<div class="flex items-center justify-between mb-xs">'
    '<h2 class="font-label-mono text-label-mono text-outline">AVAILABLE NETWORKS</h2>'
    '<button type="button" id="refresh" aria-label="Rescan" '
    'class="w-9 h-9 rounded-full bg-surface-container-low border border-surface-variant '
    'flex items-center justify-center text-on-surface-variant hover:text-primary '
    'hover:border-primary/30 transition-colors active:scale-95">'
    '<span class="material-symbols-outlined text-xl">refresh</span></button>'
    '</div>')

# Where to find it afterwards.
h = h.replace('</main>',
    '<p class="font-body-md text-on-surface-variant text-center text-sm pb-xl">'
    'After this, sudo restarts and joins your network. '
    "You'll find it at "
    '<span class="font-label-mono text-on-surface">{{DASHBOARD_URL}}</span>'
    '</p></main>', 1)

# --- replace the whole hardcoded network list with a live container
# Anchor on the heading text, not its full tag — the heading gets rewritten
# above to sit beside the rescan button, so matching the original tag fails.
MARK = 'AVAILABLE NETWORKS</h2>'
after_h2 = h.index(MARK) + len(MARK)
div_start = h.index('<div class="flex flex-col gap-sm">', after_h2)
# walk to the matching </div> of that container
depth, i = 0, div_start
while True:
    nxt_open = h.find('<div', i + 1)
    nxt_close = h.find('</div>', i + 1)
    if nxt_close == -1:
        raise SystemExit("unbalanced network list")
    if nxt_open != -1 and nxt_open < nxt_close:
        depth += 1; i = nxt_open
    else:
        if depth == 0:
            list_end = nxt_close + len('</div>')
            break
        depth -= 1; i = nxt_close

h = h[:div_start] + '<div class="flex flex-col gap-sm" id="wifi-list">\n' \
    '<div class="font-body-md text-body-md text-on-surface-variant text-center py-lg">Looking for networks\u2026</div>\n' \
    '</div>' + h[list_end:]

# --- give the password input + submit button ids, keep every class
h = h.replace('id="wifi-password"', 'id="wifi-password" name="password" autocomplete="current-password"')
h = re.sub(r'(<button aria-label="Toggle password visibility" class=")', r'<button type="button" id="peek" aria-label="Toggle password visibility" class="', h, count=1)
h = h.replace('<button aria-label="Toggle password visibility" class=" class=', '<button type="button" id="peek" aria-label="Toggle password visibility" class=')
h = re.sub(r'data-icon="visibility_off">visibility_off</span>',
           r'data-icon="visibility_off" id="peek-icon">visibility_off</span>', h, count=1)
h = re.sub(r'(<button class="px-8 py-4 rounded-xl font-button-text[^"]*")',
           r'\1 type="submit" id="submit-btn" disabled', h, count=1)

# label for the password section becomes dynamic
h = re.sub(r'(<label class="font-label-mono text-label-mono text-outline" for="wifi-password">)[^<]*</label>',
           r'\1PASSWORD</label>', h)
h = h.replace('<label class="font-label-mono text-label-mono text-outline" for="wifi-password">',
              '<label class="font-label-mono text-label-mono text-outline" for="wifi-password" id="pw-label">')

# --- wrap everything in the form the portal posts to
h = h.replace('<main class=', '<form method="POST" action="/connect" id="form"><main class=')
h = h.replace('</main>', '</main>'
    '<input type="hidden" name="ssid" id="ssid-hidden">'
    '<input type="hidden" name="country" id="country" value="US">'
    '</form>')

# --- centre the submit button --------------------------------------------
# The export right-aligns it (justify-end), which on a phone reads as a button
# that has drifted off centre rather than as deliberate alignment. Centre it,
# and let it fill the width on small screens where a lone right-hand button
# looks stray.
assert '<div class="flex justify-end gap-md pt-2">' in h, "submit row not found"
h = h.replace('<div class="flex justify-end gap-md pt-2">',
              '<div class="flex justify-center gap-md pt-2">')
assert 'flex items-center gap-2 bg-primary-container"' in h, "submit button not found"
h = h.replace('flex items-center gap-2 bg-primary-container"',
              'flex items-center justify-center gap-2 bg-primary-container w-full sm:w-auto"')

# --- a username field, for networks that want one ------------------------
# School and office networks are usually WPA2-Enterprise: a password on its
# own gets you nowhere. nmcli reports those as "802.1X" in the scan, so the
# field is added here and revealed only when such a network is picked --
# asking everyone at home for a username they do not have would be worse
# than the problem it solves.
_pw_block = '<div class="flex flex-col gap-sm">\n<label class="font-label-mono text-label-mono text-outline" for="wifi-password" id="pw-label">PASSWORD</label>'
assert _pw_block in h, "password block not found"
h = h.replace(_pw_block, '''<div class="flex flex-col gap-sm hidden" id="user-row">
<label class="font-label-mono text-label-mono text-outline" for="wifi-username">USERNAME</label>
<input class="w-full bg-surface/80 backdrop-blur-md border border-surface-variant rounded-xl px-lg py-4 font-body-md text-body-md text-on-surface focus:outline-none focus:border-primary/50 transition-colors" id="wifi-username" name="username" autocomplete="username" autocapitalize="none" autocorrect="off" spellcheck="false" placeholder="your school or work username">
<p class="font-body-md text-on-surface-variant text-xs">This network asks for a username as well as a password.</p>
</div>
''' + _pw_block, 1)

# --- drop the mock bottom nav --------------------------------------------
# The export carries a WIFI / THEME / INSTALL / BOT bar. Only Wi-Fi setup
# exists here, and the buttons have no handlers, so it advertises three
# screens that are not there. Theme now lives in the dashboard's Settings.
h = re.sub(r'<!-- BottomNavBar -->.*?</nav>\s*', '', h, flags=re.S)
# ...and the spacer that only existed to clear it (a bare comment here).
h = re.sub(r'<!-- Spacer for BottomNavBar -->\s*', '', h)
h = re.sub(r'<div class="h-20[^"]*pb-safe"></div>\s*', '', h)
assert 'BottomNavBar' not in h and '<nav' not in h, "bottom nav survived"

# --- swap the icon font for inline SVG -----------------------------------
# The Material Symbols woff2 carries no ligature substitutions, so the span
# text never becomes a glyph and renders as the literal word. SVG removes the
# font dependency and the failure mode with it.
def _span_to_svg(m):
    classes, name = m.group(1), m.group(3).strip()
    classes = classes.replace("material-symbols-outlined", "inline-block align-middle").strip()
    return svg(name, classes) if name in ICONS else m.group(0)

h = re.sub(
    r'<span class="([^"]*material-symbols-outlined[^"]*)"([^>]*)>([a-z_]+)</span>',
    _span_to_svg, h)

def _icon_js():
    import json
    return json.dumps({k: svg(k) for k in
        ("add", "lock", "check_circle", "visibility", "visibility_off",
         "signal_wifi_1_bar", "signal_wifi_2_bar", "signal_wifi_3_bar", "signal_wifi_4_bar")})

# --- template hooks + behaviour
JS = '''
<script>
var ICON = %s;
var SEL_CLS = %r;
var IDLE_CLS = %r;
var selectedSSID = null;

var TZ = {"Asia/Jakarta":"ID","Asia/Makassar":"ID","Asia/Jayapura":"ID","Asia/Pontianak":"ID",
"Asia/Singapore":"SG","Asia/Kuala_Lumpur":"MY","Asia/Bangkok":"TH","Asia/Ho_Chi_Minh":"VN",
"Asia/Manila":"PH","Asia/Tokyo":"JP","Asia/Seoul":"KR","Asia/Shanghai":"CN","Asia/Hong_Kong":"HK",
"Asia/Taipei":"TW","Asia/Kolkata":"IN","Asia/Calcutta":"IN","Asia/Dhaka":"BD","Asia/Karachi":"PK",
"Asia/Dubai":"AE","Asia/Riyadh":"SA","Asia/Jerusalem":"IL","Europe/London":"GB","Europe/Dublin":"IE",
"Europe/Berlin":"DE","Europe/Amsterdam":"NL","Europe/Paris":"FR","Europe/Madrid":"ES","Europe/Rome":"IT",
"Europe/Warsaw":"PL","Europe/Stockholm":"SE","Europe/Oslo":"NO","Europe/Copenhagen":"DK",
"Europe/Helsinki":"FI","Europe/Vienna":"AT","Europe/Zurich":"CH","Europe/Brussels":"BE",
"Europe/Lisbon":"PT","Europe/Athens":"GR","Europe/Istanbul":"TR","Europe/Moscow":"RU",
"America/New_York":"US","America/Chicago":"US","America/Denver":"US","America/Los_Angeles":"US",
"America/Anchorage":"US","America/Phoenix":"US","Pacific/Honolulu":"US","America/Toronto":"CA",
"America/Vancouver":"CA","America/Mexico_City":"MX","America/Sao_Paulo":"BR","America/Bogota":"CO",
"America/Santiago":"CL","America/Lima":"PE","Australia/Sydney":"AU","Australia/Melbourne":"AU",
"Australia/Brisbane":"AU","Australia/Perth":"AU","Pacific/Auckland":"NZ","Africa/Johannesburg":"ZA",
"Africa/Lagos":"NG","Africa/Cairo":"EG","Africa/Nairobi":"KE"};
try { var z = Intl.DateTimeFormat().resolvedOptions().timeZone;
      if (TZ[z]) document.getElementById("country").value = TZ[z]; } catch (e) {}

function sigIcon(s){s=parseInt(s,10)||0;
  return s>=75?"signal_wifi_4_bar":s>=50?"signal_wifi_3_bar":s>=25?"signal_wifi_2_bar":"signal_wifi_1_bar";}

function mkRow(net, manual){
  var b=document.createElement("button");
  b.type="button"; b.className=IDLE_CLS;
  b.innerHTML =
    '<div class="flex items-center gap-md relative z-10">'+
      '<div class="w-12 h-12 rounded-full bg-surface-container flex items-center justify-center group-hover:bg-primary-container/10 transition-colors js-ic">'+
        '<span class="text-on-surface-variant group-hover:text-primary transition-colors text-2xl js-sig">'+
          (manual?ICON.add:ICON[sigIcon(net.signal)])+'</span>'+
      '</div>'+
      '<div class="flex flex-col gap-1">'+
        '<span class="font-body-md text-body-md font-medium text-on-surface group-hover:text-primary transition-colors text-xl js-nm"></span>'+
        '<span class="font-label-mono text-label-mono text-secondary-container text-sm hidden js-st">Selected</span>'+
      '</div>'+
    '</div>'+
    '<div class="flex items-center gap-2 relative z-10">'+
      ((!manual && net.security)?'<span class="text-outline-variant text-xl js-lk">'+ICON.lock+'</span>':'')+
      '<span class="material-symbols-outlined text-secondary-container hidden js-ck" style="font-variation-settings: \\'FILL\\' 1;">check_circle</span>'+
    '</div>';
  b.querySelector(".js-nm").textContent = manual ? "Other network\\u2026" : net.ssid;
  b.addEventListener("click", function(){ manual ? pickManual(b) : pick(b, net.ssid, net.enterprise); });
  return b;
}

function reset(){
  document.querySelectorAll("#wifi-list button").forEach(function(el){
    el.className = IDLE_CLS;
    var ck=el.querySelector(".js-ck"), st=el.querySelector(".js-st"),
        lk=el.querySelector(".js-lk"), ic=el.querySelector(".js-ic"), sg=el.querySelector(".js-sig");
    if(ck) ck.classList.add("hidden");
    if(st) st.classList.add("hidden");
    if(lk) lk.classList.remove("hidden");
    if(ic) ic.className="w-12 h-12 rounded-full bg-surface-container flex items-center justify-center group-hover:bg-primary-container/10 transition-colors js-ic";
    if(sg) sg.className="text-on-surface-variant group-hover:text-primary transition-colors text-2xl js-sig";
  });
}

function mark(el){
  el.className = SEL_CLS;
  var ck=el.querySelector(".js-ck"), st=el.querySelector(".js-st"),
      lk=el.querySelector(".js-lk"), ic=el.querySelector(".js-ic"), sg=el.querySelector(".js-sig");
  if(ck) ck.classList.remove("hidden");
  if(st) st.classList.remove("hidden");
  if(lk) lk.classList.add("hidden");
  if(ic) ic.className="w-12 h-12 rounded-full bg-secondary-container/10 flex items-center justify-center js-ic";
  if(sg) sg.className="text-secondary-container text-2xl js-sig";
}

function pick(el, ssid, enterprise){
  reset(); mark(el);
  selectedSSID = ssid;
  document.getElementById("ssid-hidden").value = ssid;
  document.getElementById("manual-wrap").classList.add("hidden");
  document.getElementById("pw-label").textContent = "PASSWORD FOR " + ssid.toUpperCase();
  showUser(enterprise);
  document.getElementById(enterprise ? "wifi-username" : "wifi-password").focus();
  upd();
}

// Only networks that actually want a username get asked for one. Everyone
// else would just see a box they cannot fill.
function showUser(on){
  var row = document.getElementById("user-row");
  row.classList.toggle("hidden", !on);
  if(!on) document.getElementById("wifi-username").value = "";
}

function pickManual(el){
  reset(); mark(el);
  selectedSSID = null;
  document.getElementById("ssid-hidden").value = "";
  document.getElementById("manual-wrap").classList.remove("hidden");
  document.getElementById("pw-label").textContent = "PASSWORD";
  showUser(false);
  document.getElementById("manual-ssid").focus();
  upd();
}

function upd(){
  var mOn = !document.getElementById("manual-wrap").classList.contains("hidden");
  var mVal = document.getElementById("manual-ssid").value.trim();
  document.getElementById("submit-btn").disabled = !(selectedSSID || (mOn && mVal));
}

function render(nets){
  var L=document.getElementById("wifi-list");
  L.replaceChildren();
  if(!nets || !nets.length){
    L.innerHTML='<div class="font-body-md text-body-md text-on-surface-variant text-center py-lg">No networks found.</div>';
  } else {
    nets.forEach(function(n){ L.appendChild(mkRow(n,false)); });
  }
  L.appendChild(mkRow(null,true));
}

function scanWifi(){
  var L=document.getElementById("wifi-list");
  L.innerHTML='<div class="font-body-md text-body-md text-on-surface-variant text-center py-lg">Looking for networks\\u2026</div>';
  fetch("/scan").then(function(r){return r.json();}).then(render).catch(function(){
    L.innerHTML='<div class="font-body-md text-body-md text-on-surface-variant text-center py-lg">Scan failed. <button type="button" class="text-primary underline font-semibold" onclick="scanWifi()">Try again</button></div>';
  });
}

document.getElementById("peek").addEventListener("click", function(){
  var p=document.getElementById("wifi-password"); var on=p.type==="password";
  p.type = on ? "text" : "password";
  document.getElementById("peek-icon").textContent = on ? "visibility" : "visibility_off";
});
document.getElementById("manual-ssid").addEventListener("input", upd);
document.getElementById("refresh").addEventListener("click", scanWifi);
var eb = document.getElementById("err");
if (eb && !eb.textContent.trim()) eb.classList.add("hidden");
document.getElementById("form").addEventListener("submit", function(e){
  if(!document.getElementById("manual-wrap").classList.contains("hidden")){
    var v=document.getElementById("manual-ssid").value.trim();
    if(!v){ e.preventDefault(); return; }
    document.getElementById("ssid-hidden").value=v;
  }
  document.getElementById("submit-btn").disabled=true;
});
scanWifi();
</script>
''' % (_icon_js(), SEL_CLS, IDLE_CLS)

# manual-entry field, styled like the design's own inputs
MANUAL = '''<section class="hidden flex-col gap-sm" id="manual-wrap">
<label class="font-label-mono text-label-mono text-outline" for="manual-ssid">NETWORK NAME</label>
<div class="relative flex items-center w-full group">
<span class="material-symbols-outlined absolute left-lg text-outline-variant group-focus-within:text-primary transition-colors">wifi</span>
<input class="w-full bg-surface/80 backdrop-blur-md border border-surface-variant rounded-xl pl-14 pr-4 py-4 font-body-md text-body-md text-on-surface focus:outline-none focus:border-primary/50 focus:ring-4 focus:ring-primary/10 transition-all placeholder:text-outline-variant shadow-sm" id="manual-ssid" type="text" autocomplete="off" placeholder="Type the network name">
</div>
</section>
'''
h = h.replace('<!-- Password Input Area', MANUAL + '<!-- Password Input Area')
h = h.replace('</body>', JS + '</body>')

# Final icon pass. The manual-entry field and the appended script are added
# after the first pass, so they still carry icon-font spans; convert those too.
h = re.sub(
    r'<span class="([^"]*material-symbols-outlined[^"]*)"([^>]*)>([a-z_]+)</span>',
    _span_to_svg, h)
assert "material-symbols-outlined" not in h, "an icon span survived the swap"

OUT.write_text(h, encoding="utf-8", newline="\n")
print(f"  portal built from the Sudo Branded export ({OUT.stat().st_size} bytes)")
print(f"  selected row classes lifted verbatim: {SEL_CLS[:58]}…")
