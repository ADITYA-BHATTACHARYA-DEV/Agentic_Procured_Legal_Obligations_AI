const $ = (s, el = document) => el.querySelector(s);
const state = { chatId: null, docId: null, busy: false };

const SUGGESTIONS = [
  "Summarize this contract",
  "What are my upcoming deadlines?",
  "Is the termination clause risky?",
  "Who are the parties?",
];

/* ---------- helpers ---------- */
const md = text => DOMPurify.sanitize(marked.parse(text || ""));
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const show = (el, on = true) => (el.hidden = !on);
const TYPE_LABEL = { legal: "Legal analysis", obligation: "Obligations", summary: "Summary", general: "Quick answer" };

function go(screen) {
  show($("#screen-start"), screen === "start");
  show($("#screen-chat"), screen === "chat");
  show($("#screen-dash"), screen === "dash");
}

/* ---------- screen 1 : upload + history ---------- */
async function renderStartHistory() {
  const chats = await api.listChats().catch(() => []);
  const list = $("#start-history");
  list.innerHTML = chats.map(c => `
    <li><button class="history-item" data-id="${c.id}">
      <span class="hi-title">${esc(c.title)}</span>
      <span class="muted">${esc(c.doc_type || "Document")} · ${new Date(c.updated_at + "Z").toLocaleDateString()}</span>
    </button></li>`).join("");
  show($("#start-history-empty"), chats.length === 0);
  list.querySelectorAll(".history-item").forEach(b => b.onclick = () => openChat(b.dataset.id));
}

async function handleFile(file) {
  if (!file) return;
  const err = $("#start-error");
  show(err, false);
  show($("#start-input"), false);
  const prog = $("#progress");
  show(prog, true);
  const steps = [...prog.children];
  steps.forEach(s => s.className = "");
  let i = 0;
  steps[0].className = "active";
  const timer = setInterval(() => {            // optimistic step indicator while the backend works
    if (i < steps.length - 1) { steps[i].className = "done"; steps[++i].className = "active"; }
  }, 7000);
  try {
    const res = await api.upload(file);
    await openChat(res.chat_id);
  } catch (e) {
    err.textContent = `Upload failed: ${e.message}. Check that the backend and Ollama are running.`;
    show(err, true);
    show($("#start-input"), true);
  } finally {
    clearInterval(timer);
    show(prog, false);
    $("#file-input").value = "";
  }
}

const dz = $("#dropzone");
$("#file-input").onchange = e => handleFile(e.target.files[0]);
["dragover", "dragenter"].forEach(t => dz.addEventListener(t, e => { e.preventDefault(); dz.classList.add("drag"); }));
["dragleave", "drop"].forEach(t => dz.addEventListener(t, e => { e.preventDefault(); dz.classList.remove("drag"); }));
dz.addEventListener("drop", e => handleFile(e.dataTransfer.files[0]));
dz.addEventListener("keydown", e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); $("#file-input").click(); } });

/* ---------- sidebar ---------- */
async function renderSidebar() {
  const [active, archived] = await Promise.all([api.listChats(false), api.listChats(true)]);
  fillList($("#chat-list"), active, false);
  fillList($("#archived-list"), archived, true);
  $("#toggle-archived").textContent = $("#archived-list").hidden ? `Show archived (${archived.length})` : "Hide archived";
}

function fillList(ul, chats, isArchived) {
  ul.innerHTML = chats.map(c => `
    <li class="chat-item ${c.id === state.chatId ? "current" : ""}" data-id="${c.id}">
      <button class="chat-open" data-id="${c.id}" title="${esc(c.title)}">${esc(c.title)}</button>
      <button class="kebab" aria-label="Chat options" aria-haspopup="true">⋯</button>
      <div class="menu" hidden>
        <button data-act="rename">Rename</button>
        <button data-act="archive">${isArchived ? "Unarchive" : "Archive"}</button>
        <button data-act="delete" class="danger">Delete</button>
      </div>
    </li>`).join("");
  if (!chats.length && !isArchived) ul.innerHTML = `<li class="muted empty">No chats yet</li>`;
}

document.addEventListener("click", async e => {
  const open = e.target.closest(".chat-open");
  if (open) return openChat(open.dataset.id);

  const kebab = e.target.closest(".kebab");
  document.querySelectorAll(".menu").forEach(m => { if (!kebab || m !== kebab.nextElementSibling) m.hidden = true; });
  if (kebab) { const m = kebab.nextElementSibling; m.hidden = !m.hidden; return; }

  const act = e.target.closest(".menu button");
  if (act) {
    const id = act.closest(".chat-item").dataset.id;
    const isArchivedItem = !!act.closest("#archived-list");
    if (act.dataset.act === "rename") {
      const cur = act.closest(".chat-item").querySelector(".chat-open").textContent;
      const name = prompt("Rename chat", cur);
      if (name && name.trim()) await api.patchChat(id, { title: name.trim() });
    } else if (act.dataset.act === "archive") {
      await api.patchChat(id, { archived: !isArchivedItem });
      if (id === state.chatId && !isArchivedItem) return resetToStart();
    } else if (act.dataset.act === "delete") {
      if (!confirm("Delete this chat? The contract itself stays in your Library.")) return;
      await api.deleteChat(id);
      if (id === state.chatId) return resetToStart();
    }
    await renderSidebar();
    if (id === state.chatId) $("#chat-title").textContent = $(`.chat-item[data-id="${id}"] .chat-open`)?.textContent || $("#chat-title").textContent;
  }
});

$("#toggle-archived").onclick = () => { $("#archived-list").hidden = !$("#archived-list").hidden; renderSidebar(); };
$("#new-chat").onclick = resetToStart;

async function resetToStart() {
  state.chatId = null;
  go("start");
  show($("#start-input"), true);
  await renderStartHistory();
}

/* ---------- profile ---------- */
const DEFAULT_AVATAR = "data:image/svg+xml;utf8," + encodeURIComponent(
  `<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 40 40'><rect width='40' height='40' fill='#CFE3DF'/><circle cx='20' cy='15' r='7' fill='#0E6B5C'/><path d='M6 40c1-9 7-13 14-13s13 4 14 13z' fill='#0E6B5C'/></svg>`);
$("#avatar-img").src = localStorage.getItem("obligo.avatar") || DEFAULT_AVATAR;
$("#avatar").onclick = () => $("#avatar-input").click();
$("#avatar-input").onchange = e => {
  const f = e.target.files[0];
  if (!f) return;
  const r = new FileReader();
  r.onload = () => { $("#avatar-img").src = r.result; localStorage.setItem("obligo.avatar", r.result); };
  r.readAsDataURL(f);
};

/* ---------- chat ---------- */
async function openChat(chatId) {
  const { chat, messages } = await api.getChat(chatId);
  const doc = await api.getDoc(chat.document_id);
  state.chatId = chatId;
  state.docId = chat.document_id;

  go("chat");
  $("#chat-title").textContent = chat.title;
  const pages = doc.page_count === 1 ? "1 page" : `${doc.page_count} pages`;
  $("#doc-meta").textContent = `${doc.doc_type || "Document"} · ${pages}`;

  const box = $("#messages");
  box.innerHTML = "";
  if (!messages.length) addIntro(doc);
  messages.forEach(m => addMessage(m.role, m.content, m.query_type, m.sources ? JSON.parse(m.sources) : []));
  renderSuggestions(messages.length === 0);
  box.scrollTop = box.scrollHeight;

  renderViewer(doc);
  doc.status === "processing" ? watchProcessing(doc) : loadObligations();
  loadNotes();
  renderSidebar();
}

let pollTimer;
function watchProcessing(doc) {
  clearTimeout(pollTimer);
  $("#tab-obligations").innerHTML = `<p class="muted pad">Finding obligations in the background. You can already ask questions.</p>`;
  const tick = async () => {
    if (state.docId !== doc.id) return;
    const d = await api.getDoc(doc.id).catch(() => null);
    if (d && d.status === "ready") {
      $("#doc-meta").textContent = `${d.doc_type || "Document"} · ${d.page_count === 1 ? "1 page" : d.page_count + " pages"}`;
      $(".msg.intro")?.remove(); addIntro(d); loadObligations(); loadNotes(); renderSidebar(); return;
    }
    pollTimer = setTimeout(tick, 4000);
  };
  pollTimer = setTimeout(tick, 4000);
}

function addIntro(doc) {
  const parties = (() => { try { return JSON.parse(doc.parties || "[]"); } catch { return []; } })();
  const el = document.createElement("div");
  el.className = "msg assistant intro";
  el.innerHTML = `<div class="bubble">${md(
    `**${esc(doc.filename)}** is read and indexed.\n\n${doc.summary ? esc(doc.summary) + "\n\n" : doc.status === "processing" ? "_Summary and obligations are still being prepared. You can already ask questions._\n\n" : ""}` +
    (parties.length ? `**Parties:** ${parties.map(esc).join(", ")}\n\n` : "") +
    `Ask a question below, or open the Obligations tab to see every task and deadline.`)}</div>`;
  $("#messages").prepend(el);
}

function addMessage(role, text, qtype, sources = []) {
  const el = document.createElement("div");
  el.className = `msg ${role}`;
  const label = role === "assistant" && qtype ? `<span class="qtype qt-${qtype}">${TYPE_LABEL[qtype] || ""}</span>` : "";
  el.innerHTML = `${label}<div class="bubble">${role === "assistant" ? md(text) : esc(text)}</div>`;
  if (sources.length) el.appendChild(sourcesEl(sources));
  $("#messages").appendChild(el);
  return el;
}

function sourcesEl(sources) {
  const pages = [...new Set(sources.map(s => s.page))].sort((a, b) => a - b);
  const d = document.createElement("div");
  d.className = "sources";
  d.innerHTML = `<span class="muted">Sources</span>` + pages.map(p => `<span class="chip">p.${p}</span>`).join("");
  return d;
}

function renderSuggestions(on) {
  const box = $("#suggestions");
  box.innerHTML = on ? SUGGESTIONS.map(s => `<button type="button" class="chip-btn">${esc(s)}</button>`).join("") : "";
  box.querySelectorAll("button").forEach(b => b.onclick = () => { $("#input").value = b.textContent; $("#composer").requestSubmit(); });
}

$("#composer").addEventListener("submit", async e => {
  e.preventDefault();
  const q = $("#input").value.trim();
  if (!q || state.busy) return;
  state.busy = true;
  $("#input").value = "";
  autoGrow();
  renderSuggestions(false);
  addMessage("user", q);

  const el = addMessage("assistant", "");
  const bubble = $(".bubble", el);
  bubble.classList.add("typing");
  const box = $("#messages");
  box.scrollTop = box.scrollHeight;

  let text = "";
  try {
    const res = await api.ask(state.chatId, q);
    const reader = res.body.getReader();
    const dec = new TextDecoder();
    let buf = "";
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let nl;
      while ((nl = buf.indexOf("\n")) >= 0) {
        const line = buf.slice(0, nl).trim();
        buf = buf.slice(nl + 1);
        if (!line) continue;
        const ev = JSON.parse(line);
        if (ev.type === "meta") {
          el.insertAdjacentHTML("afterbegin", `<span class="qtype qt-${ev.query_type}">${TYPE_LABEL[ev.query_type]}</span>`);
          if (ev.sources.length) el.appendChild(sourcesEl(ev.sources));
        } else if (ev.type === "token") {
          text += ev.text;
          bubble.classList.remove("typing");
          bubble.innerHTML = md(text);
          box.scrollTop = box.scrollHeight;
        }
      }
    }
    renderSidebar();
  } catch (err) {
    bubble.classList.remove("typing");
    bubble.innerHTML = `<span class="error">Couldn't get an answer: ${esc(err.message)}. Check that Ollama is running, then send your question again.</span>`;
  } finally {
    state.busy = false;
  }
});

const input = $("#input");
function autoGrow() { input.style.height = "auto"; input.style.height = Math.min(input.scrollHeight, 140) + "px"; }
input.addEventListener("input", autoGrow);
input.addEventListener("keydown", e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); $("#composer").requestSubmit(); } });

/* ---------- right panel ---------- */
let viewerUrl = null;
async function renderViewer(doc) {
  const v = $("#tab-document");
  v.innerHTML = `<p class="muted pad">Decrypting the document…</p>`;
  try {
    const blob = await api.fileBlob(doc.id);          // decrypted by the server, sent over the (ideally HTTPS) connection
    if (viewerUrl) URL.revokeObjectURL(viewerUrl);
    viewerUrl = URL.createObjectURL(blob);
    const isPdf = (doc.mime || "").includes("pdf") || doc.filename.toLowerCase().endsWith(".pdf");
    v.innerHTML = isPdf
      ? `<iframe src="${viewerUrl}#toolbar=0&navpanes=0" title="${esc(doc.filename)}"></iframe>`
      : `<div class="img-wrap"><img src="${viewerUrl}" alt="${esc(doc.filename)}" /></div>`;
  } catch (e) {
    v.innerHTML = `<p class="error pad">Could not open the document: ${esc(e.message)}</p>`;
  }
}

document.querySelectorAll(".tab").forEach(t => t.onclick = () => {
  document.querySelectorAll(".tab").forEach(x => x.classList.toggle("active", x === t));
  ["document", "obligations", "handwriting"].forEach(name => show($("#tab-" + name), t.dataset.tab === name));
});

async function loadObligations() {
  const box = $("#tab-obligations");
  const items = await api.obligations(state.docId).catch(() => []);
  if (!items.length) { box.innerHTML = `<p class="muted pad">No obligations were found in this document.</p>`; return; }
  const today = new Date().toISOString().slice(0, 10);
  box.innerHTML = items.map(o => {
    const dated = /^\d{4}-\d{2}-\d{2}$/.test(o.deadline || "");
    const overdue = dated && o.deadline < today && o.task_status !== "done";
    return `<article class="ob ${o.task_status === "done" ? "is-done" : ""}">
      <div class="ob-top"><span class="pri pri-${o.priority}">${esc(o.priority)}</span>
        <span class="ob-date ${overdue ? "overdue" : ""}">${esc(o.deadline || "No deadline")}${overdue ? " · overdue" : ""}</span></div>
      <p>${esc(o.description)}</p>
      <div class="ob-meta muted">${esc(o.owner)}${o.clause_ref ? " · clause " + esc(o.clause_ref) : ""}</div>
      <select data-task="${o.task_id}" aria-label="Task status">
        ${["todo", "in_progress", "done"].map(s => `<option value="${s}" ${o.task_status === s ? "selected" : ""}>${{ todo: "To do", in_progress: "In progress", done: "Done" }[s]}</option>`).join("")}
      </select></article>`;
  }).join("");
  box.querySelectorAll("select").forEach(s => s.onchange = async () => { await api.setTask(s.dataset.task, s.value); loadObligations(); });
}

/* ---------- boot ---------- */
go("start");
renderStartHistory();