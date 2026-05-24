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
  let sseReconnectTimer = null;
  let sseBackoffMs = 3000;
  let sseStopped = false;

  async function api(path, options = {}) {
    const res = await fetch(path, {
      headers: { "Content-Type": "application/json", ...options.headers },
      ...options,
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || res.statusText);
    return data;
  }

  let logBuffer = [];
  let logFlushScheduled = false;

  function flushLogBuffer() {
    logFlushScheduled = false;
    if (!logBuffer.length) return;
    const el = $("log-stream");
    const frag = document.createDocumentFragment();
    for (const entry of logBuffer) {
      const line = document.createElement("div");
      line.className = `log-line ${entry.level || "INFO"}`;
      const ts = entry.ts ? entry.ts.slice(11, 19) : "";
      line.textContent = ts ? `[${ts}] ${entry.msg}` : entry.msg;
      frag.appendChild(line);
    }
    logBuffer = [];
    el.appendChild(frag);
    el.scrollTop = el.scrollHeight;
  }

  function appendLog(entry) {
    if (entry.heartbeat) return;
    logBuffer.push(entry);
    if (!logFlushScheduled) {
      logFlushScheduled = true;
      requestAnimationFrame(flushLogBuffer);
    }
  }

  function disconnectSSE(permanent = false) {
    if (permanent) sseStopped = true;
    if (sseReconnectTimer) {
      clearTimeout(sseReconnectTimer);
      sseReconnectTimer = null;
    }
    if (eventSource) {
      eventSource.close();
      eventSource = null;
    }
  }

  function connectSSE() {
    if (sseStopped) return;
    if (eventSource) {
      eventSource.close();
      eventSource = null;
    }
    eventSource = new EventSource("/api/logs/stream");
    eventSource.onopen = () => {
      sseBackoffMs = 3000;
    };
    eventSource.onmessage = (ev) => {
      try {
        appendLog(JSON.parse(ev.data));
      } catch (_) {
        /* ignore */
      }
    };
    eventSource.onerror = () => {
      if (eventSource) {
        eventSource.close();
        eventSource = null;
      }
      if (sseStopped || document.visibilityState === "hidden") {
        return;
      }
      sseReconnectTimer = setTimeout(() => {
        sseBackoffMs = Math.min(Math.round(sseBackoffMs * 1.5), 30000);
        connectSSE();
      }, sseBackoffMs);
    };
  }

  function setJobUi(status) {
    // status: "running" | "stopped" | "failed" | "idle"
    jobRunning = status === "running";

    $("btn-start").disabled = status === "running";
    $("btn-stop").disabled = status !== "running";
    $("btn-reset").disabled = status === "running" || status === "idle";

    const badge = $("job-badge");
    badge.className = "pill";

    switch (status) {
      case "running":
        badge.textContent = "Running";
        badge.classList.add("running");
        break;
      case "stopped":
        badge.textContent = "Stopped";
        badge.classList.add("stopped");
        break;
      case "failed":
        badge.textContent = "Failed";
        badge.classList.add("stopped"); // rosso
        break;
      default:
        badge.textContent = "Idle";
    }

    // Auto-refresh durante esecuzione
    if (status === "running" && !refreshTimer) {
      refreshTimer = setInterval(() => {
        if (currentSlug) loadProject(currentSlug);
        pollJobStatus();
      }, 3000);
    }
    if (status !== "running" && refreshTimer) {
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
    let pct = 0;
    if (typeof job.progress_percent === "number" && !Number.isNaN(job.progress_percent)) {
      pct = Math.max(0, Math.min(100, Math.round(job.progress_percent)));
    } else if (total > 0) {
      pct = Math.round((done / total) * 100);
    }
    $("progress-bar").style.width = `${pct}%`;
    $("current-file").textContent = job.current_file
      ? `In corso: ${job.current_file}`
      : job.status || "";
  }

  async function pollJobStatus() {
    try {
      const st = await api("/api/jobs/status");
      const job = st.job;

      updateStats(job);

      const failed = job && job.status === "failed";
      const stopped = st.stop_requested && !st.running;
      const completed = job && job.status === "completed" && !st.running;

      // FIX Task 3: sblocca UI su fail/stop/complete
      if (st.running) {
        setJobUi("running");
      } else if (failed) {
        setJobUi("failed");
        appendLog({
          msg: `[JOB] ✗ Job fallito: ${job.error || "errore sconosciuto"}`,
          level: "ERROR",
        });
      } else if (stopped) {
        setJobUi("stopped");
      } else {
        setJobUi("idle");
      }
    } catch (e) {
      console.warn("[pollJobStatus]", e);
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

  function normalizeWorkflowList(data) {
    const raw = Array.isArray(data) ? data : data?.workflows;
    if (!Array.isArray(raw)) return [];
    return raw.filter(
      (w) => w && typeof w.id === "string" && w.id.trim().length > 0,
    );
  }

  async function loadWorkflows() {
    const sel = $("workflow-select");
    try {
      const data = await api("/api/workflows");
      const list = normalizeWorkflowList(data);
      sel.innerHTML = "";
      if (!list.length) {
        sel.innerHTML = '<option value="ingest">Ingest (default)</option>';
        return;
      }
      list.forEach((w) => {
        const opt = document.createElement("option");
        opt.value = w.id;
        opt.textContent = w.label || w.id.replace(/_/g, " ");
        if (w.description) {
          opt.title = w.description;
        }
        const flags = [];
        if (w.requires_llm) flags.push("LLM");
        if (w.requires_rag) flags.push("RAG");
        if (flags.length) {
          opt.textContent += ` (${flags.join("+")})`;
        }
        sel.appendChild(opt);
      });
      const required = ["code_analysis", "devblog", "doc_refactor"];
      const ids = new Set(list.map((w) => w.id));
      const missing = required.filter((id) => !ids.has(id));
      if (missing.length) {
        console.warn("[loadWorkflows] plugin mancanti in /api/workflows:", missing);
      }
    } catch (e) {
      console.warn("[loadWorkflows] fallback:", e);
      sel.innerHTML = '<option value="ingest">Ingest</option>';
    }
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
      alert("Seleziona un progetto prima di avviare");
      return;
    }
    const workflow = $("workflow-select").value;
    if (!workflow) {
      alert("Seleziona un workflow");
      return;
    }

    // Applica profilo
    try {
      await api("/api/profiles/select", {
        method: "POST",
        body: JSON.stringify({
          profile_name: $("profile-select").value,
          project: currentSlug,
        }),
      });
    } catch (e) {
      appendLog({ msg: `[UI] Profilo: ${e.message}`, level: "WARN" });
    }

    // Avvia job
    try {
      $("btn-start").disabled = true; // feedback immediato
      const job = await api("/api/jobs/start", {
        method: "POST",
        body: JSON.stringify({ project: currentSlug, workflow }),
      });
      updateStats(job);
      setJobUi("running");
      appendLog({ msg: `[UI] ▶ Job avviato — workflow=${workflow}`, level: "INFO" });
    } catch (e) {
      $("btn-start").disabled = false;
      appendLog({ msg: `[UI] ✗ Errore avvio: ${e.message}`, level: "ERROR" });
      alert(`Impossibile avviare: ${e.message}`);
    }
  });

  $("btn-stop").addEventListener("click", async () => {
    $("btn-stop").disabled = true; // evita doppio click
    try {
      const data = await api("/api/jobs/stop", { method: "POST" });
      appendLog({ msg: `[STOP] ${data.message}`, level: "WARN" });
      setJobUi("stopped");
    } catch (e) {
      appendLog({ msg: `[STOP] Errore: ${e.message}`, level: "ERROR" });
    }
  });

  $("btn-reset").addEventListener("click", async () => {
    try {
      await api("/api/jobs/reset", { method: "POST" });
      appendLog({ msg: "[UI] Orchestrator pronto per un nuovo job", level: "INFO" });
      setJobUi("idle");
      updateStats(null);
    } catch (e) {
      appendLog({ msg: `[RESET] Errore: ${e.message}`, level: "ERROR" });
    }
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

  window.addEventListener("beforeunload", () => disconnectSSE(true));
  window.addEventListener("pagehide", () => disconnectSSE(true));
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "hidden") {
      disconnectSSE(false);
    } else if (!sseStopped) {
      connectSSE();
    }
  });

  async function init() {
    sseStopped = false;
    connectSSE();
    await loadWorkflows();
    await loadProjects();
    await loadModels();
    await pollJobStatus();
    appendLog({ msg: "UI pronta — apri http://localhost:7842", level: "INFO" });
  }

  init();
})();
