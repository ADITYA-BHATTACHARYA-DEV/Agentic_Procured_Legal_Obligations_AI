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

const api = {
  listChats: (archived = false) => request(`/chats?archived=${archived}`).then(r => r.json()),
  getChat: id => request(`/chats/${id}`).then(r => r.json()),
  patchChat: (id, body) => request(`/chats/${id}`, json("PATCH", body)),
  deleteChat: id => request(`/chats/${id}`, { method: "DELETE" }),
  upload: file => { const f = new FormData(); f.append("file", file); return request("/documents/upload", { method: "POST", body: f }).then(r => r.json()); },
  getDoc: id => request(`/documents/${id}`).then(r => r.json()),
  fileUrl: id => `${API}/documents/${id}/file`,
  obligations: id => request(`/documents/${id}/obligations`).then(r => r.json()),
  setTask: (id, status) => request(`/tasks/${id}`, json("PATCH", { status })),
  ask: (chatId, question) => request(`/chats/${chatId}/ask`, json("POST", { question })),
};
