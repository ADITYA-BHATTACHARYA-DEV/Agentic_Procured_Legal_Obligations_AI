/* Handwriting tab: shows what the vision model read, the verification checks, Llama's analysis,
   and lets a person confirm or correct the text before any task is created from it. */
const NOTE_STATUS = { verified: ["Verified", "ok"], needs_review: ["Needs review", "warn"], confirmed: ["Confirmed", "ok"] };
const NOTE_KIND = { amendment: "Amendment", instruction: "Instruction", reminder: "Reminder", question: "Question", approval: "Approval", signature: "Signature", other: "Note" };

function noteChecks(n) {
  const agree = n.agreement;
  return [
    [agree == null ? null : agree >= 0.85, agree == null ? "Second reading was skipped, so a person must check" : `Two independent readings agree (${Math.round(agree * 100)}%)`],
    [n.script_match, `Script check: text is ${esc(n.script_detected)}, model said ${esc(n.script)}`],
    [n.legibility !== "poor", `Legibility: ${esc(n.legibility)}`],
    [!n.flagged.some(f => f.kind === "unreadable_value"), "Dates and amounts are fully readable"],
  ];
}

function flagLine(f) {
  if (f.kind === "disagreement") return `The two readings differ: “${esc(f.pass1 || "(nothing)")}” vs “${esc(f.pass2 || "(nothing)")}”`;
  if (f.kind === "uncertain") return `Model was unsure about “${esc(f.text)}”`;
  return esc(f.text);
}

function analysisBlock(a) {
  if (!a || !a.summary) return "";
  const items = (a.action_items || []).map(i => `<li>${esc(i.description)}${i.owner ? ` <span class="muted">· ${esc(i.owner)}</span>` : ""}${i.deadline ? ` <span class="muted">· ${esc(i.deadline)}</span>` : ""}</li>`).join("");
  return `<div class="n-analysis">
    <div class="n-an-head"><span class="pill">${NOTE_KIND[a.note_kind] || "Note"}</span>${a.affects_clause ? `<span class="muted">refers to clause ${esc(a.affects_clause)}</span>` : ""}</div>
    <p>${esc(a.summary)}</p>
    ${a.modifies_terms ? `<p class="n-warn">This note appears to change a contract term. Have a lawyer review it.</p>` : ""}
    ${items ? `<ul>${items}</ul>` : ""}</div>`;
}

function noteCard(n) {
  const [label, tone] = NOTE_STATUS[n.status] || ["Unknown", "warn"];
  const dir = ["ar", "he", "fa", "ur"].includes(n.language_code) ? "rtl" : "auto";
  const done = n.status === "confirmed";
  const translated = n.translation_en && !["en", ""].includes(n.language_code);
  return `<article class="note" data-id="${n.id}">
    <header><strong>Page ${n.page}</strong><span class="pill">${esc(n.language)} · ${esc(n.script)}</span><span class="n-status n-${tone}">${label}</span></header>
    <ul class="n-checks">${noteChecks(n).map(([ok, text]) => `<li><i class="chk ${ok == null ? "na" : ok ? "ok" : "bad"}"></i>${text}</li>`).join("")}</ul>
    ${n.flagged.length ? `<details class="n-flags" ${n.status === "needs_review" ? "open" : ""}><summary>${n.flagged.length} thing${n.flagged.length === 1 ? "" : "s"} to check</summary><ul>${n.flagged.map(f => `<li>${flagLine(f)}</li>`).join("")}</ul></details>` : ""}
    ${n.printed ? `<label>Printed text</label><p class="n-printed">${esc(n.printed)}</p>` : ""}
    <label for="hw-${n.id}">Handwriting, as read (${esc(n.language)})</label>
    <textarea id="hw-${n.id}" class="n-text" lang="${esc(n.language_code)}" dir="${dir}" rows="4">${esc(n.handwritten)}</textarea>
    ${translated ? `<label for="tr-${n.id}">English translation</label><textarea id="tr-${n.id}" class="n-trans" rows="3">${esc(n.translation_en)}</textarea>` : ""}
    ${analysisBlock(n.analysis)}
    <footer>
      <button class="btn btn-primary n-confirm" ${done ? "disabled" : ""}>${done ? "Confirmed" : "Confirm and create tasks"}</button>
      <span class="muted">${done ? "Tasks from this note were created from the confirmed text." : "Nothing here becomes a deadline until you confirm it."}</span>
    </footer></article>`;
}

async function loadNotes() {
  const box = $("#tab-handwriting");
  if (!box || !state.docId) return;
  const [notes, health] = await Promise.all([api.notes(state.docId).catch(() => []), api.visionHealth().catch(() => null)]);

  const pending = notes.filter(n => n.status === "needs_review").length;
  const badge = $(".tab-count");
  badge.textContent = pending; show(badge, pending > 0);

  if (!notes.length) {
    const hint = health && health.enabled && !health.available
      ? `The vision model <code>${esc(health.model)}</code> is not installed. Run <code>ollama pull ${esc(health.model)}</code> to read handwriting and other languages.`
      : "No scanned pages or handwriting were found in this document. Upload a photo or scan to use this.";
    box.innerHTML = `<p class="muted pad">${hint}</p>`;
    return;
  }
  box.innerHTML = notes.map(noteCard).join("");
  box.querySelectorAll(".n-confirm").forEach(btn => btn.onclick = async () => {
    const card = btn.closest(".note"), id = card.dataset.id;
    btn.disabled = true; btn.textContent = "Saving";
    try {
      await api.confirmNote(id, { handwritten: $(".n-text", card).value, translation_en: $(".n-trans", card)?.value });
      await loadNotes();
      setTimeout(loadObligations, 15000); setTimeout(loadObligations, 45000);   // tasks are created in the background
    } catch (e) { btn.disabled = false; btn.textContent = "Confirm and create tasks"; alert(`Could not save: ${e.message}`); }
  });
}