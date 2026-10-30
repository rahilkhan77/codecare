function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char]));
}

function readCookie(name) {
  const prefix = `${name}=`;
  const hit = document.cookie.split(";").map((part) => part.trim()).find((part) => part.startsWith(prefix));
  return hit ? decodeURIComponent(hit.slice(prefix.length)) : "";
}

async function ensureCsrf() {
  let token = readCookie("codecare_csrf");
  if (token) return token;
  const response = await fetch("/api/v1/auth/csrf", { credentials: "include" });
  const data = await response.json().catch(() => ({}));
  return data.csrf_token || readCookie("codecare_csrf");
}

async function api(path, options = {}) {
  const method = (options.method || "GET").toUpperCase();
  const headers = { ...(options.headers || {}) };
  if (options.body && !headers["Content-Type"]) headers["Content-Type"] = "application/json";
  if (!["GET", "HEAD", "OPTIONS"].includes(method)) headers["X-CSRF-Token"] = await ensureCsrf();
  const response = await fetch(path, { credentials: "include", ...options, headers });
  if (response.status === 401 && document.body.dataset.page === "app") {
    window.location.href = "/workspace/login.html";
  }
  return response;
}

function errorDetail(data, fallback) {
  if (!data || data.detail == null) return fallback;
  if (typeof data.detail === "string") return data.detail;
  if (Array.isArray(data.detail)) return data.detail.map((item) => item.msg || "Check the form and try again.").join(" ");
  return fallback;
}

function showStatus(el, message, isError = true) {
  if (!el) return;
  el.textContent = message;
  el.classList.toggle("is-error", isError);
  el.classList.toggle("is-ok", !isError && Boolean(message));
}

function safeNext() {
  const next = new URLSearchParams(window.location.search).get("next") || "";
  if (next.startsWith("/workspace/") && !next.startsWith("//")) return next;
  return "/workspace/";
}

async function submitAuth(form, path, onSuccess) {
  const status = document.querySelector("#status");
  const button = form.querySelector("button[type=submit]");
  button.disabled = true;
  button.setAttribute("aria-busy", "true");
  showStatus(status, "Working…", false);
  try {
    const body = Object.fromEntries(new FormData(form).entries());
    const response = await api(path, { method: "POST", body: JSON.stringify(body) });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      showStatus(status, errorDetail(data, "Request failed."));
      return;
    }
    onSuccess(data);
  } catch {
    showStatus(status, "The API is not reachable. Start the CodeCare backend and try again.");
  } finally {
    button.disabled = false;
    button.removeAttribute("aria-busy");
  }
}

const loginForm = document.querySelector("#login-form");
if (loginForm) {
  api("/api/v1/auth/me").then((response) => {
    if (response.ok) window.location.href = safeNext();
  });
  loginForm.addEventListener("submit", (event) => {
    event.preventDefault();
    submitAuth(loginForm, "/api/v1/auth/login", () => {
      window.location.href = safeNext();
    });
  });
}

const registerForm = document.querySelector("#register-form");
if (registerForm) {
  api("/api/v1/auth/me").then((response) => {
    if (response.ok) window.location.href = safeNext();
  });
  registerForm.addEventListener("submit", (event) => {
    event.preventDefault();
    submitAuth(registerForm, "/api/v1/auth/register", (data) => {
      if (data.email_delivery && data.email_delivery !== "smtp") {
        sessionStorage.setItem("codecare-email-note", data.message || "");
      }
      window.location.href = safeNext();
    });
  });
}

const forgotForm = document.querySelector("#forgot-form");
if (forgotForm) {
  forgotForm.addEventListener("submit", (event) => {
    event.preventDefault();
    submitAuth(forgotForm, "/api/v1/auth/forgot-password", (data) => {
      showStatus(document.querySelector("#status"), data.message || "Check your email.", data.delivery === "failed" || data.delivery === "unavailable");
    });
  });
}

const resetForm = document.querySelector("#reset-form");
if (resetForm) {
  const token = new URLSearchParams(window.location.search).get("token") || "";
  const tokenInput = resetForm.querySelector("[name=token]");
  if (tokenInput) tokenInput.value = token;
  if (!token) showStatus(document.querySelector("#status"), "This reset link is missing a token.");
  resetForm.addEventListener("submit", (event) => {
    event.preventDefault();
    submitAuth(resetForm, "/api/v1/auth/reset-password", () => {
      showStatus(document.querySelector("#status"), "Password updated. You can sign in.", false);
      window.setTimeout(() => {
        window.location.href = "/workspace/login.html";
      }, 700);
    });
  });
}

if (document.body.dataset.page === "verify") {
  const status = document.querySelector("#status");
  const token = new URLSearchParams(window.location.search).get("token") || "";
  if (!token) showStatus(status, "This verification link is missing a token.");
  else {
    api("/api/v1/auth/verify-email", { method: "POST", body: JSON.stringify({ token }) })
      .then(async (response) => {
        const data = await response.json().catch(() => ({}));
        showStatus(status, response.ok ? "Email verified. You can continue to the workspace." : errorDetail(data, "Verification failed."), !response.ok);
      })
      .catch(() => showStatus(status, "The API is not reachable."));
  }
}

if (document.body.dataset.page === "invite") {
  const status = document.querySelector("#status");
  const token = new URLSearchParams(window.location.search).get("token") || "";
  const next = `${window.location.pathname}${window.location.search}`;
  api("/api/v1/auth/me").then(async (me) => {
    if (!token) {
      showStatus(status, "This invitation link is missing a token.");
      return;
    }
    if (me.status === 401) {
      showStatus(status, "Sign in with the invited email, then open this link again.", false);
      document.querySelector("#invite-actions").hidden = false;
      document.querySelector("#invite-login").href = `/workspace/login.html?next=${encodeURIComponent(next)}`;
      document.querySelector("#invite-register").href = `/workspace/register.html?next=${encodeURIComponent(next)}`;
      return;
    }
    const response = await api("/api/v1/auth/accept-invite", { method: "POST", body: JSON.stringify({ token }) });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      showStatus(status, errorDetail(data, "Invitation could not be accepted."));
      return;
    }
    if (data.organization_id) {
      await api("/api/v1/auth/switch-organization", { method: "POST", body: JSON.stringify({ organization_id: data.organization_id }) });
    }
    showStatus(status, "Invitation accepted.", false);
    window.setTimeout(() => {
      window.location.href = "/workspace/";
    }, 600);
  });
}

document.querySelectorAll("[data-github-signin]").forEach((button) => {
  fetch("/api/v1/auth/providers")
    .then((response) => response.json())
    .then((data) => {
      if (!data.github) {
        button.disabled = true;
        button.textContent = "GitHub sign-in is not configured";
      }
    })
    .catch(() => {
      button.disabled = true;
    });
  button.addEventListener("click", async () => {
    const status = document.querySelector("#status");
    button.disabled = true;
    try {
      const response = await api("/api/v1/auth/github/start");
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        showStatus(status, errorDetail(data, "GitHub sign-in is unavailable."));
        button.disabled = false;
        return;
      }
      window.location.href = data.url;
    } catch {
      showStatus(status, "The API is not reachable.");
      button.disabled = false;
    }
  });
});

const viewRoot = document.querySelector("#view-root");
if (viewRoot) {
  const state = { me: null, view: "overview", detail: null };

  async function waitForJob(jobId) {
    if (!jobId) return null;
    for (let i = 0; i < 40; i += 1) {
      const response = await api(`/api/v1/jobs/${jobId}`);
      const job = await response.json();
      if (!response.ok) return job;
      if (["completed", "failed"].includes(job.status)) return job;
      await new Promise((resolve) => setTimeout(resolve, 1500));
    }
    return { status: "queued", detail: "Still running. Refresh this view shortly." };
  }

  async function requireSession() {
    const response = await api("/api/v1/auth/me");
    if (response.status === 401) {
      window.location.href = "/workspace/login.html";
      return null;
    }
    return response.json();
  }

  function canWrite() {
    return ["owner", "admin"].includes(state.me?.role);
  }

  function banner(text, error = false) {
    return `<div class="banner${error ? " error" : ""}">${esc(text)}</div>`;
  }

  function showNotice(message, isError = true) {
    const html = `<div class="banner${isError ? " error" : ""}" id="action-status" role="status">${esc(message)}</div>`;
    const existing = document.querySelector("#action-status");
    if (existing) existing.outerHTML = html;
    else viewRoot.insertAdjacentHTML("afterbegin", html);
  }

  function setView(name) {
    state.view = name;
    document.querySelectorAll(".side-item").forEach((item) => {
      item.classList.toggle("selected", item.dataset.view === name);
    });
    render();
  }

  document.querySelectorAll(".side-item").forEach((item) => {
    item.addEventListener("click", () => setView(item.dataset.view));
  });
  document.querySelector("#logout-btn")?.addEventListener("click", async () => {
    await api("/api/v1/auth/logout", { method: "POST" });
    window.location.href = "/workspace/login.html";
  });
  document.querySelector("#avatar")?.addEventListener("click", () => setView("account"));

  async function render() {
    viewRoot.setAttribute("aria-busy", "true");
    viewRoot.innerHTML = `<p class="empty-state">Loading…</p>`;
    try {
      if (state.view === "overview") await renderOverview();
      else if (state.view === "repos") await renderRepos();
      else if (state.view === "repo") await renderRepo();
      else if (state.view === "issues") await renderIssues();
      else if (state.view === "prs") await renderPRs();
      else if (state.view === "integrations") await renderIntegrations();
      else if (state.view === "billing") await renderBilling();
      else if (state.view === "account") await renderAccount();
      else if (state.view === "issue") await renderIssue();
      else if (state.view === "fix") await renderFix();
      else viewRoot.innerHTML = banner("That view is not available.", true);
      if (state.me && !state.me.email_verified) {
        const note = sessionStorage.getItem("codecare-email-note");
        viewRoot.insertAdjacentHTML(
          "afterbegin",
          `<div class="banner" id="verify-banner" role="status">Verify ${esc(state.me.email)} to finish setting up this account. ${note ? esc(note) : ""} <button class="text-link" id="resend-verification" type="button">Resend verification email</button></div>`,
        );
        document.querySelector("#resend-verification")?.addEventListener("click", async (event) => {
          event.currentTarget.disabled = true;
          const response = await api("/api/v1/auth/resend-verification", { method: "POST" });
          const data = await response.json().catch(() => ({}));
          showNotice(data.message || errorDetail(data, "Could not resend the verification email."), !response.ok || data.delivery === "unavailable" || data.delivery === "failed");
        });
      }
    } catch (error) {
      viewRoot.innerHTML = banner(error.message || "Unable to load this view.", true);
    } finally {
      viewRoot.removeAttribute("aria-busy");
    }
  }

  async function renderOverview() {
    const data = await (await api("/api/v1/workspace/overview")).json();
    document.querySelector("#org-name").textContent = state.me?.organization?.name || "Workspace";
    document.querySelector("#plan-label").textContent = `${data.plan} plan`;
    document.querySelector("#avatar").textContent = (data.greeting_name || "C").slice(0, 2).toUpperCase();
    document.querySelector("#sync-pill").innerHTML = `<i></i> ${data.health === "healthy" ? "All systems operational" : data.repo_count ? "Needs attention" : "No repositories yet"}`;
    const repos = data.repositories.length
      ? data.repositories.map((repo) => `
          <div class="repo-row" data-repo="${repo.id}">
            <span class="repo-icon">⬡</span>
            <span class="repo-name"><b>${esc(repo.full_name)}</b><small>${esc(repo.language || "language unknown")} · ${repo.last_scanned_at ? `scanned ${esc(repo.last_scanned_at)}` : "not scanned"}</small></span>
            <span class="repo-health ${repo.health === "attention" ? "attention" : ""}"><i></i> ${repo.health}</span>
          </div>`).join("")
      : `<p class="empty-state">No repositories connected yet. Use Integrations to install the GitHub App.</p>`;
    const activity = data.activity.length
      ? data.activity.map((item) => `<article class="activity-item"><span class="activity-symbol symbol-check">⌕</span><div class="activity-copy"><b>${item.title}</b><p>${item.body || ""}</p></div></article>`).join("")
      : `<p class="empty-state">No maintenance activity yet.</p>`;
    viewRoot.innerHTML = `
      <div class="dash-heading"><div><p class="dash-kicker">WORKSPACE</p><h2>Good to see you, ${esc(data.greeting_name)}</h2><p class="dash-subtitle">Live data from your connected repositories. Nothing here is illustrative.</p></div></div>
      <div class="metric-grid">
        <article class="metric metric-health"><div class="metric-label">APPLICATION HEALTH</div><div class="health-value"><span class="health-indicator"></span>${data.health}</div><div class="health-foot">Across ${data.repo_count} connected repositories</div></article>
        <article class="metric"><div class="metric-label">ISSUES RESOLVED</div><div class="metric-value">${String(data.issues_resolved_7d).padStart(2, "0")}</div><div class="metric-caption">Last 7 days</div></article>
        <article class="metric"><div class="metric-label">NEEDS YOUR REVIEW</div><div class="metric-value">${String(data.prs_need_review).padStart(2, "0")}</div><div class="metric-caption">Open pull requests</div></article>
      </div>
      <div class="dash-columns" style="margin-top:12px">
        <section class="panel">${repos}</section>
        <section class="panel">${activity}</section>
      </div>`;
    viewRoot.querySelectorAll("[data-repo]").forEach((row) => {
      row.addEventListener("click", () => {
        state.detail = row.dataset.repo;
        state.view = "repo";
        render();
      });
    });
  }

  async function renderRepos() {
    const [repos, github] = await Promise.all([
      (await api("/api/v1/repos")).json(),
      (await api("/api/v1/github/status")).json(),
    ]);
    const rows = repos.length
      ? repos.map((repo) => `
          <div class="repo-row">
            <span class="repo-icon">⬡</span>
            <span class="repo-name" data-open="${esc(repo.id)}"><b>${esc(repo.full_name)}</b><small>${esc(repo.default_branch)} · ${esc(repo.health)}</small></span>
            ${canWrite() ? `<button class="button button-dark" data-scan="${esc(repo.id)}" type="button">Scan</button><button class="text-link" data-disconnect="${esc(repo.id)}" type="button">Disconnect</button>` : `<span class="role-note">View only</span>`}
          </div>`).join("")
      : `<p class="empty-state">No connected repositories.</p>`;
    viewRoot.innerHTML = `
      ${github.configured ? "" : banner("GitHub App credentials are not configured on the server, so repository connection is unavailable.")}
      <div class="dash-heading"><div><h2>Repositories</h2><p class="dash-subtitle">Only repositories you explicitly connect are scanned.</p></div></div>
      ${rows}`;
    viewRoot.querySelectorAll("[data-open]").forEach((el) => {
      el.addEventListener("click", () => {
        state.detail = el.dataset.open;
        state.view = "repo";
        render();
      });
    });
    viewRoot.querySelectorAll("[data-scan]").forEach((button) => {
      button.addEventListener("click", async () => {
        const response = await api(`/api/v1/repos/${button.dataset.scan}/scans`, { method: "POST" });
        const data = await response.json();
        if (!response.ok) return showNotice(data.detail);
        const job = await waitForJob(data.job_id);
        showNotice(job?.status === "completed" ? "Scan finished." : (job?.error || job?.detail || `Scan ${job?.status || "queued"}`), job?.status !== "completed");
        render();
      });
    });
    viewRoot.querySelectorAll("[data-disconnect]").forEach((button) => {
      button.addEventListener("click", async () => {
        await api(`/api/v1/github/repos/${button.dataset.disconnect}/disconnect`, { method: "POST" });
        render();
      });
    });
  }

  async function renderRepo() {
    const response = await api(`/api/v1/repos/${state.detail}`);
    const repo = await response.json();
    if (!response.ok) {
      viewRoot.innerHTML = banner(repo.detail || "Repository not found.", true);
      return;
    }
    const scans = (repo.scans || []).length
      ? repo.scans.map((scan) => `<div class="issue-row"><div><b>${scan.status}</b><div class="dash-subtitle">${scan.summary || scan.trigger || "scan"}</div></div><span>${scan.commit_sha ? scan.commit_sha.slice(0, 7) : "—"}</span></div>`).join("")
      : `<p class="empty-state">No scans yet.</p>`;
    const issues = (repo.issues || []).length
      ? repo.issues.map((issue) => `<div class="issue-row" data-issue="${esc(issue.id)}"><div><b>${esc(issue.title)}</b><div class="dash-subtitle">${esc(issue.file_path || issue.category)}</div></div><span class="pill ${esc(issue.severity)}">${esc(issue.severity)}</span><span>${esc(issue.status)}</span></div>`).join("")
      : `<p class="empty-state">No issues recorded for this repository.</p>`;
    viewRoot.innerHTML = `
      <div class="dash-heading"><div><h2>${esc(repo.full_name)}</h2><p class="dash-subtitle">${esc(repo.language || "language unknown")} · health ${esc(repo.health)} · last scan ${esc(repo.last_scanned_at || "never")}</p></div></div>
      <div class="action-row">
        <button class="button button-dark" data-scan="${repo.id}" type="button">Scan now</button>
        <button class="text-link" id="back-repos" type="button">Back to repositories</button>
      </div>
      <h3>Scan history</h3>${scans}
      <h3>Detected issues</h3>${issues}`;
    document.querySelector("#back-repos").addEventListener("click", () => setView("repos"));
    viewRoot.querySelector("[data-scan]")?.addEventListener("click", async (event) => {
      const response = await api(`/api/v1/repos/${event.currentTarget.dataset.scan}/scans`, { method: "POST" });
      const data = await response.json();
      if (!response.ok) return showNotice(data.detail);
      await waitForJob(data.job_id);
      render();
    });
    viewRoot.querySelectorAll("[data-issue]").forEach((row) => {
      row.addEventListener("click", () => {
        state.detail = row.dataset.issue;
        state.view = "issue";
        render();
      });
    });
  }

  async function renderIssues() {
    const issues = await (await api("/api/v1/issues")).json();
    if (!issues.length) {
      viewRoot.innerHTML = `<div class="dash-heading"><div><h2>Maintenance</h2><p class="dash-subtitle">Detected issues from completed scans.</p></div></div><p class="empty-state">No issues yet. Connect a repository and run a scan.</p>`;
      return;
    }
    viewRoot.innerHTML = `<div class="dash-heading"><div><h2>Maintenance</h2></div></div>` + issues.map((issue) => `
      <div class="issue-row" data-issue="${esc(issue.id)}">
        <div><b>${esc(issue.title)}</b><div class="dash-subtitle">${esc(issue.file_path || issue.category)}</div></div>
        <span class="pill ${esc(issue.severity)}">${esc(issue.severity)}</span>
        <span>${esc(issue.status)}</span>
        <span>${esc(issue.category)}</span>
      </div>`).join("");
    viewRoot.querySelectorAll("[data-issue]").forEach((row) => {
      row.addEventListener("click", () => {
        state.detail = row.dataset.issue;
        state.view = "issue";
        render();
      });
    });
  }

  async function renderIssue() {
    const issue = await (await api(`/api/v1/issues/${state.detail}`)).json();
    if (issue.detail) {
      viewRoot.innerHTML = banner(issue.detail, true);
      return;
    }
    const proposals = (issue.proposals || []).map((item) => `<button class="text-link" data-fix="${esc(item.id)}" type="button">${esc(item.provider)} · ${esc(item.status)}</button>`).join("") || "<p class='empty-state'>No proposals yet.</p>";
    viewRoot.innerHTML = `
      <div class="dash-heading"><div><h2>${esc(issue.title)}</h2><p class="dash-subtitle">${esc(issue.category)} · ${esc(issue.severity)} · ${esc(issue.status)}</p></div></div>
      <p>${esc(issue.evidence || "")}</p>
      <h3>Diagnosis</h3>
      <pre class="log-block">${esc(issue.diagnosis || "Not investigated yet.")}</pre>
      ${/heuristic/i.test(issue.diagnosis || "") ? banner("This diagnosis used heuristic fallback, not a configured AI provider.") : ""}
      <div class="action-row">
        ${canWrite() ? `<button class="button button-dark" id="diagnose" type="button">Investigate</button><button class="button button-dark" id="propose" type="button">Propose fix</button>` : `<p class="role-note">Members can review issues. Owners and admins can investigate and propose fixes.</p>`}
      </div>
      <h3>Fix proposals</h3>${proposals}`;
    document.querySelector("#diagnose")?.addEventListener("click", async () => {
      const response = await api(`/api/v1/issues/${issue.id}/diagnose`, { method: "POST" });
      const data = await response.json();
      if (!response.ok) return showNotice(data.detail);
      await waitForJob(data.job_id);
      render();
    });
    document.querySelector("#propose")?.addEventListener("click", async () => {
      const response = await api(`/api/v1/issues/${issue.id}/propose-fix`, { method: "POST" });
      const data = await response.json();
      if (!response.ok) return showNotice(data.detail);
      await waitForJob(data.job_id);
      render();
    });
    viewRoot.querySelectorAll("[data-fix]").forEach((button) => {
      button.addEventListener("click", () => {
        state.detail = button.dataset.fix;
        state.view = "fix";
        render();
      });
    });
  }

  async function renderFix() {
    const response = await api(`/api/v1/fixes/${state.detail}`);
    const fix = await response.json();
    if (!response.ok) {
      viewRoot.innerHTML = banner(fix.detail || "Fix not found", true);
      return;
    }
    const runs = (fix.verification_runs || []).map((run) => `<p><b>${run.status}</b><pre class="log-block">${run.log || ""}</pre></p>`).join("") || "<p class='empty-state'>No verification runs.</p>";
    viewRoot.innerHTML = `
      <div class="dash-heading"><div><h2>Fix proposal</h2><p class="dash-subtitle">${fix.provider} · ${fix.status}</p></div></div>
      <p>${esc(fix.summary || "")}</p>
      <pre class="diff">${esc(JSON.stringify(fix.patch, null, 2))}</pre>
      <div class="action-row">
        ${canWrite() ? `<button class="button button-dark" id="verify" type="button">Verify in sandbox</button><button class="button button-dark" id="pr" type="button">Open pull request</button>` : `<p class="role-note">Verification and pull requests require an owner or admin.</p>`}
      </div>
      <h3>Verification</h3>${runs}
      <p class="feature-note">A pull request is created only after a verified sandbox run. Unverified AI output is never treated as correct.</p>`;
    document.querySelector("#verify")?.addEventListener("click", async () => {
      const result = await api(`/api/v1/fixes/${fix.id}/verify`, { method: "POST" });
      const data = await result.json();
      if (!result.ok) return showNotice(data.detail);
      const job = await waitForJob(data.job_id);
      showNotice(job?.result?.status ? `Verification ${job.result.status}` : (job?.error || job?.status));
      render();
    });
    document.querySelector("#pr")?.addEventListener("click", async () => {
      const result = await api(`/api/v1/fixes/${fix.id}/pull-request`, { method: "POST" });
      const data = await result.json();
      if (!result.ok) return showNotice(data.detail);
      const job = await waitForJob(data.job_id);
      showNotice(job?.result?.url || job?.error || job?.status);
      render();
    });
  }

  async function renderPRs() {
    const rows = await (await api("/api/v1/pull-requests")).json();
    viewRoot.innerHTML = `<div class="dash-heading"><div><h2>Pull requests</h2></div></div>` + (
      rows.length
        ? rows.map((row) => `<div class="issue-row"><div><b>${esc(row.title || "Untitled")}</b><div class="dash-subtitle">${esc(row.url || "no url")}</div></div><span>${esc(row.status)}</span><span>#${esc(row.number || "—")}</span></div>`).join("")
        : `<p class="empty-state">No pull requests have been opened by CodeCare.</p>`
    );
  }

  async function renderIntegrations() {
    const github = await (await api("/api/v1/github/status")).json();
    let available = [];
    if (github.connected) {
      const response = await api("/api/v1/github/repos/available");
      if (response.ok) available = await response.json();
    }
    const actions = canWrite()
      ? `<div class="action-row"><button class="button button-dark" id="install-gh" type="button">Install GitHub App</button><button class="button button-dark" id="oauth-gh" type="button">Connect GitHub identity</button><button class="text-link" id="revoke-gh" type="button">Revoke GitHub identity</button></div>`
      : `<p class="role-note">Only owners and admins can change GitHub connections.</p>`;
    const repoList = available.length
      ? `<h3>Authorized repositories</h3>${available.map((repo) => `<label class="repo-row"><input type="checkbox" value="${esc(repo.github_repo_id)}" ${canWrite() ? "" : "disabled"}> <span class="repo-name"><b>${esc(repo.full_name)}</b><small>${repo.private ? "private" : "public"} · ${esc(repo.default_branch)}</small></span></label>`).join("")}${canWrite() ? `<div class="action-row"><button class="button button-dark" id="connect-repos" type="button">Connect selected</button></div>` : ""}`
      : "";
    const statusLine = github.configured
      ? `<p>GitHub App is configured. ${github.connected ? `Connected as ${esc(github.account_login || github.github_login)}.` : "Not installed yet."} OAuth: ${esc(github.oauth?.status || "missing")}.</p>${actions}`
      : banner("GitHub is not configured. Set GITHUB_APP_ID, GITHUB_APP_CLIENT_ID, GITHUB_APP_CLIENT_SECRET, and the private key on the server.");
    viewRoot.innerHTML = `
      <div class="dash-heading"><div><h2>Integrations</h2><p class="dash-subtitle">GitHub access uses a GitHub App. Tokens are stored only on the server.</p></div></div>
      ${statusLine}
      ${repoList}`;
    document.querySelector("#install-gh")?.addEventListener("click", async () => {
      const response = await api("/api/v1/github/install-url");
      const data = await response.json();
      if (!response.ok) return showNotice(data.detail);
      window.location.href = data.url;
    });
    document.querySelector("#oauth-gh")?.addEventListener("click", async () => {
      const response = await api("/api/v1/github/oauth/start");
      const data = await response.json();
      if (!response.ok) return showNotice(data.detail);
      window.location.href = data.url;
    });
    document.querySelector("#revoke-gh")?.addEventListener("click", async () => {
      const response = await api("/api/v1/github/oauth/revoke", { method: "POST" });
      const data = await response.json();
      showNotice(response.ok ? "GitHub identity revoked." : (data.detail || "Revoke failed"), !response.ok);
      render();
    });
    document.querySelector("#connect-repos")?.addEventListener("click", async () => {
      const ids = [...viewRoot.querySelectorAll("input[type=checkbox]:checked")].map((item) => item.value);
      const response = await api("/api/v1/github/repos/connect", { method: "POST", body: JSON.stringify({ github_repo_ids: ids }) });
      const data = await response.json();
      showNotice(response.ok ? `Connected ${data.length} repositories.` : errorDetail(data, "Could not connect repositories."), !response.ok);
      setView("repos");
    });
  }

  async function renderBilling() {
    const [plans, status] = await Promise.all([
      (await api("/api/v1/billing/plans")).json(),
      (await api("/api/v1/billing/status")).json(),
    ]);
    viewRoot.innerHTML = `
      <div class="dash-heading"><div><h2>Billing</h2><p class="dash-subtitle">Paid access is determined on the server from Stripe, not from this page.</p></div></div>
      ${status.checkout_enabled ? "" : banner("Stripe is not configured. Checkout is disabled until STRIPE_SECRET_KEY and STRIPE_PRICE_STARTER are set.")}
      ${canWrite() ? "" : `<p class="role-note">Billing changes require an owner or admin.</p>`}
      <p>Current plan: <b>${esc(status.plan)}</b> (${esc(status.status)})</p>
      ${status.checkout_enabled && canWrite() ? `<div class="action-row"><button class="button button-dark" id="portal" type="button">Customer portal</button><button class="button button-dark" id="cancel-sub" type="button">Cancel at period end</button></div>` : ""}
      ${plans.plans.map((plan) => `
        <article class="panel" style="margin-top:10px">
          <h3>${esc(plan.name)}</h3>
          <p>${plan.monthly ? `$${plan.monthly}/month` : "Custom"}</p>
          ${plan.id !== "enterprise" && status.checkout_enabled && canWrite() ? `<button class="button button-dark" data-plan="${esc(plan.id)}" type="button">Checkout</button>
          <button class="text-link" data-change="${esc(plan.id)}" type="button">Change to this plan</button>` : ""}
        </article>`).join("")}`;
    viewRoot.querySelectorAll("[data-plan]").forEach((button) => {
      button.addEventListener("click", async () => {
        const response = await api("/api/v1/billing/checkout", { method: "POST", body: JSON.stringify({ plan: button.dataset.plan }) });
        const data = await response.json();
        if (!response.ok) return showNotice(data.detail);
        window.location.href = data.url;
      });
    });
    viewRoot.querySelectorAll("[data-change]").forEach((button) => {
      button.addEventListener("click", async () => {
        const response = await api("/api/v1/billing/change-plan", { method: "POST", body: JSON.stringify({ plan: button.dataset.change }) });
        const data = await response.json();
        showNotice(data.detail || data.ok);
      });
    });
    document.querySelector("#portal")?.addEventListener("click", async () => {
      const response = await api("/api/v1/billing/portal", { method: "POST" });
      const data = await response.json();
      if (!response.ok) return showNotice(data.detail);
      window.location.href = data.url;
    });
    document.querySelector("#cancel-sub")?.addEventListener("click", async () => {
      const response = await api("/api/v1/billing/cancel", { method: "POST" });
      const data = await response.json();
      showNotice(data.detail || data.ok);
    });
  }

  async function renderAccount() {
    const [settings, members] = await Promise.all([
      (await api("/api/v1/settings")).json(),
      (await api("/api/v1/org/members")).json(),
    ]);
    const orgs = state.me?.organizations || [];
    const orgOptions = orgs.length > 1
      ? `<label for="switch-org">Workspace</label><select id="switch-org">${orgs.map((org) => `<option value="${esc(org.id)}" ${org.id === state.me.organization?.id ? "selected" : ""}>${esc(org.name)} · ${esc(org.role)}</option>`).join("")}</select>`
      : "";
    const memberRows = Array.isArray(members)
      ? members.map((member) => `<div class="member-row"><div><b>${esc(member.full_name || member.email)}</b><div class="dash-subtitle">${esc(member.email)}</div></div><span class="role-note">${esc(member.role)}</span>${state.me?.role === "owner" && member.profile_id !== state.me.id ? `<select data-role="${esc(member.id)}" aria-label="Role for ${esc(member.email)}"><option value="owner" ${member.role === "owner" ? "selected" : ""}>owner</option><option value="admin" ${member.role === "admin" ? "selected" : ""}>admin</option><option value="member" ${member.role === "member" ? "selected" : ""}>member</option></select>` : ""}</div>`).join("")
      : banner(members.detail || "Members could not be loaded.", true);
    viewRoot.innerHTML = `
      <div class="dash-heading"><div><h2>Account</h2><p class="dash-subtitle">${esc(settings.profile.email)} · ${esc(settings.profile.role)} · ${settings.profile.email_verified ? "email verified" : "email not verified"}</p></div></div>
      <form id="account-form" class="stack-form">
        <label for="full_name">Name</label>
        <input id="full_name" name="full_name" required value="${esc(settings.profile.full_name || "")}">
        ${orgOptions}
        <button class="button button-dark" type="submit">Save profile</button>
      </form>
      <p class="feature-note">AI path: ${esc(settings.ai.provider)}. Sandbox verification requires Docker on the server and does not open a pull request when verification is inconclusive.</p>
      <h3>People</h3>
      ${canWrite() ? `<form id="invite-form" class="stack-form"><label for="invite-email">Invite by email</label><input id="invite-email" name="email" type="email" required autocomplete="email"><label for="invite-role">Role</label><select id="invite-role" name="role"><option value="member">member</option><option value="admin">admin</option></select><button class="button button-dark" type="submit">Send invite</button></form>` : `<p class="empty-state">Ask an owner or admin to invite teammates.</p>`}
      <div id="member-list">${memberRows}</div>
      <form id="password-form" class="stack-form">
        <h3>Password</h3>
        <label for="current_password">Current password</label>
        <input id="current_password" name="current_password" type="password" autocomplete="current-password">
        <p class="hint">Leave blank only if this account was created with GitHub and has no password yet.</p>
        <label for="new_password">New password</label>
        <input id="new_password" name="new_password" type="password" required minlength="10" autocomplete="new-password">
        <button class="button button-dark" type="submit">Change password</button>
      </form>
      <form id="delete-form" class="stack-form danger-zone">
        <h3>Delete account</h3>
        <p>This removes your profile. If you are the only person in a workspace, that workspace is deleted too. Type DELETE to confirm.</p>
        <label for="delete-password">Password</label>
        <input id="delete-password" name="password" type="password" autocomplete="current-password">
        <label for="delete-confirmation">Confirmation</label>
        <input id="delete-confirmation" name="confirmation" required autocomplete="off">
        <button class="button button-dark" type="submit">Delete account</button>
      </form>`;
    document.querySelector("#switch-org")?.addEventListener("change", async (event) => {
      const response = await api("/api/v1/auth/switch-organization", { method: "POST", body: JSON.stringify({ organization_id: event.target.value }) });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) return showNotice(errorDetail(data, "Could not switch workspace."));
      state.me = await requireSession();
      render();
    });
    document.querySelector("#account-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      const fullName = new FormData(event.target).get("full_name");
      const response = await api("/api/v1/settings", { method: "PATCH", body: JSON.stringify({ full_name: fullName }) });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) return showNotice(errorDetail(data, "Could not save your profile."));
      state.me = await requireSession();
      showNotice("Profile saved.", false);
      render();
    });
    document.querySelector("#invite-form")?.addEventListener("submit", async (event) => {
      event.preventDefault();
      const body = Object.fromEntries(new FormData(event.target).entries());
      const response = await api("/api/v1/org/members", { method: "POST", body: JSON.stringify(body) });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) return showNotice(errorDetail(data, "Invite failed."));
      const extra = data.invite_url ? ` Share this link: ${data.invite_url}` : "";
      showNotice(`${data.message || "Invite recorded."}${extra}`, data.delivery === "unavailable" || data.delivery === "failed");
      render();
    });
    viewRoot.querySelectorAll("[data-role]").forEach((select) => {
      select.addEventListener("change", async () => {
        const response = await api(`/api/v1/org/members/${select.dataset.role}`, { method: "PATCH", body: JSON.stringify({ role: select.value }) });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) return showNotice(errorDetail(data, "Role was not changed."));
        showNotice("Role updated.", false);
        render();
      });
    });
    document.querySelector("#password-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      const body = Object.fromEntries(new FormData(event.target).entries());
      const response = await api("/api/v1/auth/change-password", { method: "POST", body: JSON.stringify(body) });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) return showNotice(errorDetail(data, "Password was not changed."));
      event.target.reset();
      showNotice("Password updated.", false);
    });
    document.querySelector("#delete-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      const body = Object.fromEntries(new FormData(event.target).entries());
      const response = await api("/api/v1/auth/delete-account", { method: "POST", body: JSON.stringify(body) });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) return showNotice(errorDetail(data, "Account was not deleted."));
      window.location.href = "/workspace/login.html";
    });
  }

  requireSession().then((me) => {
    if (!me) return;
    state.me = me;
    render();
  });
}
