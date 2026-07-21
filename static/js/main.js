// main.js — APK Signer frontend logic
(function () {
  "use strict";

  let currentBuildId = null;
  let currentFilename = null;
  let evtSource = null;

  // ----------------------------------------------------------------- //
  // Toasts
  // ----------------------------------------------------------------- //
  function showToast(message, type = "info") {
    const iconMap = { success: "fa-circle-check text-success", error: "fa-circle-xmark text-danger", info: "fa-circle-info text-info" };
    const icon = iconMap[type] || iconMap.info;
    const el = document.createElement("div");
    el.className = "toast align-items-center border-0";
    el.setAttribute("role", "alert");
    el.innerHTML = `
      <div class="d-flex">
        <div class="toast-body"><i class="fa-solid ${icon} me-2"></i>${escapeHtml(message)}</div>
        <button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast"></button>
      </div>`;
    document.getElementById("toast-container").appendChild(el);
    const toast = new bootstrap.Toast(el, { delay: 5000 });
    toast.show();
    el.addEventListener("hidden.bs.toast", () => el.remove());
  }

  function escapeHtml(str) {
    const div = document.createElement("div");
    div.textContent = str;
    return div.innerHTML;
  }

  // ----------------------------------------------------------------- //
  // Drag & drop upload
  // ----------------------------------------------------------------- //
  const dropzone = document.getElementById("dropzone");
  const fileInput = document.getElementById("apkFileInput");

  if (dropzone) {
    dropzone.addEventListener("click", () => fileInput.click());

    ["dragenter", "dragover"].forEach((evt) =>
      dropzone.addEventListener(evt, (e) => {
        e.preventDefault();
        dropzone.classList.add("dragover");
      })
    );
    ["dragleave", "drop"].forEach((evt) =>
      dropzone.addEventListener(evt, (e) => {
        e.preventDefault();
        dropzone.classList.remove("dragover");
      })
    );
    dropzone.addEventListener("drop", (e) => {
      const files = e.dataTransfer.files;
      if (files.length) uploadFile(files[0]);
    });
    fileInput.addEventListener("change", (e) => {
      if (e.target.files.length) uploadFile(e.target.files[0]);
    });
  }

  function uploadFile(file) {
    if (!file.name.toLowerCase().endsWith(".apk")) {
      showToast("Only .apk files are allowed", "error");
      return;
    }

    document.getElementById("uploadResult").classList.add("d-none");
    document.getElementById("downloadArea").classList.add("d-none");
    document.getElementById("verifyResult").classList.add("d-none");
    document.getElementById("btnSignDefault").disabled = true;
    resetLog();

    const wrap = document.getElementById("uploadProgressWrap");
    const bar = document.getElementById("uploadProgressBar");
    const pct = document.getElementById("uploadPercent");
    const nameLbl = document.getElementById("uploadFileName");
    wrap.classList.remove("d-none");
    nameLbl.textContent = file.name;
    bar.style.width = "0%";
    pct.textContent = "0%";

    const formData = new FormData();
    formData.append("apk_file", file);

    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/upload");
    xhr.upload.addEventListener("progress", (e) => {
      if (e.lengthComputable) {
        const percent = Math.round((e.loaded / e.total) * 100);
        bar.style.width = percent + "%";
        pct.textContent = percent + "%";
      }
    });
    xhr.onload = () => {
      let data;
      try { data = JSON.parse(xhr.responseText); } catch { data = {}; }
      if (xhr.status >= 200 && xhr.status < 300) {
        currentBuildId = data.build_id;
        currentFilename = data.filename;
        document.getElementById("resFilename").textContent = data.filename;
        document.getElementById("resSize").textContent = data.size_human;
        document.getElementById("resSha256").textContent = data.sha256;
        document.getElementById("uploadResult").classList.remove("d-none");
        document.getElementById("btnSignDefault").disabled = false;
        showToast("APK uploaded successfully", "success");
      } else {
        showToast(data.error || "Upload failed", "error");
      }
    };
    xhr.onerror = () => showToast("Network error during upload", "error");
    xhr.send(formData);
  }

  // ----------------------------------------------------------------- //
  // Default (debug keystore) signing
  // ----------------------------------------------------------------- //
  const btnSignDefault = document.getElementById("btnSignDefault");
  if (btnSignDefault) {
    btnSignDefault.addEventListener("click", async () => {
      if (!currentBuildId) return;
      btnSignDefault.disabled = true;
      resetLog();
      document.getElementById("verifyResult").classList.add("d-none");
      document.getElementById("downloadArea").classList.add("d-none");

      try {
        const res = await fetch(`/api/sign/default/${currentBuildId}`, { method: "POST" });
        const data = await res.json();
        if (!res.ok) throw new Error(data.error || "Failed to start signing");
        showToast("Signing started (debug keystore)", "info");
        streamLogs(currentBuildId);
      } catch (err) {
        showToast(err.message, "error");
        btnSignDefault.disabled = false;
      }
    });
  }

  // ----------------------------------------------------------------- //
  // Log streaming (SSE) + polling fallback
  // ----------------------------------------------------------------- //
  function resetLog() {
    const log = document.getElementById("buildLog");
    if (log) log.textContent = "Awaiting job...";
  }

  function appendLog(line) {
    const log = document.getElementById("buildLog");
    if (!log) return;
    if (log.textContent === "Awaiting job...") log.textContent = "";
    log.textContent += line + "\n";
    log.scrollTop = log.scrollHeight;
  }

  function streamLogs(buildId) {
    if (evtSource) evtSource.close();
    document.getElementById("buildLog").textContent = "";
    evtSource = new EventSource(`/api/logs/stream/${buildId}`);

    evtSource.onmessage = (e) => appendLog(e.data);
    evtSource.addEventListener("done", (e) => {
      evtSource.close();
      fetchBuildResult(buildId, e.data);
    });
    evtSource.onerror = () => {
      // SSE dropped (e.g. proxy without streaming support) — fall back to polling
      evtSource.close();
      pollBuildStatus(buildId);
    };
  }

  function pollBuildStatus(buildId) {
    const interval = setInterval(async () => {
      const res = await fetch(`/api/build/${buildId}`);
      const data = await res.json();
      if (data.status === "success" || data.status === "failed") {
        clearInterval(interval);
        fetchBuildResult(buildId, data.status);
      }
    }, 1500);
  }

  async function fetchBuildResult(buildId, finalStatus) {
    const res = await fetch(`/api/build/${buildId}`);
    const data = await res.json();
    btnSignDefaultReenable();

    if (finalStatus === "success" || data.status === "success") {
      showToast("APK signed successfully!", "success");
      renderVerify(data.verify);
      renderDownloads(buildId, data.sha256_signed);
    } else {
      showToast("Signing failed — see build log / error", "error");
      if (data.error_message) appendLog("\n[ERROR] " + data.error_message);
    }
  }

  function btnSignDefaultReenable() {
    const btn = document.getElementById("btnSignDefault");
    if (btn) btn.disabled = false;
    const btnCustom = document.getElementById("btnSignCustom");
    if (btnCustom) { btnCustom.disabled = false; btnCustom.innerHTML = '<i class="fa-solid fa-signature me-2"></i>Sign APK'; }
  }

  function renderVerify(verify) {
    const el = document.getElementById("verifyResult");
    if (!verify) { el.classList.add("d-none"); return; }

    const schemesHtml = Object.entries(verify.schemes || {})
      .map(([k, v]) => `<span class="scheme-pill ${v ? "scheme-true" : "scheme-false"}">${k.toUpperCase()} ${v ? "✓" : "✗"}</span>`)
      .join(" ");

    el.innerHTML = `
      <h6 class="cyber-heading small mb-2"><i class="fa-solid fa-check-double me-2"></i>Signature Verification</h6>
      <div class="verify-row"><span class="text-muted">Verified</span><span>${verify.verified ? '<span class="text-success">YES</span>' : '<span class="text-danger">NO</span>'}</span></div>
      <div class="verify-row"><span class="text-muted">Schemes</span><span>${schemesHtml || "—"}</span></div>
      <div class="verify-row"><span class="text-muted">Owner</span><span class="mono small break-all">${escapeHtml(verify.owner || "—")}</span></div>
      <div class="verify-row"><span class="text-muted">Issuer</span><span class="mono small break-all">${escapeHtml(verify.issuer || "—")}</span></div>
      <div class="verify-row"><span class="text-muted">SHA-1</span><span class="mono small break-all">${escapeHtml(verify.sha1 || "—")}</span></div>
      <div class="verify-row"><span class="text-muted">SHA-256</span><span class="mono small break-all">${escapeHtml(verify.sha256 || "—")}</span></div>
      <div class="verify-row"><span class="text-muted">Valid From</span><span>${escapeHtml(verify.valid_from || "—")}</span></div>
      <div class="verify-row"><span class="text-muted">Valid Until</span><span>${escapeHtml(verify.valid_until || "—")}</span></div>
    `;
    el.classList.remove("d-none");
  }

  function renderDownloads(buildId, sha256Signed) {
    const el = document.getElementById("downloadArea");
    el.innerHTML = `
      <a href="/download/apk/${buildId}" class="btn btn-cyber"><i class="fa-solid fa-download me-2"></i>Download Signed APK</a>
      <a href="/download/log/${buildId}" class="btn btn-cyber-outline"><i class="fa-solid fa-file-lines me-2"></i>Download Build Log</a>
    `;
    el.classList.remove("d-none");
    if (sha256Signed) showToast("Signed SHA-256: " + sha256Signed.slice(0, 16) + "…", "info");
  }

  // ----------------------------------------------------------------- //
  // Keystore generation form
  // ----------------------------------------------------------------- //
  const keystoreForm = document.getElementById("keystoreForm");
  if (keystoreForm) {
    keystoreForm.addEventListener("submit", async (e) => {
      e.preventDefault();
      const submitBtn = keystoreForm.querySelector("button[type=submit]");
      submitBtn.disabled = true;
      submitBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-2"></span>Generating...';

      try {
        const res = await fetch("/api/keystore/generate", { method: "POST", body: new FormData(keystoreForm) });
        const data = await res.json();
        if (!res.ok) throw new Error(data.error || "Keystore generation failed");

        const resultEl = document.getElementById("keystoreResult");
        resultEl.innerHTML = `
          <h6 class="cyber-heading small mb-2"><i class="fa-solid fa-circle-check text-success me-2"></i>Keystore Generated</h6>
          <div class="verify-row"><span class="text-muted">Filename</span><span class="mono">${escapeHtml(data.filename)}</span></div>
          <div class="verify-row"><span class="text-muted">Alias</span><span class="mono">${escapeHtml(data.alias)}</span></div>
          <a href="${data.download_url}" class="btn btn-cyber mt-3"><i class="fa-solid fa-download me-2"></i>Download Keystore</a>
        `;
        resultEl.classList.remove("d-none");
        showToast("Keystore generated successfully", "success");

        // Add to the custom-sign dropdown live
        const sel = document.getElementById("ksSourceSelect");
        if (sel) {
          const opt = document.createElement("option");
          opt.value = data.keystore_id;
          opt.textContent = `${data.filename} (${data.alias})`;
          sel.appendChild(opt);
        }
      } catch (err) {
        showToast(err.message, "error");
      } finally {
        submitBtn.disabled = false;
        submitBtn.innerHTML = '<i class="fa-solid fa-hammer me-2"></i>Generate Keystore';
      }
    });
  }

  // ----------------------------------------------------------------- //
  // Custom keystore signing form
  // ----------------------------------------------------------------- //
  const ksSourceSelect = document.getElementById("ksSourceSelect");
  const ksUploadWrap = document.getElementById("ksUploadWrap");
  if (ksSourceSelect) {
    ksSourceSelect.addEventListener("change", () => {
      ksUploadWrap.style.display = ksSourceSelect.value ? "none" : "block";
    });
  }

  const customSignForm = document.getElementById("customSignForm");
  if (customSignForm) {
    customSignForm.addEventListener("submit", async (e) => {
      e.preventDefault();
      if (!currentBuildId) {
        showToast("Upload an APK in the 'Upload & Sign' tab first", "error");
        return;
      }
      const btn = document.getElementById("btnSignCustom");
      btn.disabled = true;
      btn.innerHTML = '<span class="spinner-border spinner-border-sm me-2"></span>Signing...';

      // switch to upload tab so the user can watch the log stream
      const tabTrigger = document.querySelector('[data-bs-target="#tab-upload"]');
      bootstrap.Tab.getOrCreateInstance(tabTrigger).show();
      resetLog();
      document.getElementById("verifyResult").classList.add("d-none");
      document.getElementById("downloadArea").classList.add("d-none");

      try {
        const res = await fetch(`/api/sign/custom/${currentBuildId}`, {
          method: "POST",
          body: new FormData(customSignForm),
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.error || "Failed to start custom signing");
        showToast("Signing started (custom keystore)", "info");
        streamLogs(currentBuildId);
      } catch (err) {
        showToast(err.message, "error");
        btn.disabled = false;
        btn.innerHTML = '<i class="fa-solid fa-signature me-2"></i>Sign APK';
      }
    });
  }
})();
