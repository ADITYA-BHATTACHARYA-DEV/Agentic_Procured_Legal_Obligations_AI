/* Security tab: protection status, the noise-envelope lab, and the audit log. */
const b64Blob = (b64, type = "image/png") => new Blob([Uint8Array.from(atob(b64), c => c.charCodeAt(0))], { type });

async function loadSecurity() {
  const [s, log] = await Promise.all([api.securityStatus(), api.audit()]);
  const allEnc = s.documents_encrypted === s.documents_total;
  const card = (value, label, hint, warn) => `<div class="stat ${warn ? "warn" : ""}"><strong>${esc(value)}</strong><span>${esc(label)}</span><small class="muted">${esc(hint)}</small></div>`;
  $("#sec-status").innerHTML = [
    card(s.encryption_at_rest ? `${s.documents_encrypted} of ${s.documents_total}` : "Off", "Documents encrypted", allEnc ? "AES-256-GCM, one key per document" : "Run encrypt-existing for older uploads", !s.encryption_at_rest || !allEnc),
    card(s.key_source === "environment" ? "Environment" : "Key file", "Master key", s.key_source === "environment" ? "Not stored beside the data" : "Move it to OBLIGO_MASTER_KEY on a server", s.key_source !== "environment"),
    card(s.https ? "HTTPS" : "HTTP", "Connection", s.https ? "Encrypted in transit" : "Use HTTPS on any shared network", !s.https),
    card(s.auth_enabled ? "Required" : "Not set", "Access token", s.auth_enabled ? "Every API call is checked" : "Set OBLIGO_API_TOKEN on a server", !s.auth_enabled),
    card(s.cors_open ? "Any site" : "Restricted", "Allowed origins", s.cors_open ? "Set ALLOWED_ORIGINS on a server" : "Only your frontend can call the API", s.cors_open),
  ].join("");

  $("#audit-table").innerHTML = `<thead><tr><th>Time</th><th>Action</th><th>Target</th><th>Detail</th><th>From</th></tr></thead><tbody>` +
    (log.map(a => `<tr class="${a.action.includes("denied") || a.action.includes("failed") ? "overdue-row" : ""}"><td class="nowrap">${esc(a.ts)}</td><td><span class="pill ${a.action.includes("denied") || a.action.includes("failed") ? "pill-warn" : ""}">${esc(a.action)}</span></td><td>${esc(a.target)}</td><td class="muted">${esc(a.detail)}</td><td class="muted">${esc(a.ip)}</td></tr>`).join("")
      || `<tr><td colspan="5" class="muted pad">Nothing recorded yet.</td></tr>`) + "</tbody>";
}

function metricsTable(m) {
  if (!m) return "";
  const row = (name, value, ideal, ok) => `<tr><td>${name}</td><td><strong>${value}</strong></td><td class="muted">${ideal}</td><td><i class="chk ${ok ? "ok" : "bad"}"></i></td></tr>`;
  const c = m.correlation;
  let html = `<table class="metrics"><thead><tr><th>Measure</th><th>Result</th><th>Ideal</th><th></th></tr></thead><tbody>`;
  html += row("Randomness (bits per byte)", m.entropy.static.toFixed(4), "8.0000", m.entropy.static > 7.99);
  if (m.chi_square) html += row("Histogram flatness (chi-square)", m.chi_square.static, `below ${m.chi_square.limit}`, m.chi_square.uniform);
  if (c) {
    const worst = Math.max(...Object.values(c.static).map(Math.abs));
    html += row("Neighbour-pixel correlation", `${Object.values(c.original).map(v => v.toFixed(2)).join(" / ")} → ${Object.values(c.static).map(v => v.toFixed(2)).join(" / ")}`, "near 0", worst < 0.05);
    html += row("Change spread (NPCR)", `${m.npcr.value}%`, `${m.npcr.ideal}%`, Math.abs(m.npcr.value - m.npcr.ideal) < 0.4);
    html += row("Change size (UACI)", `${m.uaci.value}%`, `${m.uaci.ideal}%`, Math.abs(m.uaci.value - m.uaci.ideal) < 1);
  }
  return html + `</tbody></table><p class="muted n-hint">Correlation reads horizontal / vertical / diagonal, original → static. NPCR and UACI flip one bit of the input and measure how much of the output changes.</p>`;
}

$("#nz-go").onclick = async () => {
  const file = $("#nz-file").files[0], pass = $("#nz-pass").value, out = $("#nz-out");
  if (!file) return flash("Choose an image or a file first.", true);
  if (pass.length < 8) return flash("Use a passphrase of at least 8 characters.", true);
  $("#nz-go").disabled = true;
  out.innerHTML = `<p class="muted">Scrambling…</p>`;
  try {
    const r = await api.noiseEncrypt(file, pass);
    const staticUrl = `data:image/png;base64,${r.static_png}`;
    const isImg = r.kind === "image";
    out.innerHTML = `<div class="tri">
        <figure>${isImg ? `<img alt="Original" src="${URL.createObjectURL(file)}" />` : `<div class="file-tile">${esc(file.name)}</div>`}<figcaption>Original</figcaption></figure>
        <figure><img class="noise" alt="Static" src="${staticUrl}" /><figcaption>What travels</figcaption></figure>
        <figure>${isImg ? `<img alt="Recovered" src="data:image/png;base64,${r.recovered_png}" />` : `<div class="file-tile">${esc(file.name)}</div>`}<figcaption>Recovered</figcaption></figure></div>
      <p class="${r.lossless ? "" : "error"}">${r.lossless ? "Recovered exactly, with no loss." : "Recovery did not match. Do not use this envelope."}</p>
      ${metricsTable(r.metrics)}
      <button id="nz-save" class="btn">Download the static image</button>
      <p class="muted n-hint">Send it as a PNG. Saving it as JPEG, resizing it or taking a screenshot changes the pixels, and recovery will then be refused.</p>`;
    $("#nz-save").onclick = () => saveBlob(b64Blob(r.static_png), `${file.name.replace(/\.[^.]+$/, "")}.static.png`);
  } catch (e) { out.innerHTML = `<p class="error">${esc(e.message)}</p>`; }
  finally { $("#nz-go").disabled = false; }
};

$("#nz-undo").onclick = async () => {
  const file = $("#nz-in-file").files[0], pass = $("#nz-in-pass").value, out = $("#nz-in-out");
  if (!file || !pass) return flash("Choose the static image and enter its passphrase.", true);
  out.innerHTML = `<p class="muted">Checking the key…</p>`;
  try {
    const { blob, kind, name } = await api.noiseDecrypt(file, pass);
    out.innerHTML = kind === "image" ? `<img class="recovered" alt="Recovered image" src="${URL.createObjectURL(blob)}" />` : `<div class="file-tile">${esc(name)}</div>`;
    out.insertAdjacentHTML("beforeend", `<p>Recovered ${kind === "image" ? "the image" : esc(name)}. <button id="nz-dl" class="link-btn">Download</button></p>`);
    $("#nz-dl").onclick = () => saveBlob(blob, name);
  } catch (e) { out.innerHTML = `<p class="error">${esc(e.message)}</p>`; }
};