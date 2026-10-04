import asyncio, subprocess, time, sys
from playwright.async_api import async_playwright
srv = subprocess.Popen([sys.executable,"-m","http.server","8765","-d","."],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
time.sleep(1)
URL="http://localhost:8765/"
ok=lambda c,m: print(("✅ " if c else "❌ ")+m) or c
results=[]
async def ctx_page(b, delay=6000, deny=False, extra=""):
    ctx = await b.new_context(viewport={"width":390,"height":844}, has_touch=True, is_mobile=True)
    if not deny: await ctx.grant_permissions(["camera"], origin=URL.rstrip('/'))
    page = await ctx.new_page()
    errs=[]; page.on("pageerror", lambda e: errs.append(str(e)))
    page.on("console", lambda m: m.type=="error" and errs.append(m.text))
    js = ("navigator.mediaDevices.getUserMedia = () => new Promise((_,j)=>setTimeout(()=>j(new DOMException('no','NotAllowedError')),%d));" % delay) if deny else \
         ("const real=navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices); navigator.mediaDevices.getUserMedia=c=>new Promise(r=>setTimeout(()=>r(real(c)),%d));" % delay)
    js += " const _c=navigator.mediaDevices.getUserMedia; window.__gum=0; navigator.mediaDevices.getUserMedia=c=>{window.__gum++; return _c.call(navigator.mediaDevices,c)};"
    await page.add_init_script(js + extra)
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
hidden = lambda sel: f"(()=>{{const e=document.querySelector('{sel}'); return !!e && (e.hidden || getComputedStyle(e).display=='none')}})()"

async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch(args=["--use-fake-device-for-media-stream","--use-fake-ui-for-media-stream"])
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

        ctx,page,errs = await ctx_page(b, delay=300, deny=True)
        async with page.expect_file_chooser(timeout=1500) as fc: await page.tap("#pick")
        await (await fc.value).set_files("tests/chat.png")
        await page.wait_for_timeout(800)
        st = await page.evaluate("[document.querySelector('#intro').classList.contains('gone'), err.hidden, err.textContent]")
        results.append(ok(not st[0] and not st[1], f"Caméra refusée → message clair : « {st[2][:50]}… »"))
        await page.screenshot(path="tests/test_refus.png")
        await b.close()
    print(f"\n{sum(results)}/{len(results)} tests OK")
asyncio.run(main()); srv.terminate()
