/* Dashboard: tasks & deadlines, renewals, library, cross-contract chat, comparison, team & alerts.
   Shares helpers ($, esc, md, show, go, openChat, api) with app.js. */
let dashPrev = "start";
let dashTab = "tasks";
const today = () => new Date().toISOString().slice(0, 10);
const isoDate = v => /^\d{4}-\d{2}-\d{2}$/.test(v || "");
const inDays = n => (n < 0 ? `${-n} days ago` : n === 0 ? "today" : `in ${n} days`);

function flash(text, isError = false) {
  const m = $("#dash-msg");
  m.textContent = text;
  m.classList.toggle("error", isError);
  show(m, true);
  clearTimeout(flash.t);
  flash.t = setTimeout(() => show(m, false), 5000);
}

async function readNdjson(res, onEvent) {
  const reader = res.body.getReader(), dec = new TextDecoder();
  let buf = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let nl;
    while ((nl = buf.indexOf("\n")) >= 0) {
      const line = buf.slice(0, nl).trim();
      buf = buf.slice(nl + 1);
      if (line) onEvent(JSON.parse(line));
    }
  }
}

/* ---------- navigation ---------- */
function openDashboard() {
  dashPrev = $("#screen-chat").hidden ? "start" : "chat";
  go("dash");
  showDashTab(dashTab);
}

function showDashTab(name) {
  dashTab = name;
  document.querySelectorAll(".dtab").forEach(t => t.classList.toggle("active", t.dataset.dtab === name));
  document.querySelectorAll(".dpane").forEach(p => show(p, p.id === `dtab-${name}`));
  ({ tasks: loadTasksTab, library: loadLibrary, compare: loadCompareSelects, team: loadTeam }[name] || (() => {}))()
    ?.catch?.(e => flash(`Could not load: ${e.message}. Is the backend running?`, true));
}

document.querySelectorAll(".dtab").forEach(t => t.onclick = () => showDashTab(t.dataset.dtab));
$("#dash-back").onclick = () => (dashPrev === "chat" && state.chatId ? go("chat") : resetToStart());
$("#btn-dashboard").onclick = openDashboard;
$("#open-dash").onclick = openDashboard;
$("#btn-upload").onclick = () => $("#file-input").click();
$("#btn-history").onclick = () => {
  const panel = $(".start-history");
  panel.hidden = !panel.hidden;
  $("#btn-history").setAttribute("aria-expanded", String(!panel.hidden));
  if (!panel.hidden) renderStartHistory();
};

/* ---------- tasks & deadlines ---------- */
const filters = () => ({ owner: $("#f-owner").value, priority: $("#f-priority").value, status: $("#f-status").value,
                         document_id: $("#f-doc").value, q: $("#f-q").value.trim() });

function fillSelect(sel, first, options, keep) {
  const cur = keep ? sel.value : "";
  sel.innerHTML = `<option value="">${first}</option>` + options.map(o => `<option value="${esc(o.value)}">${esc(o.label)}</option>`).join("");
  sel.value = cur;
}

async function loadTasksTab() {
  const [s, ren] = await Promise.all([api.dashSummary(), api.renewals()]);
  $("#stats").innerHTML = [
    ["Overdue", s.overdue, s.overdue ? "bad" : ""], ["Due in 7 days", s.due_7, s.due_7 ? "warn" : ""],
    ["Due in 30 days", s.due_30, ""], ["Completion rate", `${s.completion_rate}%`, ""],
    ["Tasks done", `${s.done} / ${s.total}`, ""],
  ].map(([l, v, c]) => `<div class="stat ${c}"><strong>${esc(v)}</strong><span>${l}</span></div>`).join("");

  fillSelect($("#f-owner"), "Any owner", s.owners.map(o => ({ value: o, label: o })), true);
  fillSelect($("#f-doc"), "All documents", s.documents.map(d => ({ value: d.id, label: d.filename })), true);

  $("#renewals").innerHTML = ren.length ? ren.map(r => {
    const urgent = r.days_to_notice <= 30;
    return `<div class="renewal ${r.days_to_notice < 0 ? "bad" : urgent ? "warn" : ""}">
      <strong>${esc(r.filename)}</strong>
      <span>${r.auto_renews === 0 ? "Ends" : "Renews"} ${esc(r.renewal_date)} (${inDays(r.days_to_renewal)})</span>
      <span>Last day for notice: <b>${esc(r.notice_date)}</b> (${inDays(r.days_to_notice)})</span></div>`;
  }).join("") : `<p class="muted">No renewal dates found yet. They appear when a contract states its term and notice period.</p>`;
  await loadTaskTable();
}

async function loadTaskTable() {
  const rows = await api.dashTasks(filters());
  const t = today();
  $("#task-table").innerHTML = `<thead><tr><th>Due</th><th>Obligation</th><th>Owner</th><th>Contract</th><th>Priority</th><th>Status</th></tr></thead><tbody>` +
    (rows.map(r => {
      const overdue = isoDate(r.due_date) && r.due_date < t && r.status !== "done";
      return `<tr class="${r.status === "done" ? "is-done" : ""}">
        <td class="nowrap ${overdue ? "overdue" : ""}">${esc(r.due_date || r.deadline_text || "No date")}${overdue ? "<br><small>overdue</small>" : ""}</td>
        <td>${r.kind === "renewal_notice" ? '<span class="pill pill-warn">Renewal</span> ' : ""}${r.recurrence ? `<span class="pill">${esc(r.recurrence)}</span> ` : ""}${esc(r.title)}${r.clause_ref ? `<br><small class="muted">clause ${esc(r.clause_ref)}</small>` : ""}</td>
        <td>${esc(r.assignee || "")}${r.assignee_email ? `<br><small class="muted">${esc(r.assignee_email)}</small>` : ""}</td>
        <td><button class="link-btn" data-open-doc="${r.document_id}">${esc(r.filename)}</button></td>
        <td><span class="pri pri-${esc(r.priority)}">${esc(r.priority)}</span></td>
        <td><select data-task="${r.id}" aria-label="Status">${["todo", "in_progress", "done"].map(s =>
          `<option value="${s}" ${r.status === s ? "selected" : ""}>${{ todo: "To do", in_progress: "In progress", done: "Done" }[s]}</option>`).join("")}</select></td></tr>`;
    }).join("") || `<tr><td colspan="6" class="muted pad">No tasks match these filters.</td></tr>`) + "</tbody>";
  $("#task-table").querySelectorAll("select").forEach(s => s.onchange = async () => { await api.setTask(s.dataset.task, s.value); loadTasksTab(); });
  $("#task-table").querySelectorAll("[data-open-doc]").forEach(b => b.onclick = () => openDocChat(b.dataset.openDoc));
}

async function openDocChat(docId) {
  const { documents } = await api.libDocs({});
  const doc = documents.find(d => d.id === docId);
  if (doc?.chat_id) openChat(doc.chat_id);
  else flash("This contract has no chat. Delete it from the Library and upload it again to start one.", true);
}

["f-owner", "f-priority", "f-status", "f-doc"].forEach(id => $("#" + id).onchange = loadTaskTable);
$("#f-q").oninput = () => { clearTimeout(loadTaskTable.t); loadTaskTable.t = setTimeout(loadTaskTable, 250); };
$("#btn-ics").onclick = () => { window.location.href = api.icsUrl(filters()); flash("Calendar file downloading. Import it into Google Calendar, Outlook or Apple Calendar."); };
$("#btn-remind").onclick = async () => {
  try { const r = await api.runReminders(); flash(`Checked ${r.checked} dated tasks and created ${r.created} new reminder${r.created === 1 ? "" : "s"}.`); }
  catch (e) { flash(`Reminder check failed: ${e.message}`, true); }
};

/* ---------- library: tags + full-text search ---------- */
async function loadLibrary() {
  const { documents, tags } = await api.libDocs({ q: $("#lib-q").value.trim(), tag: $("#lib-tag").value });
  fillSelect($("#lib-tag"), "All tags", tags.map(t => ({ value: t, label: t })), true);
  $("#lib-table").innerHTML = `<thead><tr><th>Contract</th><th>Type</th><th>Key dates</th><th>Open tasks</th><th>Tags</th><th></th></tr></thead><tbody>` +
    (documents.map(d => `<tr>
      <td><strong>${esc(d.filename)}</strong>${d.status === "processing" ? ' <span class="pill pill-warn">analysing</span>' : ""}</td>
      <td>${esc(d.doc_type || "—")}</td>
      <td class="nowrap">${d.effective_date ? "From " + esc(d.effective_date) : ""}${d.renewal_date ? "<br>Ends " + esc(d.renewal_date) : ""}</td>
      <td>${d.open_tasks}</td>
      <td class="tags">${d.tags.map(t => `<span class="pill">${esc(t)} <button class="x" data-doc="${d.id}" data-tag="${esc(t)}" aria-label="Remove tag ${esc(t)}">×</button></span>`).join("")}
        <input class="tag-input" data-doc="${d.id}" placeholder="+ tag" aria-label="Add tag" /></td>
      <td class="nowrap">${d.chat_id ? `<button class="btn" data-chat="${d.chat_id}">Open</button> ` : ""}<button class="btn" data-del="${d.id}">Delete</button></td></tr>`).join("")
    || `<tr><td colspan="6" class="muted pad">No contracts found.</td></tr>`) + "</tbody>";
  const t = $("#lib-table");
  t.querySelectorAll("[data-chat]").forEach(b => b.onclick = () => openChat(b.dataset.chat));
  t.querySelectorAll("[data-del]").forEach(b => b.onclick = async () => {
    if (confirm("Delete this contract, its tasks and its chats? This cannot be undone.")) { await api.deleteDoc(b.dataset.del); loadLibrary(); }
  });
  t.querySelectorAll(".x").forEach(b => b.onclick = async () => { await api.removeTag(b.dataset.doc, b.dataset.tag); loadLibrary(); });
  t.querySelectorAll(".tag-input").forEach(i => i.onkeydown = async e => {
    if (e.key === "Enter" && i.value.trim()) { await api.addTag(i.dataset.doc, i.value); loadLibrary(); }
  });
}

$("#lib-q").oninput = () => { clearTimeout(loadLibrary.t); loadLibrary.t = setTimeout(loadLibrary, 250); };
$("#lib-tag").onchange = loadLibrary;
async function runTextSearch() {
  const q = $("#lib-ft").value.trim(), box = $("#ft-results");
  if (!q) return show(box, false);
  const hits = await api.searchText(q).catch(() => []);
  box.innerHTML = hits.length ? hits.map(h => `<div class="hit"><strong>${esc(h.filename)}</strong> <span class="chip">p.${h.page}</span>
      <p>${esc(h.snippet).replace(/&lt;&lt;/g, "<mark>").replace(/&gt;&gt;/g, "</mark>")}</p></div>`).join("")
    : `<p class="muted">No contract text matches “${esc(q)}”. Only contracts uploaded after this feature was added are searchable.</p>`;
  show(box, true);
}
$("#lib-ft-go").onclick = runTextSearch;
$("#lib-ft").onkeydown = e => { if (e.key === "Enter") runTextSearch(); };

/* ---------- cross-contract chat ---------- */
$("#ask-form").addEventListener("submit", async e => {
  e.preventDefault();
  const q = $("#ask-input").value.trim();
  if (!q || $("#ask-form").dataset.busy) return;
  $("#ask-form").dataset.busy = "1";
  $("#ask-input").value = "";
  const box = $("#ask-messages");
  box.insertAdjacentHTML("beforeend", `<div class="msg user"><div class="bubble">${esc(q)}</div></div>`);
  box.insertAdjacentHTML("beforeend", `<div class="msg assistant"><div class="bubble typing"></div></div>`);
  const bubble = box.lastElementChild.querySelector(".bubble");
  box.scrollTop = box.scrollHeight;
  let text = "";
  try {
    await readNdjson(await api.askAll(q), ev => {
      if (ev.type === "token") { text += ev.text; bubble.classList.remove("typing"); bubble.innerHTML = md(text); box.scrollTop = box.scrollHeight; }
      if (ev.type === "meta" && ev.sources.length)
        box.lastElementChild.insertAdjacentHTML("beforeend", `<div class="sources"><span class="muted">Sources</span>${
          [...new Set(ev.sources.map(s => `${s.file} p.${s.page}`))].map(s => `<span class="chip">${esc(s)}</span>`).join("")}</div>`);
    });
  } catch (err) {
    bubble.classList.remove("typing");
    bubble.innerHTML = `<span class="error">Couldn't get an answer: ${esc(err.message)}. Check that Ollama is running.</span>`;
  } finally { delete $("#ask-form").dataset.busy; }
});
$("#ask-input").addEventListener("keydown", e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); $("#ask-form").requestSubmit(); } });

/* ---------- comparison ---------- */
async function loadCompareSelects() {
  const { documents } = await api.libDocs({});
  const opts = documents.map(d => `<option value="${d.id}">${esc(d.filename)}</option>`).join("");
  const a = $("#cmp-a").value, b = $("#cmp-b").value;
  $("#cmp-a").innerHTML = $("#cmp-b").innerHTML = opts;
  if (a) $("#cmp-a").value = a;
  $("#cmp-b").value = b || documents[1]?.id || "";
}

$("#cmp-go").onclick = async () => {
  const a = $("#cmp-a").value, b = $("#cmp-b").value;
  if (!a || !b || a === b) return flash("Choose two different contracts to compare.", true);
  const table = $("#cmp-table"), status = $("#cmp-status");
  $("#cmp-go").disabled = true;
  status.textContent = "Comparing. Clause topics take a moment each.";
  table.innerHTML = "";
  let body;
  try {
    await readNdjson(await api.compare(a, b), ev => {
      if (ev.type === "meta") {
        table.innerHTML = `<thead><tr><th>Topic</th><th>${esc(ev.a)}</th><th>${esc(ev.b)}</th><th>Difference</th><th>Stricter</th></tr></thead><tbody></tbody>`;
        body = table.querySelector("tbody");
      } else if (ev.type === "row") {
        const diff = ev.source === "data" ? `<span class="pill ${ev.difference === "Differs" ? "pill-warn" : ""}">${esc(ev.difference)}</span>` : esc(ev.difference);
        const strict = { A: "First", B: "Second" }[ev.stricter] || (ev.stricter && ev.stricter !== "unclear" ? esc(ev.stricter) : "");
        body.insertAdjacentHTML("beforeend", `<tr class="${ev.source}"><td><strong>${esc(ev.topic)}</strong>${ev.source === "ai" ? '<br><small class="muted">AI-read clause</small>' : ""}</td><td>${esc(ev.a)}</td><td>${esc(ev.b)}</td><td>${diff}</td><td>${strict}</td></tr>`);
      }
    });
    status.textContent = "Done. Clause rows are read by the model, so check them against the contract text.";
  } catch (e) { status.textContent = ""; flash(`Comparison failed: ${e.message}`, true); }
  finally { $("#cmp-go").disabled = false; }
};

/* ---------- team & alerts ---------- */
async function loadTeam() {
  const [owners, notes] = await Promise.all([api.owners(), api.notifications()]);
  $("#owner-table").innerHTML = `<thead><tr><th>Person</th><th>Email</th><th>Party in contract</th><th></th></tr></thead><tbody>` +
    (owners.map(o => `<tr><td>${esc(o.name)}</td><td>${esc(o.email)}</td><td>${esc(o.party)}</td><td><button class="btn" data-owner="${o.id}">Remove</button></td></tr>`).join("")
      || `<tr><td colspan="4" class="muted pad">No owners yet. Tasks show the party name until you add one.</td></tr>`) + "</tbody>";
  $("#owner-table").querySelectorAll("[data-owner]").forEach(b => b.onclick = async () => { await api.deleteOwner(b.dataset.owner); loadTeam(); });
  $("#notif-list").innerHTML = notes.map(n => `<div class="notif"><span>${esc(n.message)}</span><small class="muted">${esc(n.status)} · ${esc(n.created_at)}</small></div>`).join("")
    || `<p class="muted">No reminders yet. Use “Run reminder check” on the Tasks tab to test.</p>`;
}

$("#owner-form").addEventListener("submit", async e => {
  e.preventDefault();
  try {
    const r = await api.addOwner({ name: $("#ow-name").value, email: $("#ow-email").value, party: $("#ow-party").value });
    $("#owner-form").reset();
    flash(`Owner added. ${r.updated} task${r.updated === 1 ? "" : "s"} reassigned.`);
    loadTeam();
  } catch (err) { flash(err.message, true); }
});