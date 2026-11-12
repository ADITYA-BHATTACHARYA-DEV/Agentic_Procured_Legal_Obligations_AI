/* Landing sections: scroll reveals, parallax, spotlight, counters, calendar, and the typed product demo. */
(() => {
  const root = document.getElementById("screen-start");
  if (!root) return;
  const $ = (s, el = root) => el.querySelector(s);
  const $$ = (s, el = root) => [...el.querySelectorAll(s)];
  const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;

  /* ----- mini calendar ----- */
  const cal = $("#cal");
  if (cal) {
    const events = { 5: "", 12: "amber", 20: "", 27: "amber", 28: "" }, today = 13, offset = 2;
    cal.innerHTML = "MTWTFSS".split("").map(d => `<b>${d}</b>`).join("") +
      Array.from({ length: 35 }, (_, i) => {
        const day = i - offset + 1, ok = day >= 1 && day <= 31;
        const cls = [ok && day in events ? "has " + events[day] : "", ok && day === today ? "today" : ""].join(" ").trim();
        return `<span style="--i:${i}" class="${cls}">${ok ? day : ""}</span>`;
      }).join("");
  }

  /* ----- stagger children of [data-stagger] ----- */
  $$("[data-stagger]").forEach(group => [...group.children].forEach((c, i) => c.style.setProperty("--d", `${i * 0.12}s`)));

  /* ----- cursor spotlight on cards ----- */
  $$(".card").forEach(card => card.addEventListener("pointermove", e => {
    const r = card.getBoundingClientRect();
    card.style.setProperty("--mx", `${e.clientX - r.left}px`); card.style.setProperty("--my", `${e.clientY - r.top}px`);
  }));

  /* ----- reveal on scroll ----- */
  const reveals = $$("[data-reveal]");
  if (!reduce && "IntersectionObserver" in window) {
    root.classList.add("reveal-ready");
    const io = new IntersectionObserver(entries => entries.forEach(en => {
      if (en.isIntersecting) { en.target.classList.add("in"); io.unobserve(en.target); }
    }), { threshold: 0.14, rootMargin: "0px 0px -6% 0px" });
    reveals.forEach(el => io.observe(el));
  } else reveals.forEach(el => el.classList.add("in"));

  /* ----- renewal ring counter (60 -> 12) ----- */
  const num = $("#ring-num");
  if (num) {
    const card = num.closest(".card");
    const run = () => {
      if (reduce) { num.textContent = "12"; return; }
      const t0 = performance.now(), dur = 2400;
      const tick = now => { const p = Math.min(1, (now - t0) / dur), e = 1 - Math.pow(1 - p, 3); num.textContent = Math.round(60 - 48 * e); if (p < 1) requestAnimationFrame(tick); };
      setTimeout(() => requestAnimationFrame(tick), 350);
    };
    const o = new IntersectionObserver(es => { if (es[0].isIntersecting) { run(); o.disconnect(); } }, { threshold: 0.4 });
    o.observe(card);
  }

  /* ----- scroll progress + parallax ----- */
  const bar = $(".scroll-progress"), px = $$("[data-parallax]");
  let ticking = false;
  function onScroll() {
    if (ticking || root.hidden) return;
    ticking = true;
    requestAnimationFrame(() => {
      ticking = false;
      const max = document.documentElement.scrollHeight - innerHeight;
      bar.style.transform = `scaleX(${max > 0 ? Math.min(1, scrollY / max) : 0})`;
      if (!reduce) px.forEach(el => {
        const r = el.parentElement.getBoundingClientRect();
        el.style.transform = `translate3d(0, ${(r.top + r.height / 2 - innerHeight / 2) * parseFloat(el.dataset.parallax)}px, 0)`;
      });
    });
  }
  addEventListener("scroll", onScroll, { passive: true }); addEventListener("resize", onScroll); onScroll();

  /* ----- typed product demo ----- */
  const demo = $("#demo");
  if (demo) {
    const q = $(".d-q", demo), typing = $(".d-typing", demo), ans = $(".d-ans", demo), cite = $(".d-cite", demo), hl = $(".d-hl", demo);
    const paras = ["a1", "a2", "a3"].map(c => $("." + c, demo));
    const QUESTION = "What happens if we miss the notice date?";
    const ANSWER = ["The contract renews for another 12 months. Notice has to reach the vendor 60 days before the term ends.",
                    "Missing the window locks you in for a full year at the current fees.",
                    "Set a reminder for 61 days before the renewal date."];
    let run = 0;

    const reset = () => { q.textContent = ""; typing.hidden = true; ans.hidden = true; cite.hidden = true; hl.classList.remove("on"); paras.forEach(p => (p.textContent = "")); };
    const finalState = () => { q.textContent = QUESTION; ans.hidden = false; cite.hidden = false; hl.classList.add("on"); paras.forEach((p, i) => (p.textContent = ANSWER[i])); };
    const wait = (ms, id) => new Promise(r => setTimeout(() => r(id === run), ms));

    async function loop() {
      const id = ++run;
      while (id === run) {
        reset();
        if (!(await wait(700, id))) return;
        for (const ch of QUESTION) { q.textContent += ch; if (!(await wait(34, id))) return; }
        if (!(await wait(450, id))) return;
        typing.hidden = false;
        if (!(await wait(1100, id))) return;
        typing.hidden = true; ans.hidden = false;
        for (let i = 0; i < 3; i++) {
          for (const word of ANSWER[i].split(" ")) { paras[i].textContent += (paras[i].textContent ? " " : "") + word; if (!(await wait(55, id))) return; }
          if (i === 0) { cite.hidden = false; hl.classList.add("on"); }
          if (!(await wait(300, id))) return;
        }
        if (!(await wait(6500, id))) return;
      }
    }
    if (reduce) finalState();
    else new IntersectionObserver(es => { es[0].isIntersecting ? (run === 0 || !demo.dataset.on) && (demo.dataset.on = "1", loop()) : (run++, delete demo.dataset.on); }, { threshold: 0.4 }).observe(demo);
  }

  /* ----- call-to-action buttons ----- */
  $("#cta-upload")?.addEventListener("click", () => { scrollTo({ top: 0, behavior: "smooth" }); document.getElementById("file-input").click(); });
  $("#cta-dashboard")?.addEventListener("click", () => openDashboard());
})();