/* Landing page: a "mess to order" flow field behind the hero + seamless marquee loop.
   Tangled threads on the left straighten into evenly ruled lines on the right. */
(() => {
  const screen = document.getElementById("screen-start");
  const canvas = document.getElementById("flow-bg");
  if (!screen || !canvas) return;
  const ctx = canvas.getContext("2d");
  const reduceMotion = matchMedia("(prefers-reduced-motion: reduce)");

  /* ----- marquee: clone each track once so translateX(-50%) loops with no jump ----- */
  document.querySelectorAll(".marquee-track").forEach(track => {
    [...track.children].forEach(el => { const c = el.cloneNode(true); c.setAttribute("aria-hidden", "true"); track.appendChild(c); });
  });

  /* ----- flow field ----- */
  const LINES = 18;
  const rnd = i => { const x = Math.sin(i * 127.1 + 311.7) * 43758.5453; return x - Math.floor(x); };  // stable pseudo-random per line
  const lines = Array.from({ length: LINES }, (_, i) => ({
    u: i / (LINES - 1), chaos: rnd(i) - 0.5, ph: rnd(i + 40) * 6.283, k1: 0.0045 + rnd(i + 9) * 0.004,
    k2: 0.009 + rnd(i + 21) * 0.006, s1: 0.5 + rnd(i + 3) * 0.7, s2: 0.4 + rnd(i + 14) * 0.6, amber: i % 6 === 2,
  }));
  const smooth = (a, b, x) => { const t = Math.min(1, Math.max(0, (x - a) / (b - a))); return t * t * (3 - 2 * t); };
  let w = 0, h = 0, rgb = { a: [63, 183, 162], b: [227, 163, 88] };

  const toRgb = hex => { const m = hex.trim().replace("#", ""); const n = parseInt(m.length === 3 ? m.replace(/./g, "$&$&") : m, 16); return [n >> 16 & 255, n >> 8 & 255, n & 255]; };
  function readColors() {
    const css = getComputedStyle(document.documentElement);
    try { rgb = { a: toRgb(css.getPropertyValue("--accent")), b: toRgb(css.getPropertyValue("--amber")) }; } catch (_) {}
  }
  function resize() {
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    w = canvas.clientWidth; h = canvas.clientHeight;
    canvas.width = w * dpr; canvas.height = h * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    draw(performance.now() / 1000);
  }

  function draw(t) {
    ctx.clearRect(0, 0, w, h);
    ctx.lineCap = "round"; ctx.lineJoin = "round";
    const ga = ctx.createLinearGradient(0, 0, w, 0), gb = ctx.createLinearGradient(0, 0, w, 0);
    [[ga, rgb.a], [gb, rgb.b]].forEach(([g, c]) => {
      g.addColorStop(0, `rgba(${c},0.10)`); g.addColorStop(0.45, `rgba(${c},0.22)`); g.addColorStop(1, `rgba(${c},0.38)`);
    });
    const step = Math.max(6, Math.round(w / 220));
    for (const L of lines) {
      const orderedY = h * (0.16 + 0.68 * L.u), chaosY = h * (0.46 + L.chaos * 0.34);
      ctx.beginPath();
      for (let x = -step; x <= w + step; x += step) {
        const s = x / w, order = smooth(0.12, 0.8, s), calm = 1 - order;
        const base = chaosY + (orderedY - chaosY) * order;
        const amp = Math.pow(calm, 1.5) * h * 0.2 + 2.2;      // wild when tangled, a gentle breath when ordered
        const wave = amp * (0.6 * Math.sin(x * L.k1 + t * L.s1 + L.ph) + 0.4 * Math.sin(x * L.k2 - t * L.s2 + L.ph * 2));
        x === -step ? ctx.moveTo(x, base + wave) : ctx.lineTo(x, base + wave);
      }
      ctx.strokeStyle = L.amber ? gb : ga;
      ctx.lineWidth = L.amber ? 1.6 : 1.1;
      ctx.stroke();
    }
  }

  let raf = 0, last = 0;
  function frame(now) {
    raf = requestAnimationFrame(frame);
    if (screen.hidden || document.hidden || now - last < 33) return;   // pause off-screen, cap at ~30 fps
    last = now; draw(now / 1000);
  }
  function start() { cancelAnimationFrame(raf); readColors(); if (reduceMotion.matches) draw(0); else raf = requestAnimationFrame(frame); }

  readColors(); resize(); start();
  addEventListener("resize", resize);
  reduceMotion.addEventListener("change", start);
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => { readColors(); start(); });
})();