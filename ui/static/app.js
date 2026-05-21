/**
 * Local AI Orchestrator — UI (FASE 7)
 */
(function () {
  const $ = (id) => document.getElementById(id);

  let currentSlug = "";
  let jobRunning = false;
  let refreshTimer = null;
  let roleTargetPath = null;
  let eventSource = null;

  async function api(path, options = {}) {
    const res = await fetch(path, {
      headers: { "Content-Type": "application/json", ...options.headers },
      ...options,
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || res.statusText);
    return data;
  }

  function appendLog(entry) {
    if (entry.heartbeat) return;
    const el = $("log-stream");
    const line = document.createElement("div");
    line.className = `log-line ${entry.level || "INFO"}`;
    const ts = entry.ts ? entry.ts.slice(11, 19) : "";
    line.textContent = ts ? `[${ts}] ${entry.msg}` : entry.msg;
    el.appendChild(line);
    el.scrollTop = el.scrollHeight;
  }

  function connectSSE() {
    if (eventSource) eventSource.close();
    eventSource = new EventSource("/api/logs/stream");
    eventSource.onmessage = (ev) => {
      try {
        appendLog(JSON.parse(ev.data));
      } catch (_) {
        /* ignore */
      }
    };
    eventSource.onerror = () => {
      setTimeout(connectSSE, 3000);
    };
  }

  function setJobUi(running, stopped) {
    jobRunning = running;
    $("btn-start").disabled = running;
    $("btn-stop").disabled = !running;
    $("btn-reset").disabled = !stopped;
    const badge = $("job-badge");
    badge.textContent = running ? "Running" : stopped ? "Stopped" : "Idle";
    badge.className = "pill " + (running ? "running" : stopped ? "stopped" : "");

    if (running && !refreshTimer) {
      refreshTimer = setInterval(() => {
        if (currentSlug) loadProject(currentSlug);
        pollJobStatus();
      }, 5000);
    }
    if (!running && refreshTimer) {
      clearInterval(refreshTimer);
      refreshTimer = null;
    }
  }

  function updateStats(job) {
    if (!job) {
      $("stat-done").textContent = "0";
      $("stat-total").textContent = "0";
      $("stat-failed").textContent = "0";
      $("progress-bar").style.width = "0%";
      $("current-file").textContent = "";
      return;
    }
    const done = job.files_completed || 0;
    const total = job.files_total || 0;
    const failed = job.files_failed || 0;
    $("stat-done").textContent = String(done);
    $("stat-total").textContent = String(total);
    $("stat-failed").textContent = String(failed);
    const pct = total > 0 ? Math.round((done / total) * 100) : 0;
    $("progress-bar").style.width = `${pct}%`;
    $("current-file").textContent = job.current_file
      ? `In corso: ${job.current_file}`
      : job.status || "";
  }

  async function pollJobStatus() {
    try {
      const st = await api("/api/jobs/status");
      updateStats(st.job);
      setJobUi(st.running, st.stop_requested && !st.running);
    } catch (e) {
      console.warn(e);
    }
  }

  async function loadProjects() {
    const list = await api("/api/projects");
    const sel = $("project-select");
    const prev = sel.value;
    sel.innerHTML = '<option value="">— seleziona —</option>';
    list.forEach((p) => {
      const opt = document.createElement("option");
      opt.value = p.slug;
      opt.textContent = p.name || p.slug;
      sel.appendChild(opt);
    });
    if (prev) sel.value = prev;
  }

  async function loadWorkflows() {
    const { workflows } = await api("/api/workflows").catch(() => ({
      workflows: [{ id: "ingest", label: "Ingest" }],
    }));
    const sel = $("workflow-select");
    sel.innerHTML = "";
    (workflows || []).forEach((w) => {
      const opt = document.createElement("option");
      opt.value = w.id;
      opt.textContent = w.label || w.id;
      sel.appendChild(opt);
    });
  }

  async function loadModels() {
    try {
      const data = await api("/api/models");
      $("model-badge").textContent = data.active
        ? `Modello: ${data.active}`
        : "Modello: offline";
    } catch {
      $("model-badge").textContent = "Modello: —";
    }
  }

  function renderFiles(files) {
    const ul = $("file-list");
    ul.innerHTML = "";
    if (!files || !files.length) {
      ul.innerHTML = '<li class="caption" style="padding:12px">Nessun file — copia in 01_INGEST/</li>';
      return;
    }
    files.forEach((f) => {
      const li = document.createElement("li");
      li.className = "file-item";
      li.dataset.path = f.path;
      const role = f.role || "Raw";
      li.innerHTML = `
        <span class="file-path" title="${f.path}">${f.path}</span>
        <span class="role-badge ${role}">${role}</span>
      `;
      li.addEventListener("click", () => openRoleModal(f.path, role));
      ul.appendChild(li);
    });
  }

  async function loadProject(slug) {
    if (!slug) {
      $("project-slug-label").textContent = "";
      renderFiles([]);
      return;
    }
    const detail = await api(`/api/projects/${encodeURIComponent(slug)}`);
    $("project-slug-label").textContent = `(${slug})`;
    if (detail.workflow) $("workflow-select").value = detail.workflow;
    if (detail.hardware_profile) $("profile-select").value = detail.hardware_profile;
    renderFiles(detail.files);
  }

  function openRoleModal(path, role) {
    roleTargetPath = path;
    $("role-modal-path").textContent = path;
    $("role-modal-select").value = role;
    $("role-modal").classList.remove("hidden");
  }

  function closeRoleModal() {
    roleTargetPath = null;
    $("role-modal").classList.add("hidden");
  }

  $("project-select").addEventListener("change", async (e) => {
    currentSlug = e.target.value;
    await loadProject(currentSlug);
  });

  $("btn-new-project").addEventListener("click", async () => {
    const name = $("new-project-name").value.trim();
    if (!name) return;
    const workflow = $("workflow-select").value || "ingest";
    const meta = await api("/api/projects", {
      method: "POST",
      body: JSON.stringify({ name, workflow }),
    });
    $("new-project-name").value = "";
    await loadProjects();
    $("project-select").value = meta.slug;
    currentSlug = meta.slug;
    await loadProject(currentSlug);
    appendLog({ msg: `Progetto creato: ${meta.slug}`, level: "INFO" });
  });

  $("profile-select").addEventListener("change", async () => {
    const profile = $("profile-select").value;
    await api("/api/profiles/select", {
      method: "POST",
      body: JSON.stringify({
        profile_name: profile,
        project: currentSlug || undefined,
      }),
    });
  });

  $("btn-start").addEventListener("click", async () => {
    if (!currentSlug) {
      alert("Seleziona un progetto");
      return;
    }
    try {
      await api("/api/profiles/select", {
        method: "POST",
        body: JSON.stringify({
          profile_name: $("profile-select").value,
          project: currentSlug,
        }),
      });
      const job = await api("/api/jobs/start", {
        method: "POST",
        body: JSON.stringify({
          project: currentSlug,
          workflow: $("workflow-select").value,
        }),
      });
      updateStats(job);
      setJobUi(true, false);
      appendLog({ msg: "[UI] Job avviato", level: "INFO" });
    } catch (e) {
      alert(e.message);
    }
  });

  $("btn-stop").addEventListener("click", async () => {
    const res = await fetch("/api/jobs/stop", { method: "POST" });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || res.statusText);
    appendLog({ msg: `[STOP] ${data.message}`, level: "WARN" });
    $("btn-start").disabled = true;
    $("btn-stop").disabled = true;
    $("btn-reset").disabled = false;
    setJobUi(false, true);
    pollJobStatus();
  });

  $("btn-reset").addEventListener("click", async () => {
    await api("/api/jobs/reset", { method: "POST" });
    appendLog({ msg: "[UI] Orchestrator reset", level: "INFO" });
    setJobUi(false, false);
    updateStats(null);
    pollJobStatus();
  });

  $("role-modal-cancel").addEventListener("click", closeRoleModal);
  $("role-modal-save").addEventListener("click", async () => {
    if (!currentSlug || !roleTargetPath) return;
    await api(`/api/projects/${encodeURIComponent(currentSlug)}/roles`, {
      method: "POST",
      body: JSON.stringify({
        file_path: roleTargetPath,
        role: $("role-modal-select").value,
      }),
    });
    closeRoleModal();
    await loadProject(currentSlug);
  });

  async function init() {
    connectSSE();
    await loadWorkflows();
    await loadProjects();
    await loadModels();
    await pollJobStatus();
    appendLog({ msg: "UI pronta — apri http://localhost:7842", level: "INFO" });
  }

  init();
})();
