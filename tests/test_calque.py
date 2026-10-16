import asyncio, subprocess, time, sys, os
from playwright.async_api import async_playwright
srv = subprocess.Popen([sys.executable,"-m","http.server","8765","-d","."],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
time.sleep(1)
URL="http://localhost:8765/"
ok=lambda c,m: print(("✅ " if c else "❌ ")+m) or c
results=[]
ALL_ERRS=[]   # (scénario, erreurs) de toutes les pages ouvertes : contrôle global en fin de test
async def ctx_page(b, delay=6000, deny=False, extra="", vp=(390,844), name=None):
    ctx = await b.new_context(viewport={"width":vp[0],"height":vp[1]}, has_touch=True, is_mobile=True)
    if not deny: await ctx.grant_permissions(["camera"], origin=URL.rstrip('/'))
    page = await ctx.new_page()
    errs=[]; page.on("pageerror", lambda e: errs.append(str(e)))
    page.on("console", lambda m: m.type=="error" and errs.append(m.text))
    ALL_ERRS.append((name or f"page {len(ALL_ERRS)+1} ({vp[0]}×{vp[1]})", errs))
    js = ("navigator.mediaDevices.getUserMedia = () => new Promise((_,j)=>setTimeout(()=>j(new DOMException('no','NotAllowedError')),%d));" % delay) if deny else \
         ("const real=navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices); navigator.mediaDevices.getUserMedia=c=>new Promise(r=>setTimeout(()=>r(real(c)),%d));" % delay)
    js += " const _c=navigator.mediaDevices.getUserMedia; window.__gum=0; navigator.mediaDevices.getUserMedia=c=>{window.__gum++; return _c.call(navigator.mediaDevices,c)};"
    await page.add_init_script(js + extra)
    reqs=[]; page.on("request", lambda r: reqs.append(r.url)); page.reqs = reqs
    await page.goto(URL); return ctx, page, errs

async def start(page):
    async with page.expect_file_chooser(timeout=1500) as fc: await page.tap("#pick")
    await (await fc.value).set_files("tests/chat.png")
    await page.wait_for_function("document.querySelector('#intro').classList.contains('gone')", timeout=10000)

# Caméra de téléphone simulée : capacités en plus, applyConstraints enregistré dans window.__ac
FAKE_CAPS = """
const P = MediaStreamTrack.prototype, gc = P.getCapabilities, gs = P.getSettings;
window.__ac = [];
P.getCapabilities = function(){ return Object.assign(gc.call(this), {torch:true,
  focusMode:['continuous','manual'], focusDistance:{min:0, max:1, step:0.01}}); };
P.getSettings = function(){ return Object.assign(gs.call(this), {focusMode:'continuous', focusDistance:0.35}); };
P.applyConstraints = function(c){ window.__ac.push(c); return Promise.resolve(); };
"""
# Caméra sans mise au point réglable
NO_FOCUS = """
const P = MediaStreamTrack.prototype, gc = P.getCapabilities;
P.getCapabilities = function(){ const c = gc.call(this); delete c.focusMode; delete c.focusDistance; return c; };
"""
# Téléphone verrouillé puis déverrouillé : visibilité pilotable, Wake Lock simulé (compte les demandes)
PHONE_LOCK = """
window.__vis = 'visible'; window.__wl = 0;
Object.defineProperty(document, 'visibilityState', { get: () => window.__vis });
Object.defineProperty(document, 'hidden', { get: () => window.__vis === 'hidden' });
Object.defineProperty(navigator, 'wakeLock', { value: { request: () => {
  window.__wl++; const s = new EventTarget(); s.release = () => s.dispatchEvent(new Event('release'));
  window.__sentinel = s; return Promise.resolve(s); } } });
"""
LOCK = """(() => { window.__vis = 'hidden';
  cam.srcObject.getTracks().forEach(t => t.stop()); cam.pause(); window.__sentinel.release();
  document.dispatchEvent(new Event('visibilitychange')); })()"""
UNLOCK = "(() => { window.__vis = 'visible'; document.dispatchEvent(new Event('visibilitychange')); })()"
# Contrôles visuels de la DA, évalués dans la page
AUDIT = r"""(scope) => {
  const vis = e => { const r = e.getBoundingClientRect(), c = getComputedStyle(e);
    return r.width > 0 && r.height > 0 && c.visibility != 'hidden' && c.display != 'none' && !e.closest('[hidden]'); };
  const rgb = s => (s.match(/[\d.]+/g) || []).map(Number);
  const lum = ([r,g,b]) => { const f = v => (v/=255) <= .03928 ? v/12.92 : ((v+.055)/1.055)**2.4; return .2126*f(r)+.7152*f(g)+.0722*f(b); };
  const bgOf = e => { for (; e; e = e.parentElement) { const c = rgb(getComputedStyle(e).backgroundColor);
    if (c.length == 3 || c[3] > .9) return c.slice(0,3); if (e.id == 'stage') return null; } return [255,255,255]; };
  const sel = scope == 'intro' ? '#intro label.btn, #intro button' : '#top button, #top label.btn, #panel button, #panel input, #peek';
  const targets = [...document.querySelectorAll(sel)].filter(vis);
  const small = targets.filter(e => { const r = e.getBoundingClientRect(); return r.width < 44 || r.height < 44; })
                       .map(e => e.id || e.textContent.trim());
  const stretched = [...document.querySelectorAll('#top button, #top label.btn, #panel button')].filter(vis).filter(e => {
    const w = e.getBoundingClientRect().width, old = e.style.cssText;
    e.style.width = 'max-content'; e.style.flex = 'none'; const n = Math.max(44, e.getBoundingClientRect().width);
    e.style.cssText = old; return w > n + 2; }).map(e => e.id || e.textContent.trim());
  const texts = [...document.querySelectorAll(scope == 'intro' ? '#intro *' : '#top *, #panel *, #peek')].filter(vis)
    .filter(e => [...e.childNodes].some(n => n.nodeType == 3 && n.textContent.trim()));
  const lowContrast = texts.map(e => { const bg = bgOf(e); if (!bg) return null;
    const a = lum(rgb(getComputedStyle(e).color)), b = lum(bg), r = (Math.max(a,b)+.05)/(Math.min(a,b)+.05);
    return r < 4.5 ? `${e.id || e.textContent.trim().slice(0,15)} (${r.toFixed(1)})` : null; }).filter(Boolean);
  const radius = targets.filter(e => parseFloat(getComputedStyle(e).borderRadius) > 0).map(e => e.id || e.textContent.trim());
  return { small, stretched, lowContrast, radius };
}"""
# Pixels opaques de l'image de contours : tous noirs (R, G, B < 40), aucun rose
EDGE_PIXELS = r"""new Promise(res => { const im = new Image(); im.onload = () => {
  const c = document.createElement('canvas'); c.width = im.width; c.height = im.height;
  const x = c.getContext('2d'); x.drawImage(im, 0, 0); const d = x.getImageData(0, 0, c.width, c.height).data;
  let n = 0, notBlack = 0, pink = 0, max = 0;
  for (let i = 0; i < d.length; i += 4) { if (d[i+3] < 128) continue; n++;
    const m = Math.max(d[i], d[i+1], d[i+2]); max = Math.max(max, m);
    if (m >= 40) notBlack++; if (d[i] > 180 && d[i+1] < 140 && d[i+2] > 100) pink++; }
  res({ n, notBlack, pink, max }); }; im.src = art.src; })"""
PANEL = "(()=>{const e=document.querySelector('#panel'), r=e.getBoundingClientRect(), c=getComputedStyle(e); return {l:r.left,r:r.right,t:r.top,b:r.bottom,w:r.width,bw:c.borderTopWidth,sh:c.boxShadow}})()"
FONTS = r"""document.fonts.ready.then(()=>[...document.fonts].filter(f=>f.status=='loaded').map(f=>f.family.replace(/"/g,'')+' '+f.weight))"""

async def layout(b, vp, name):
    ctx,page,errs = await ctx_page(b, delay=0, vp=vp, name=name)
    W,H = vp
    show_resume = "v => { const r = document.querySelector('#resume'); if (r) r.hidden = !v; }"
    await page.evaluate(show_resume, True)   # aperçu de la carte Reprendre (branchée au point 5)
    await page.wait_for_timeout(300)
    await page.screenshot(path=f"tests/test_accueil_{name}.png")
    a = await page.evaluate(AUDIT, 'intro')
    results.append(ok(not a['small'] and not a['lowContrast'], f"{name} accueil : cibles ≥ 44px {a['small'] or 'ok'}, contraste ≥ 4.5 {a['lowContrast'] or 'ok'}"))
    await page.evaluate(show_resume, False)
    await start(page)
    await page.tap('[data-mode="lines"]'); await page.tap('#gridBtn'); await page.wait_for_timeout(500)
    await page.screenshot(path=f"tests/test_dessin_{name}.png")
    a = await page.evaluate(AUDIT, 'dessin')
    results.append(ok(not a['small'], f"{name} dessin : cibles tactiles ≥ 44px {a['small'] or ''}"))
    results.append(ok(not a['lowContrast'], f"{name} dessin : contraste texte ≥ 4.5:1 {a['lowContrast'] or ''}"))
    results.append(ok(not a['stretched'], f"{name} dessin : boutons jamais étirés {a['stretched'] or ''}"))
    results.append(ok(not a['radius'], f"{name} dessin : angles droits {a['radius'] or ''}"))
    p = await page.evaluate(PANEL)
    if name == "telephone":
        good = p['l']==0 and p['r']==W and abs(p['b']-H)<1 and p['bw']=='2px'
    elif name == "paysage":
        good = p['r']==W and p['t']==0 and abs(p['b']-H)<1 and p['w']<=300 and p['l']>W/2
    else:
        good = p['w']<=420 and abs((p['l']+p['r'])/2-W/2)<2 and H-p['b']>=12 and p['bw']=='2px' and '6px 6px 0px' in p['sh']
    results.append(ok(good, f"{name} : panneau placé ({p['l']:.0f}→{p['r']:.0f} × {p['t']:.0f}→{p['b']:.0f}, bord {p['bw']})"))
    if name == "paysage":   # l'image se centre dans la zone libre, à gauche du panneau
        r = await page.evaluate("(()=>{const r=art.getBoundingClientRect();return [r.left,r.right,(r.left+r.right)/2]})()")
        results.append(ok(r[1] <= p['l']+1 and abs(r[2]-p['l']/2) < 3, f"paysage : image centrée hors du panneau ({r[0]:.0f}→{r[1]:.0f}, panneau à {p['l']:.0f})"))
    fonts = await page.evaluate(FONTS)
    ext = [u for u in page.reqs if not u.startswith(URL) and not u.startswith(("data:", "blob:"))]
    results.append(ok(any('Bricolage' in f for f in fonts) and any('Space Mono' in f for f in fonts) and not ext,
                      f"{name} : polices locales chargées {sorted(set(fonts))}, requêtes externes {ext or 'aucune'}"))
    results.append(ok(not errs, f"{name} : aucune erreur JS" + ("" if not errs else f" : {errs}")))
    await ctx.close()

# ---------- verrou ancré : vidéos factices .y4m d'une feuille avec quelques traits ----------
VW, VH, FPS = 360, 640, 15
# Scène dessinée dans un canvas, renvoyée en plan Y (niveaux de gris) base64.
# p = { dx, dy, rot (°), hand (x du bord gauche de la main, ou None) } ; toute la scène bouge = le téléphone bouge
SCENE = r"""(args) => {
  const [W, H, frames, seed0] = args;
  const c = document.createElement('canvas'); c.width = W; c.height = H;
  const x = c.getContext('2d', { willReadFrequently: true });
  let seed = 7; const rnd = () => (seed = (seed * 16807) % 2147483647) / 2147483647;
  const strokes = [];
  for (let i = 0; i < 16; i++) {                       // traits au crayon : lignes brisées, courbes, cercles
    const k = i % 4, px = 60 + rnd() * 240, py = 80 + rnd() * 480, pts = [];
    for (let j = 0; j < 3 + (rnd() * 4 | 0); j++) pts.push([px + (rnd() - .5) * 140, py + (rnd() - .5) * 140]);
    strokes.push({ k, pts, w: 2 + rnd() * 2, g: 25 + rnd() * 50, r: 8 + rnd() * 20 });
  }
  seed = seed0;
  const out = [];
  for (const p of frames) {
    x.setTransform(1, 0, 0, 1, 0, 0);
    x.fillStyle = 'rgb(72,72,72)'; x.fillRect(0, 0, W, H);   // table unie
    x.translate(W / 2 + p.dx, H / 2 + p.dy); x.rotate(p.rot * Math.PI / 180); x.translate(-W / 2, -H / 2);
    x.fillStyle = 'rgb(236,234,228)'; x.fillRect(30, 40, 300, 560);   // la feuille
    x.lineCap = 'round'; x.lineJoin = 'miter';
    for (const s of strokes) {
      x.strokeStyle = `rgb(${s.g},${s.g},${s.g})`; x.lineWidth = s.w; x.beginPath();
      if (s.k === 3) x.arc(s.pts[0][0], s.pts[0][1], s.r, 0, 6.2832);
      else if (s.k === 2) { x.moveTo(...s.pts[0]); x.quadraticCurveTo(...s.pts[1], ...s.pts[2]); }
      else { x.moveTo(...s.pts[0]); s.pts.slice(1).forEach(q => x.lineTo(...q)); }
      x.stroke();
    }
    x.setTransform(1, 0, 0, 1, 0, 0);
    if (p.hand != null) {                               // la main : forme sombre qui ne suit pas la feuille
      x.fillStyle = 'rgb(58,44,40)'; x.beginPath();
      x.ellipse(p.hand + 110, 330, 110, 150, 0.3, 0, 6.2832); x.fill();
      for (let f = 0; f < 4; f++) x.fillRect(p.hand - 10 + f * 8, 190 + f * 40, 120, 24);
    }
    const d = x.getImageData(0, 0, W, H).data, y = new Uint8Array(W * H);
    for (let i = 0; i < y.length; i++) {               // bruit de capteur ±4
      const n = ((seed = (seed * 16807) % 2147483647) % 9) - 4;
      y[i] = Math.max(0, Math.min(255, d[i * 4] + n));
    }
    let b = ''; for (let i = 0; i < y.length; i += 8192) b += String.fromCharCode.apply(null, y.subarray(i, i + 8192));
    out.push(btoa(b));
  }
  return [out, seed];
}"""
ease = lambda t, a, b: 0.0 if t <= a else 1.0 if t >= b else (lambda u: u*u*(3-2*u))((t-a)/(b-a))
SCENARIOS = {   # durée (s), paramètres de scène au temps t
    "decalage": (10, lambda t: dict(dx=30*ease(t,4,5), dy=0, rot=0, hand=None)),
    "rotation": (10, lambda t: dict(dx=0, dy=0, rot=5*ease(t,4,5), hand=None)),
    "main":     (10, lambda t: dict(dx=0, dy=0, rot=0, hand=None if not 4 <= t <= 8 else 380 - 200*min(1, (t-4)/1.5, (8-t)/1.5))),
    "sortie":   (13, lambda t: dict(dx=430*(ease(t,4,5) - ease(t,7,8)), dy=0, rot=0, hand=None)),
}
async def make_y4m(b, name):
    import base64, os, tempfile
    d = os.path.join(tempfile.gettempdir(), "calque_y4m"); os.makedirs(d, exist_ok=True)
    path = os.path.join(d, f"{name}_v1.y4m")
    if os.path.exists(path): return path
    dur, fn = SCENARIOS[name]
    page = await b.new_page()
    frames = [fn(i / FPS) for i in range(dur * FPS)]
    seed = 12345
    with open(path + ".part", "wb") as f:
        f.write(f"YUV4MPEG2 W{VW} H{VH} F{FPS}:1 Ip A1:1 C420jpeg\n".encode())
        uv = bytes([128]) * (VW * VH // 2)
        for i in range(0, len(frames), 15):
            ys, seed = await page.evaluate(SCENE, [VW, VH, frames[i:i+15], seed])
            for y in ys:
                f.write(b"FRAME\n"); f.write(base64.b64decode(y)); f.write(uv)
    await page.close(); os.replace(path + ".part", path)
    return path

# Échantillonne la transformation affichée et l'état du verrou à chaque frame (temps vidéo depuis la 1re frame)
SAMPLER = r"""
window.__s = []; window.__t0 = null;
const _tick = () => {
  const v = document.querySelector('#cam'); requestAnimationFrame(_tick);
  if (!v || !v.srcObject || v.readyState < 2) return;
  if (window.__t0 === null) window.__t0 = performance.now();
  // translate(-50%, -50%) translate(x, y) rotate(r) scale(sx, sy)
  const m = (document.querySelector('#art').style.transform.match(/-?[\d.]+(e-?\d+)?/g) || []).map(Number);
  window.__s.push({ t: (performance.now() - window.__t0) / 1000, x: m[2], y: m[3], r: m[4],
                    st: window.calqueAnchor ? window.calqueAnchor.state : 'none' });
};
requestAnimationFrame(_tick);
"""
async def vtime(page, t):   # attend le temps vidéo t (s)
    await page.wait_for_function(f"window.__t0 !== null && (performance.now() - window.__t0) / 1000 >= {t}", timeout=30000, polling=50)
def window_avg(s, a, b):
    w = [e for e in s if a <= e['t'] <= b]
    n = len(w) or 1
    return dict(x=sum(e['x'] for e in w)/n, y=sum(e['y'] for e in w)/n, r=sum(e['r'] for e in w)/n, n=len(w))
COVER = "(()=>{const r=cam.getBoundingClientRect(); return Math.max(r.width/cam.videoWidth, r.height/cam.videoHeight)})()"
STATE = "[calqueAnchor.state, document.querySelector('#track').hidden, document.querySelector('#track').textContent.trim()]"
TIMES = []

async def anchor_case(p, gen, name, vps):
    path = await make_y4m(gen, name)
    b = await p.chromium.launch(args=["--use-fake-device-for-media-stream","--use-fake-ui-for-media-stream",
                                      f"--use-file-for-fake-video-capture={path}"])
    for vp, label in vps:
        ctx,page,errs = await ctx_page(b, delay=0, vp=vp, extra=SAMPLER, name=f"verrou {name} {label}")
        await start(page)
        await vtime(page, 1.5)
        await page.tap("#lockBtn")
        await vtime(page, 2.0)
        st = await page.evaluate(STATE)
        results.append(ok(st == ['anchored', False, 'Ancré'], f"{name} {label} : verrouillé → « {st[2]} » affiché ({st[0]})"))
        s_cov = await page.evaluate(COVER)
        if name == "sortie":
            await vtime(page, 6.6)
            lost = await page.evaluate(STATE)
            await page.screenshot(path="tests/test_suivi_perdu.png")
            await vtime(page, 11.5)
            back = await page.evaluate(STATE)
            await page.screenshot(path="tests/test_suivi_retrouve.png")
        else:
            if name == "main":
                await vtime(page, 6.0); await page.screenshot(path="tests/test_ancre_main.png")
            await vtime(page, 8.5)
            if name == "decalage": await page.screenshot(path=f"tests/test_ancre_{label}.png")
        s = await page.evaluate("window.__s")
        stats = await page.evaluate("calqueAnchor.stats")
        base = window_avg(s, 2.5, 3.8)
        if name == "decalage":
            end = window_avg(s, 6.0, 8.3)
            dx, dy, want = end['x'] - base['x'], end['y'] - base['y'], 30 * s_cov
            results.append(ok(abs(dx - want) <= 3 and abs(dy) <= 3,
                f"{name} {label} : feuille décalée de 30 px vidéo ({want:.1f} px écran) → modèle {dx:+.1f}, {dy:+.1f} px (écart {dx-want:+.2f})"))
        elif name == "rotation":
            end = window_avg(s, 6.0, 8.3)
            dr = end['r'] - base['r']
            results.append(ok(abs(dr - 5) <= 1, f"{name} {label} : feuille tournée de 5° → modèle {dr:+.2f}°"))
            results.append(ok(abs(end['x']-base['x']) <= 3 and abs(end['y']-base['y']) <= 3,
                f"{name} {label} : centre du modèle stable pendant la rotation ({end['x']-base['x']:+.1f}, {end['y']-base['y']:+.1f} px)"))
        elif name == "main":
            w = [e for e in s if 3.8 <= e['t'] <= 8.3]
            dev = max((((e['x']-base['x'])**2 + (e['y']-base['y'])**2) ** .5 for e in w), default=99)
            lostn = sum(e['st'] != 'anchored' for e in w)
            results.append(ok(bool(w) and dev <= 3, f"{name} {label} : la main traverse → le modèle bouge de {dev:.2f} px max ({len(w)} mesures, {lostn} sans suivi)"))
        else:
            w = [e for e in s if 5.4 <= e['t'] <= 6.6]
            frz = max((abs(e['x']-w[0]['x']) + abs(e['y']-w[0]['y']) for e in w), default=99)
            results.append(ok(lost[0] == 'lost' and lost[2] == 'Suivi perdu' and frz < 0.5,
                f"{name} {label} : feuille hors cadre → « {lost[2]} », modèle figé (variation {frz:.2f} px)"))
            end = window_avg(s, 10.0, 11.4)
            dx, dy = end['x'] - base['x'], end['y'] - base['y']
            results.append(ok(back[0] == 'anchored' and back[2] == 'Ancré' and abs(dx) <= 3 and abs(dy) <= 3,
                f"{name} {label} : feuille revenue → « {back[2]} », modèle recalé ({dx:+.1f}, {dy:+.1f} px)"))
        n = max(1, stats['n'])
        TIMES.append((f"{name} {label}", stats['total'] / n, stats['max'], stats['n'], stats['skipped']))
        print(f"   ⏱  {name} {label} : {stats['total']/n:.2f} ms/frame en moyenne (max {stats['max']:.1f}, référence {stats['ref']:.1f}), {stats['n']} frames traitées, {stats['skipped']} sautées")
        if name == "decalage" and label == "telephone":   # déverrouiller : suivi coupé, gestes rendus, pas de saut
            pos = "(()=>{const r=art.getBoundingClientRect(); return [r.left+r.width/2, r.top+r.height/2]})()"
            p0 = await page.evaluate(pos)
            await page.tap("#lockBtn"); before = await page.evaluate("art.style.transform"); p1 = await page.evaluate(pos)
            await page.wait_for_timeout(500)
            un = await page.evaluate("[calqueAnchor.active, document.querySelector('#track').hidden, art.style.transform]")
            results.append(ok(un[0] is False and un[1] and un[2] == before and abs(p1[0]-p0[0]) < 1 and abs(p1[1]-p0[1]) < 1,
                              f"Déverrouiller : suivi coupé, état masqué, modèle laissé en place (saut {p1[0]-p0[0]:+.2f}, {p1[1]-p0[1]:+.2f} px)"))
            await page.mouse.move(195,420); await page.mouse.down(); await page.mouse.move(235,450,steps=5); await page.mouse.up()
            results.append(ok(await page.evaluate("art.style.transform") != before, "Déverrouiller : les gestes déplacent de nouveau le modèle"))
        await ctx.close()
    await b.close()

hidden = lambda sel: f"(()=>{{const e=document.querySelector('{sel}'); return !!e && (e.hidden || getComputedStyle(e).display=='none')}})()"

async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch(args=["--use-fake-device-for-media-stream","--use-fake-ui-for-media-stream"])
        if os.environ.get("CALQUE_VERROU"):   # mise au point du suivi : seulement les scénarios du verrou ancré
            for n in os.environ["CALQUE_VERROU"].split(","):
                await anchor_case(p, b, n, [((390,844),"telephone")])
            await b.close(); return
        ctx,page,errs = await ctx_page(b)
        t=time.time()
        try:
            async with page.expect_file_chooser(timeout=1500) as fc: await page.tap("#pick")
            chooser = await fc.value; opened=True
        except Exception: opened=False
        results.append(ok(opened, f"Sélecteur ouvert immédiatement au tap ({(time.time()-t)*1000:.0f} ms)"))
        if opened:
            g = await page.evaluate("window.__gum")
            results.append(ok(g==0, f"Caméra pas demandée avant le choix de l'image ({g} appel(s))"))
            await chooser.set_files("tests/chat.png")
            await page.wait_for_function("document.querySelector('#intro').classList.contains('gone')", timeout=10000)
            results.append(ok(True, "Image choisie, popup caméra lente (6 s) → l'appli démarre quand même"))
            vs = await page.evaluate("[cam.readyState, cam.videoWidth]")
            results.append(ok(vs[0]>=2 and vs[1]>0, f"Flux caméra actif (readyState {vs[0]}, {vs[1]}px)"))
            pz = await page.evaluate("[cam.paused, cam.controls, cam.disablePictureInPicture]")
            results.append(ok(pz==[False,False,True], f"Vidéo en lecture, sans contrôles natifs (paused={pz[0]}, controls={pz[1]}, pip off={pz[2]})"))
            nw = await page.evaluate("art.naturalWidth")
            results.append(ok(nw==800, f"Image affichée par-dessus ({nw}px)"))
            results.append(ok(await page.evaluate(hidden('#torchBtn')), "Pas de torche sur cette caméra → bouton lampe masqué"))
            # La fausse caméra de Chromium déclare focusMode manual : vraie contrainte appliquée
            await page.tap("#focusBtn"); await page.wait_for_timeout(300)
            fs = await page.evaluate("[focusBtn.getAttribute('aria-pressed'), cam.srcObject.getVideoTracks()[0].getSettings().focusMode]")
            await page.tap("#focusBtn"); await page.wait_for_timeout(300)
            results.append(ok(fs==['true','manual'], f"Netteté fixe sur la caméra Chromium (mode {fs[1]})"))
            await page.screenshot(path="tests/test_photo.png")
            bx = await page.evaluate("(()=>{const r=art.getBoundingClientRect();return [r.left+r.width/2, r.top+r.height/2, r.width]})()")
            results.append(ok(abs(bx[0]-195)<3 and abs(bx[1]-422)<3, f"Image centrée à l'écran (centre {bx[0]:.0f},{bx[1]:.0f})"))
            results.append(ok(bx[2] <= 390, f"Image entière visible ({bx[2]:.0f}px de large)"))

            await page.wait_for_timeout(800)
            await page.tap('[data-mode="lines"]'); await page.wait_for_timeout(300)
            src = await page.evaluate("art.src.slice(0,22)")
            sty = await page.evaluate("(()=>{const c=getComputedStyle(document.querySelector('[data-mode=lines]'));return [c.backgroundColor,c.color]})()")
            results.append(ok(sty[0]!=sty[1] and 'rgba(0, 0, 0, 0)' not in sty[0], f"Bouton actif lisible (fond {sty[0]}, texte {sty[1]})"))
            results.append(ok(src.startswith("data:image/png"), "Mode Contours : image de contours générée"))
            px = await page.evaluate(EDGE_PIXELS)
            results.append(ok(px['n'] > 500 and px['notBlack'] == 0 and px['pink'] == 0,
                              f"Contours en noir : {px['n']} pixels opaques, {px['notBlack']} non noirs, {px['pink']} roses (max RGB {px['max']})"))
            await page.screenshot(path="tests/test_contours.png")

            # Grille : suit la transformation de l'image
            same = "(()=>{const a=art.getBoundingClientRect(), g=document.querySelector('#grid').getBoundingClientRect(); return grid.style.transform==art.style.transform && ['left','top','width','height'].every(k=>Math.abs(a[k]-g[k])<1)})()"
            await page.tap("#gridBtn"); await page.wait_for_timeout(100)
            gon = await page.evaluate("getComputedStyle(document.querySelector('#grid')).display!='none' && gridBtn.getAttribute('aria-pressed')=='true'")
            results.append(ok(gon and await page.evaluate(same), "Grille affichée, alignée sur l'image"))

            # Appui long sur l'image : elle disparaît tant que le doigt reste posé, sans bouger
            vis = "getComputedStyle(art).visibility"
            before = await page.evaluate("art.style.transform")
            await page.mouse.move(195,422); await page.mouse.down(); await page.wait_for_timeout(700)
            await page.mouse.move(198,424)  # petit tremblement du doigt
            hid = await page.evaluate(vis); held = await page.evaluate("art.style.transform")
            ghid = await page.evaluate("getComputedStyle(document.querySelector('#grid')).visibility")
            await page.screenshot(path="tests/test_appui_long.png")
            await page.mouse.up(); shown = await page.evaluate(vis)
            results.append(ok(hid=="hidden" and shown=="visible", f"Appui long : image masquée puis réaffichée ({hid} → {shown})"))
            results.append(ok(ghid=="hidden", "Appui long : la grille disparaît aussi"))
            results.append(ok(held==before==await page.evaluate("art.style.transform"), "Appui long : aucun déplacement"))
            await page.mouse.move(195,120); await page.mouse.down(); await page.wait_for_timeout(700)
            out = await page.evaluate(vis); await page.mouse.up()
            results.append(ok(out=="visible", "Appui long hors de l'image : rien ne disparaît"))

            await page.mouse.move(195,420); await page.mouse.down(); await page.mouse.move(255,470,steps=5); await page.mouse.up()
            after = await page.evaluate("art.style.transform")
            results.append(ok(before!=after, "Glisser déplace l'image"))
            results.append(ok(await page.evaluate(same), "La grille suit l'image déplacée"))
            await page.screenshot(path="tests/test_grille.png")

            await page.tap("#lockBtn")
            before = after
            await page.mouse.move(195,420); await page.mouse.down(); await page.mouse.move(100,300,steps=5); await page.mouse.up()
            after = await page.evaluate("art.style.transform")
            results.append(ok(before==after, "Verrouillé : l'image ne bouge plus"))
            await page.mouse.move(255,472); await page.mouse.down(); await page.wait_for_timeout(700)
            hid = await page.evaluate(vis); await page.mouse.up()
            results.append(ok(hid=="hidden" and await page.evaluate(vis)=="visible", "Appui long marche aussi verrouillé"))

            await page.fill("#op","80"); await page.dispatch_event("#op","input")
            results.append(ok(await page.evaluate("art.style.opacity")=="0.8", "Curseur d'opacité"))

            try:
                async with page.expect_file_chooser(timeout=1500) as fc2: await page.tap("#swapBtn")
                await (await fc2.value).set_files("tests/chat.png"); sw=True
            except Exception: sw=False
            results.append(ok(sw, "Bouton changer d'image ouvre le sélecteur"))
            await page.wait_for_timeout(1500)
            swr = await page.evaluate("navigator.serviceWorker.controller !== null || navigator.serviceWorker.getRegistration().then(r=>!!r)")
            results.append(ok(swr, "Service worker enregistré (installable)"))
        results.append(ok(not errs, "Aucune erreur JS" + ("" if not errs else f" : {errs}")))
        await ctx.close()

        ctx,page,errs = await ctx_page(b, delay=0, extra=FAKE_CAPS)
        await start(page)
        results.append(ok(not await page.evaluate(hidden('#torchBtn')), "Caméra avec torche → bouton lampe affiché"))
        await page.screenshot(path="tests/test_options_camera.png")
        await page.tap("#torchBtn")
        a1 = await page.evaluate("[JSON.stringify(__ac.at(-1)), torchBtn.getAttribute('aria-pressed')]")
        await page.tap("#torchBtn")
        a2 = await page.evaluate("[JSON.stringify(__ac.at(-1)), torchBtn.getAttribute('aria-pressed')]")
        results.append(ok(a1==['{"advanced":[{"torch":true}]}','true'] and a2==['{"advanced":[{"torch":false}]}','false'],
                          f"Lampe : allume puis éteint ({a1[0]} / {a2[0]})"))
        results.append(ok(not await page.evaluate(hidden('#focusBtn')), "Mise au point manuelle possible → bouton netteté affiché"))
        await page.tap("#focusBtn")
        f1 = await page.evaluate("[JSON.stringify(__ac.at(-1)), focusBtn.getAttribute('aria-pressed')]")
        await page.tap("#focusBtn")
        f2 = await page.evaluate("[JSON.stringify(__ac.at(-1)), focusBtn.getAttribute('aria-pressed')]")
        results.append(ok(f1==['{"advanced":[{"focusMode":"manual","focusDistance":0.35}]}','true']
                          and f2==['{"advanced":[{"focusMode":"continuous"}]}','false'],
                          f"Netteté : fige à la distance actuelle puis relâche ({f1[0]} / {f2[0]})"))
        results.append(ok(not errs, "Caméra simulée : aucune erreur JS" + ("" if not errs else f" : {errs}")))
        await ctx.close()

        ctx,page,errs = await ctx_page(b, delay=0, extra=PHONE_LOCK)
        await start(page); await page.wait_for_timeout(300)
        await page.evaluate(LOCK); await page.wait_for_timeout(300)
        await page.evaluate(UNLOCK); await page.wait_for_timeout(1500)
        rv = await page.evaluate("[cam.paused, cam.readyState, cam.srcObject && cam.srcObject.getVideoTracks()[0].readyState, window.__wl]")
        await page.screenshot(path="tests/test_retour.png")
        results.append(ok(rv[0]==False and rv[1]>=2 and rv[2]=='live', f"Retour après verrouillage : caméra relancée (paused={rv[0]}, piste {rv[2]})"))
        results.append(ok(rv[3]==2, f"Retour après verrouillage : écran maintenu allumé de nouveau ({rv[3]} demande(s) Wake Lock)"))
        results.append(ok(not errs, "Verrouillage : aucune erreur JS" + ("" if not errs else f" : {errs}")))
        await ctx.close()

        ctx,page,errs = await ctx_page(b, delay=0, extra=NO_FOCUS)
        await start(page)
        results.append(ok(await page.evaluate(hidden('#focusBtn')), "Pas de mise au point manuelle → bouton netteté masqué"))
        await ctx.close()

        for vp,name in [((390,844),"telephone"), ((844,390),"paysage"), ((1024,1366),"tablette")]:
            await layout(b, vp, name)

        SIZES = [((390,844),"telephone"), ((844,390),"paysage"), ((1024,1366),"tablette")]
        await anchor_case(p, b, "decalage", SIZES)
        await anchor_case(p, b, "rotation", SIZES[:1])
        await anchor_case(p, b, "main", SIZES[:1])
        await anchor_case(p, b, "sortie", SIZES[:1])

        ctx,page,errs = await ctx_page(b, delay=300, deny=True)
        async with page.expect_file_chooser(timeout=1500) as fc: await page.tap("#pick")
        await (await fc.value).set_files("tests/chat.png")
        await page.wait_for_timeout(800)
        st = await page.evaluate("[document.querySelector('#intro').classList.contains('gone'), err.hidden, err.textContent]")
        results.append(ok(not st[0] and not st[1], f"Caméra refusée → message clair : « {st[2][:50]}… »"))
        await page.screenshot(path="tests/test_refus.png")
        await b.close()
    import re
    sw = open("sw.js", encoding="utf-8").read()
    files = re.findall(r"'([^']+)'", re.search(r"FILES\s*=\s*\[(.*?)\]", sw, re.S).group(1))
    missing = [f for f in files if f != './' and not os.path.exists(f)]
    fonts = [f for f in os.listdir("fonts") if f.endswith(".woff2")] if os.path.isdir("fonts") else []
    results.append(ok(fonts and not missing and all("fonts/"+f in files for f in fonts),
                      f"sw.js : FILES complet, polices incluses {fonts}, manquants {missing or 'aucun'}"))
    if TIMES:
        avg = sum(t[1]*t[3] for t in TIMES) / max(1, sum(t[3] for t in TIMES))
        print(f"\n⏱  Verrou ancré : {avg:.2f} ms par frame traitée en moyenne, max {max(t[2] for t in TIMES):.1f} ms (PC de test)")
    bad = [(n, e) for n, e in ALL_ERRS if e]
    results.append(ok(not bad, f"Zéro erreur console / pageerror sur {len(ALL_ERRS)} pages" + ("" if not bad else f" : {bad}")))
    print(f"\n{sum(results)}/{len(results)} tests OK")
try: asyncio.run(main())
finally: srv.terminate()
sys.exit(0 if results and all(results) else 1)
