// Verrou ancré : suit la feuille dans la vidéo depuis le verrouillage.
// Image de travail en niveaux de gris (~320 px de large). Au verrouillage : points d'intérêt de la
// frame de référence. À chaque frame, les patchs de référence sont recherchés dans l'image courante
// à travers la dernière estimation (Lucas-Kanade pyramidal) : le recalage sur la référence se fait
// donc à chaque frame, sans dérive. RANSAC estime une similitude (translation, rotation, échelle)
// en écartant ce qui bouge seul (la main). Feuille perdue : estimation figée, puis recherche globale
// par descripteurs ORB jusqu'à la retrouver. jsfeat (MIT) fournit pyramides, coins et ORB.
(() => {
  const TW = 320;            // largeur de l'image de travail
  const LEVELS = 3, WIN = 11, ITER = 10;
  const MAX_PTS = 120;       // points suivis
  const BUDGET = 10;         // ms par frame traitée ; au-delà on saute des frames
  const TOL = 1.5;           // px (image de travail) : seuil d'inlier pour le suivi
  const RES_MAX = 20;        // écart moyen (niveaux de gris) au-delà duquel un patch est rejeté
  const TOL_FIND = 3;        // px : seuil d'inlier pour la recherche globale
  const BORDER = 16;         // marge pour les patchs ORB (32 px)

  // ---------- similitude q = [a -b; b a] p + (tx, ty) ----------
  const ID = () => ({ a: 1, b: 0, tx: 0, ty: 0 });
  const compose = (m, n) => ({     // m ∘ n
    a: m.a * n.a - m.b * n.b, b: m.b * n.a + m.a * n.b,
    tx: m.a * n.tx - m.b * n.ty + m.tx, ty: m.b * n.tx + m.a * n.ty + m.ty
  });
  // moindres carrés sur les paires (p[i] -> q[i]) d'indices idx
  function fit(p, q, idx, n) {
    let px = 0, py = 0, qx = 0, qy = 0;
    for (let k = 0; k < n; k++) { const i = idx[k] << 1; px += p[i]; py += p[i+1]; qx += q[i]; qy += q[i+1]; }
    px /= n; py /= n; qx /= n; qy /= n;
    let d = 0, sa = 0, sb = 0;
    for (let k = 0; k < n; k++) {
      const i = idx[k] << 1, ux = p[i] - px, uy = p[i+1] - py, vx = q[i] - qx, vy = q[i+1] - qy;
      d += ux * ux + uy * uy; sa += ux * vx + uy * vy; sb += ux * vy - uy * vx;
    }
    if (d < 1e-6) return null;
    const a = sa / d, b = sb / d;
    return { a, b, tx: qx - (a * px - b * py), ty: qy - (b * px + a * py) };
  }
  function inliers(m, p, q, ok, n, tol, out) {
    let c = 0; const t2 = tol * tol;
    for (let i = 0; i < n; i++) {
      if (!ok[i]) continue;
      const x = p[2*i], y = p[2*i+1];
      const dx = m.a * x - m.b * y + m.tx - q[2*i], dy = m.b * x + m.a * y + m.ty - q[2*i+1];
      if (dx * dx + dy * dy < t2) out[c++] = i;
    }
    return c;
  }
  const sane = m => { const s = Math.hypot(m.a, m.b); return s > 0.6 && s < 1.7; };
  // RANSAC à 2 points puis raffinement sur les inliers
  function ransac(p, q, ok, n, tol, best) {
    const cand = [];
    for (let i = 0; i < n; i++) if (ok[i]) cand.push(i);
    if (cand.length < 2) return { m: null, count: 0 };
    const tmp = new Int32Array(n), pair = new Int32Array(2);
    let bm = null, bc = 0;
    for (let it = 0; it < 80; it++) {
      pair[0] = cand[(Math.random() * cand.length) | 0];
      pair[1] = cand[(Math.random() * cand.length) | 0];
      if (pair[0] === pair[1]) continue;
      const m = fit(p, q, pair, 2);
      if (!m || !sane(m)) continue;
      const c = inliers(m, p, q, ok, n, tol, tmp);
      if (c > bc) { bc = c; bm = m; best.set(tmp.subarray(0, c)); if (c > cand.length * 0.9) break; }
    }
    if (!bm) return { m: null, count: 0 };
    for (let r = 0; r < 2; r++) {               // raffinement
      const m = fit(p, q, best, bc);
      if (!m || !sane(m)) break;
      const c = inliers(m, p, q, ok, n, tol, tmp);
      if (c < bc) break;
      bm = m; bc = c; best.set(tmp.subarray(0, c));
    }
    return { m: bm, count: bc };
  }

  // ---------- popcount pour les distances de Hamming ----------
  const POP = new Uint8Array(256);
  for (let i = 1; i < 256; i++) POP[i] = POP[i >> 1] + (i & 1);

  window.Anchor = function (video, onUpdate) {
    const jf = window.jsfeat;
    let tw = 0, th = 0, vw = 0, vh = 0;
    let canvas = null, ctx = null, gray, blur, refPyr, curPyr;
    let refXY, curXY, disp, status, nRef = 0, refDesc = null, best, tpl = [];
    const corners = [];
    let curDesc;
    let M = ID(), state = 'off', running = false, handle = 0, needRef = false;
    let skip = 0, cost = 0, frame = 0;
    const stats = { n: 0, total: 0, max: 0, skipped: 0, inliers: 0, points: 0, ref: 0 };

    function alloc() {
      vw = video.videoWidth; vh = video.videoHeight;
      tw = Math.min(TW, vw); th = Math.round(vh * tw / vw);
      canvas = document.createElement('canvas'); canvas.width = tw; canvas.height = th;
      ctx = canvas.getContext('2d', { willReadFrequently: true });
      gray = new jf.matrix_t(tw, th, jf.U8C1_t);
      blur = new jf.matrix_t(tw, th, jf.U8C1_t);
      refPyr = new jf.pyramid_t(LEVELS); refPyr.allocate(tw, th, jf.U8C1_t);
      curPyr = new jf.pyramid_t(LEVELS); curPyr.allocate(tw, th, jf.U8C1_t);
      refXY = new Float32Array(MAX_PTS * 2); curXY = new Float32Array(MAX_PTS * 2); disp = new Float32Array(MAX_PTS * 2);
      status = new Uint8Array(MAX_PTS); best = new Int32Array(Math.max(MAX_PTS, 400));
      // tampon de coins pour yape06 (qui peut en écrire jusqu'à un par pixel), gardé d'un verrouillage à l'autre
      while (corners.length < tw * th) corners.push(new jf.keypoint_t(0, 0, 0, 0, 0));
      curDesc = new jf.matrix_t(32, 400, jf.U8C1_t);
    }

    function grab() {
      if (video.videoWidth !== vw || video.videoHeight !== vh) return false;
      ctx.drawImage(video, 0, 0, tw, th);
      jf.imgproc.grayscale(ctx.getImageData(0, 0, tw, th).data, tw, th, gray);
      return true;
    }

    // coins répartis : les meilleurs par case d'une grille 8×8
    function detect(img, max) {
      jf.yape06.laplacian_threshold = 18; jf.yape06.min_eigen_value_threshold = 14;
      const n = jf.yape06.detect(img, corners, BORDER);
      const G = 8, cells = new Map(), per = Math.max(1, Math.ceil(max / (G * G)) + 1);
      for (let i = 0; i < n; i++) {
        const c = corners[i], k = ((c.y * G / th) | 0) * G + ((c.x * G / tw) | 0);
        let a = cells.get(k); if (!a) cells.set(k, a = []);
        a.push(c);
      }
      let out = [];
      cells.forEach(a => { a.sort((u, v) => v.score - u.score); out.push(...a.slice(0, per)); });
      out.sort((u, v) => v.score - u.score);
      return out.slice(0, max).map(c => ({ x: c.x, y: c.y, score: c.score, angle: 0 }));
    }

    function describe(img, pts, desc) {
      jf.orb.describe(img, pts, pts.length, desc);
      return new Uint8Array(desc.data.subarray(0, pts.length * 32));
    }

    // version lissée, pour la détection de coins et les descripteurs seulement
    const smooth = () => jf.imgproc.gaussian_blur(gray, blur, 5, 0);

    function captureRef() {
      if (!grab()) return false;
      smooth();
      const pts = detect(blur, MAX_PTS);
      nRef = pts.length;
      pts.forEach((c, i) => { refXY[2*i] = c.x; refXY[2*i+1] = c.y; });
      const d = new jf.matrix_t(32, Math.max(1, nRef), jf.U8C1_t);
      refDesc = nRef ? describe(blur, pts, d) : null;
      refPyr.build(gray, false);
      templates();
      M = ID();
      needRef = nRef < 20;           // surface sans relief : on réessaiera
      return !needRef;
    }

    // ---------- Lucas-Kanade pyramidal, inverse compositionnel ----------
    // Les patchs de référence, leurs gradients et leur hessienne sont calculés une fois au verrouillage.
    // À chaque frame, chaque patch est relu dans l'image courante à travers M (rotation et échelle
    // comprises) : pas d'image redressée à fabriquer ni de dérivées à recalculer.
    const HW = WIN >> 1, WA = WIN * WIN, SW = WIN + 2;
    const samp = new Float32Array(SW * SW), cur = new Float32Array(WA);
    // échantillon bilinéaire, coordonnées supposées dans l'image
    const bil = (d, w, x, y) => {
      const ix = x | 0, iy = y | 0, a = x - ix, b = y - iy, o = iy * w + ix;
      const p0 = d[o] + a * (d[o + 1] - d[o]), p1 = d[o + w] + a * (d[o + w + 1] - d[o + w]);
      return p0 + b * (p1 - p0);
    };
    // coordonnées : niveau l <-> niveau 0 (pyrdown moyenne des blocs 2×2)
    const up = (u, sc) => sc * (u + 0.5) - 0.5, down = (x, sc) => (x + 0.5) / sc - 0.5;

    function templates() {
      tpl = [];
      for (let l = 0; l < LEVELS; l++) {
        const im = refPyr.data[l], w = im.cols, h = im.rows, d = im.data, sc = 1 << l;
        const L = { T: new Float32Array(nRef * WA), GX: new Float32Array(nRef * WA), GY: new Float32Array(nRef * WA),
                    H: new Float32Array(nRef * 3), ok: new Uint8Array(nRef) };
        for (let i = 0; i < nRef; i++) {
          const cx = down(refXY[2*i], sc) - HW - 1, cy = down(refXY[2*i+1], sc) - HW - 1;
          if (cx < 0 || cy < 0 || cx + SW >= w - 1 || cy + SW >= h - 1) continue;
          for (let y = 0; y < SW; y++) for (let x = 0; x < SW; x++) samp[y * SW + x] = bil(d, w, cx + x, cy + y);
          let m = 0, a11 = 0, a12 = 0, a22 = 0;
          for (let y = 0, k = i * WA; y < WIN; y++) for (let x = 0; x < WIN; x++, k++) {
            const o = (y + 1) * SW + x + 1;
            const gx = (samp[o + 1] - samp[o - 1]) * 0.5, gy = (samp[o + SW] - samp[o - SW]) * 0.5;
            L.T[k] = samp[o]; L.GX[k] = gx; L.GY[k] = gy; m += samp[o];
            a11 += gx * gx; a12 += gx * gy; a22 += gy * gy;
          }
          m /= WA;
          for (let k = i * WA; k < (i + 1) * WA; k++) L.T[k] -= m;     // patch centré : insensible à l'exposition
          const det = a11 * a22 - a12 * a12;
          const eig = (a11 + a22 - Math.sqrt((a11 - a22) ** 2 + 4 * a12 * a12)) / (2 * WA);
          if (eig < 6 || det < 1e-6) continue;                           // patch sans texture : inutilisable
          L.H[3*i] = a22 / det; L.H[3*i+1] = -a12 / det; L.H[3*i+2] = a11 / det;
          L.ok[i] = 1;
        }
        tpl.push(L);
      }
    }

    function lk() {
      const A = M.a, B = M.b;
      disp.fill(0);
      for (let i = 0; i < nRef; i++) status[i] = tpl[0].ok[i];
      for (let l = LEVELS - 1; l >= 0; l--) {
        const im = curPyr.data[l], w = im.cols, h = im.rows, d = im.data, sc = 1 << l, L = tpl[l];
        for (let i = 0; i < nRef; i++) {
          if (!status[i] || !L.ok[i]) continue;
          const base = i * WA, h0 = L.H[3*i], h1 = L.H[3*i+1], h2 = L.H[3*i+2];
          let dx = disp[2*i] / sc, dy = disp[2*i+1] / sc, err = 0, inside = true;
          for (let it = 0; it < ITER; it++) {
            // coin haut-gauche du patch : niveau l de référence -> niveau 0 -> M -> niveau l courant
            const ux = up(down(refXY[2*i], sc) + dx - HW, sc), uy = up(down(refXY[2*i+1], sc) + dy - HW, sc);
            const x0 = down(A * ux - B * uy + M.tx, sc), y0 = down(B * ux + A * uy + M.ty, sc);
            // pas d'un pixel du patch : colonnes de la partie linéaire de M
            const xe = x0 + (A - B) * (WIN - 1), ye = y0 + (B + A) * (WIN - 1);
            const xa = x0 + A * (WIN - 1), ya = y0 + B * (WIN - 1), xb = x0 - B * (WIN - 1), yb = y0 + A * (WIN - 1);
            if (Math.min(x0, xe, xa, xb) < 0 || Math.min(y0, ye, ya, yb) < 0 ||
                Math.max(x0, xe, xa, xb) >= w - 1 || Math.max(y0, ye, ya, yb) >= h - 1) { inside = false; break; }
            let m = 0;
            for (let y = 0, k = 0; y < WIN; y++) {
              let x = x0 - B * y, yy = y0 + A * y;
              for (let xi = 0; xi < WIN; xi++, k++, x += A, yy += B) { const v = bil(d, w, x, yy); cur[k] = v; m += v; }
            }
            m /= WA;
            let b1 = 0, b2 = 0; err = 0;
            for (let k = 0; k < WA; k++) {
              const e = cur[k] - m - L.T[base + k];
              b1 += L.GX[base + k] * e; b2 += L.GY[base + k] * e; err += e < 0 ? -e : e;
            }
            const ddx = h0 * b1 + h1 * b2, ddy = h1 * b1 + h2 * b2;
            dx -= ddx; dy -= ddy;
            if (ddx * ddx + ddy * ddy < 1e-3) break;   // < 0,03 px
          }
          if (!inside) { if (l === 0) status[i] = 0; continue; }
          disp[2*i] = dx * sc; disp[2*i+1] = dy * sc;
          if (l === 0 && err / WA > RES_MAX) status[i] = 0;   // patch qui ne ressemble plus (main, reflet)
        }
      }
      for (let i = 0; i < 2 * nRef; i++) curXY[i] = refXY[i] + disp[i];
    }

    // suivi : points de référence retrouvés dans l'image courante à travers M, résidu estimé par RANSAC
    function track() {
      curPyr.build(gray, false);
      lk();
      const r = ransac(refXY, curXY, status, nRef, TOL, best);
      stats.inliers = r.count; stats.points = nRef;
      if (!r.m || r.count < Math.max(12, nRef * 0.25)) return false;
      const m = compose(M, r.m);
      if (!sane(m)) return false;
      M = m;
      return true;
    }

    // recherche globale : correspondances ORB référence -> image courante
    function find() {
      if (!refDesc) return false;
      smooth();
      const pts = detect(blur, 400);
      if (pts.length < 12) return false;
      const cd = describe(blur, pts, curDesc);
      const n = pts.length, p = new Float32Array(n * 2), q = new Float32Array(n * 2), ok = new Uint8Array(n);
      for (let i = 0; i < n; i++) {
        let b1 = 1e9, b2 = 1e9, bj = -1;
        for (let j = 0; j < nRef; j++) {
          let h = 0;
          for (let k = 0, a = i * 32, b = j * 32; k < 32; k++) h += POP[cd[a + k] ^ refDesc[b + k]];
          if (h < b1) { b2 = b1; b1 = h; bj = j; } else if (h < b2) b2 = h;
        }
        if (bj >= 0 && b1 < 64 && b1 < 0.8 * b2) {
          ok[i] = 1; p[2*i] = refXY[2*bj]; p[2*i+1] = refXY[2*bj+1]; q[2*i] = pts[i].x; q[2*i+1] = pts[i].y;
        }
      }
      const r = ransac(p, q, ok, n, TOL_FIND, best);
      if (!r.m || r.count < 15) return false;
      M = r.m;
      return true;
    }

    function step() {
      if (video.videoWidth !== vw || video.videoHeight !== vh) {   // la caméra a changé de format
        api.onRebase && api.onRebase();
        alloc(); captureRef(); setState(needRef ? 'lost' : 'anchored'); return;
      }
      if (!grab()) return;
      frame++;
      let ok = track();
      if (!ok && state === 'lost' && frame % 3 === 0) ok = find() && track();
      setState(ok ? 'anchored' : 'lost');
    }

    function setState(s) {
      const changed = s !== state; state = s;
      onUpdate && onUpdate(state, changed);
    }

    function tick() {
      if (!running) return;
      schedule();
      if (video.readyState < 2 || !video.videoWidth) return;
      if (skip > 0) { skip--; stats.skipped++; return; }
      const t0 = performance.now();
      if (!canvas || needRef) {                    // capture de la référence : hors moyenne
        if (!canvas) alloc();
        captureRef(); setState(needRef ? 'lost' : 'anchored');
        stats.ref = performance.now() - t0; return;
      }
      step();
      const dt = performance.now() - t0;
      stats.n++; stats.total += dt; stats.max = Math.max(stats.max, dt);
      cost = cost ? cost * 0.8 + dt * 0.2 : dt;
      skip = Math.max(0, Math.ceil(cost / BUDGET) - 1);   // trop lent : on saute des frames
    }
    const rvfc = 'requestVideoFrameCallback' in HTMLVideoElement.prototype;
    function schedule() { handle = rvfc ? video.requestVideoFrameCallback(tick) : requestAnimationFrame(tick); }
    function cancel() { if (rvfc) video.cancelVideoFrameCallback(handle); else cancelAnimationFrame(handle); }

    const api = {
      onRebase: null,
      get active() { return running; },
      get state() { return state; },
      stats,
      start() {
        if (running) return;
        if (!window.jsfeat) return;
        running = true; canvas = null; M = ID(); skip = 0; cost = 0;
        Object.assign(stats, { n: 0, total: 0, max: 0, skipped: 0 });
        schedule();
      },
      stop() { running = false; cancel(); state = 'off'; M = ID(); },
      restart() { if (running) { needRef = true; M = ID(); } },
      // similitude écran (q -> [a -b; b a] q + (e, f)) correspondant à M, vidéo en object-fit: cover
      screen() {
        if (!running || !vw) return null;
        const r = video.getBoundingClientRect();
        const s = Math.max(r.width / vw, r.height / vh);           // échelle de cover
        const ox = r.left + (r.width - vw * s) / 2, oy = r.top + (r.height - vh * s) / 2;
        const f = s * vw / tw;                                     // px écran par px de travail
        return { a: M.a, b: M.b,
                 e: ox + f * M.tx - (M.a * ox - M.b * oy), f: oy + f * M.ty - (M.b * ox + M.a * oy) };
      }
    };
    return api;
  };
})();
