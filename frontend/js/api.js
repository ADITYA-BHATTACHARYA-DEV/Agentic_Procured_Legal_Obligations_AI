const API = "http://localhost:8000/api";

async function request(path, opts = {}) {
  const res = await fetch(API + path, opts);
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch (_) {}
    throw new Error(detail);
  }
  return res;
}
const json = (method, body) => ({ method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
const qs = obj => new URLSearchParams(Object.entries(obj).filter(([, v]) => v !== "" && v != null)).toString();

const api = {
  // chats + documents
  listChats: (archived = false) => request(`/chats?archived=${archived}`).then(r => r.json()),
  getChat: id => request(`/chats/${id}`).then(r => r.json()),
  patchChat: (id, body) => request(`/chats/${id}`, json("PATCH", body)),
  deleteChat: id => request(`/chats/${id}`, { method: "DELETE" }),
  upload: file => { const f = new FormData(); f.append("file", file); return request("/documents/upload", { method: "POST", body: f }).then(r => r.json()); },
  getDoc: id => request(`/documents/${id}`).then(r => r.json()),
  deleteDoc: id => request(`/documents/${id}`, { method: "DELETE" }),
  fileUrl: id => `${API}/documents/${id}/file`,
  obligations: id => request(`/documents/${id}/obligations`).then(r => r.json()),
  setTask: (id, status) => request(`/tasks/${id}`, json("PATCH", { status })),
  ask: (chatId, question) => request(`/chats/${chatId}/ask`, json("POST", { question })),

  // dashboard
  dashSummary: () => request("/dashboard/summary").then(r => r.json()),
  dashTasks: filters => request(`/dashboard/tasks?${qs(filters)}`).then(r => r.json()),
  renewals: () => request("/dashboard/renewals").then(r => r.json()),
  icsUrl: filters => `${API}/calendar.ics?${qs(filters)}`,
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
};