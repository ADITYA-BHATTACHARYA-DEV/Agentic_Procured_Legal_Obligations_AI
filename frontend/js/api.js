// The API lives on the same host as this page, port 8000, over the same protocol (so an HTTPS page talks HTTPS).
const API = `${location.protocol === "https:" ? "https:" : "http:"}//${location.hostname || "localhost"}:8000/api`;
const tokenStore = { get: () => localStorage.getItem("obligo.token") || "", set: t => localStorage.setItem("obligo.token", t) };

async function request(path, opts = {}, retried = false) {
  const headers = new Headers(opts.headers || {});
  if (tokenStore.get()) headers.set("Authorization", `Bearer ${tokenStore.get()}`);   // never put the token in a URL
  const res = await fetch(API + path, { ...opts, headers });
  if (res.status === 401 && !retried) {
    const entered = prompt("This server needs an access token. Enter it to continue:");
    if (entered) { tokenStore.set(entered.trim()); return request(path, opts, true); }
  }
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch (_) {}
    throw new Error(detail);
  }
  return res;
}
const json = (method, body) => ({ method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
const qs = obj => new URLSearchParams(Object.entries(obj).filter(([, v]) => v !== "" && v != null)).toString();
const form = obj => { const f = new FormData(); Object.entries(obj).forEach(([k, v]) => f.append(k, v)); return f; };
function saveBlob(blob, name) {
  const a = Object.assign(document.createElement("a"), { href: URL.createObjectURL(blob), download: name });
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 4000);
}

const api = {
  // chats + documents
  listChats: (archived = false) => request(`/chats?archived=${archived}`).then(r => r.json()),
  getChat: id => request(`/chats/${id}`).then(r => r.json()),
  patchChat: (id, body) => request(`/chats/${id}`, json("PATCH", body)),
  deleteChat: id => request(`/chats/${id}`, { method: "DELETE" }),
  upload: file => request("/documents/upload", { method: "POST", body: form({ file }) }).then(r => r.json()),
  getDoc: id => request(`/documents/${id}`).then(r => r.json()),
  deleteDoc: id => request(`/documents/${id}`, { method: "DELETE" }),
  fileBlob: id => request(`/documents/${id}/file`).then(r => r.blob()),
  obligations: id => request(`/documents/${id}/obligations`).then(r => r.json()),
  setTask: (id, status) => request(`/tasks/${id}`, json("PATCH", { status })),
  ask: (chatId, question) => request(`/chats/${chatId}/ask`, json("POST", { question })),

  // handwriting
  notes: id => request(`/documents/${id}/notes`).then(r => r.json()),
  confirmNote: (id, body) => request(`/notes/${id}`, json("PATCH", body)).then(r => r.json()),
  visionHealth: () => request("/vision/health").then(r => r.json()),

  // dashboard
  dashSummary: () => request("/dashboard/summary").then(r => r.json()),
  dashTasks: filters => request(`/dashboard/tasks?${qs(filters)}`).then(r => r.json()),
  renewals: () => request("/dashboard/renewals").then(r => r.json()),
  downloadIcs: async filters => saveBlob(await request(`/calendar.ics?${qs(filters)}`).then(r => r.blob()), "obligo-deadlines.ics"),
  runReminders: () => request("/reminders/run", { method: "POST" }).then(r => r.json()),
  notifications: () => request("/notifications").then(r => r.json()),
  owners: () => request("/owners").then(r => r.json()),
  addOwner: body => request("/owners", json("POST", body)).then(r => r.json()),
  deleteOwner: id => request(`/owners/${id}`, { method: "DELETE" }).then(r => r.json()),
  applyOwners: () => request("/owners/apply", { method: "POST" }).then(r => r.json()),

  // library
  libDocs: filters => request(`/library/documents?${qs(filters)}`).then(r => r.json()),
  addTag: (id, tag) => request(`/library/documents/${id}/tags`, json("POST", { tag })),
  removeTag: (id, tag) => request(`/library/documents/${id}/tags?${qs({ tag })}`, { method: "DELETE" }),
  searchText: q => request(`/library/search?${qs({ q })}`).then(r => r.json()),
  askAll: question => request("/library/ask", json("POST", { question })),
  compare: (a, b) => request("/library/compare", json("POST", { document_a: a, document_b: b })),

  // security
  securityStatus: () => request("/security/status").then(r => r.json()),
  audit: () => request("/audit").then(r => r.json()),
  noiseEncrypt: (file, passphrase) => request("/crypto/noise/encrypt", { method: "POST", body: form({ file, passphrase }) }).then(r => r.json()),
  noiseDecrypt: async (file, passphrase) => {
    const r = await request("/crypto/noise/decrypt", { method: "POST", body: form({ file, passphrase }) });
    return { blob: await r.blob(), kind: r.headers.get("X-Obligo-Kind"), name: decodeURIComponent(r.headers.get("X-Obligo-Filename") || "recovered") };
  },
};