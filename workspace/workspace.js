const api = (path, options = {}) =>
  fetch(path, {
    credentials: "include",
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });

function showStatus(el, message, isError = true) {
  if (!el) return;
  el.textContent = message;
  el.classList.toggle("is-error", isError);
}

const loginForm = document.querySelector("#login-form");
if (loginForm) {
  loginForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const status = document.querySelector("#status");
    const body = Object.fromEntries(new FormData(loginForm).entries());
    const response = await api("/api/v1/auth/login", { method: "POST", body: JSON.stringify(body) });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      showStatus(status, data.detail || "Sign in failed.");
      return;
    }
    window.location.href = "/workspace/";
  });
}

const registerForm = document.querySelector("#register-form");
if (registerForm) {
  registerForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const status = document.querySelector("#status");
    const body = Object.fromEntries(new FormData(registerForm).entries());
    const response = await api("/api/v1/auth/register", { method: "POST", body: JSON.stringify(body) });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      showStatus(status, data.detail || "Could not create the account.");
      return;
    }
    window.location.href = "/workspace/";
  });
}

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

  function banner(text, error = false) {
    return `<div class="banner${error ? " error" : ""}">${text}</div>`;
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

  async function render() {
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
    } catch (error) {
      viewRoot.innerHTML = banner(error.message || "Unable to load this view.", true);
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
            <span class="repo-name"><b>${repo.full_name}</b><small>${repo.language || "language unknown"} · ${repo.last_scanned_at ? `scanned ${repo.last_scanned_at}` : "not scanned"}</small></span>
            <span class="repo-health ${repo.health === "attention" ? "attention" : ""}"><i></i> ${repo.health}</span>
          </div>`).join("")
      : `<p class="empty-state">No repositories connected yet. Use Integrations to install the GitHub App.</p>`;
    const activity = data.activity.length
      ? data.activity.map((item) => `<article class="activity-item"><span class="activity-symbol symbol-check">⌕</span><div class="activity-copy"><b>${item.title}</b><p>${item.body || ""}</p></div></article>`).join("")
      : `<p class="empty-state">No maintenance activity yet.</p>`;
    viewRoot.innerHTML = `
      <div class="dash-heading"><div><p class="dash-kicker">WORKSPACE</p><h2>Good to see you, ${data.greeting_name}</h2><p class="dash-subtitle">Live data from your connected repositories. Nothing here is illustrative.</p></div></div>
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
            <span class="repo-name" data-open="${repo.id}"><b>${repo.full_name}</b><small>${repo.default_branch} · ${repo.health}</small></span>
            <button class="button button-dark" data-scan="${repo.id}" type="button">Scan</button>
            <button class="text-link" data-disconnect="${repo.id}" type="button">Disconnect</button>
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
        if (!response.ok) return alert(data.detail);
        const job = await waitForJob(data.job_id);
        alert(job?.status === "completed" ? "Scan finished." : (job?.error || job?.detail || `Scan ${job?.status || "queued"}`));
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
      ? repo.issues.map((issue) => `<div class="issue-row" data-issue="${issue.id}"><div><b>${issue.title}</b><div class="dash-subtitle">${issue.file_path || issue.category}</div></div><span class="pill ${issue.severity}">${issue.severity}</span><span>${issue.status}</span></div>`).join("")
      : `<p class="empty-state">No issues recorded for this repository.</p>`;
    viewRoot.innerHTML = `
      <div class="dash-heading"><div><h2>${repo.full_name}</h2><p class="dash-subtitle">${repo.language || "language unknown"} · health ${repo.health} · last scan ${repo.last_scanned_at || "never"}</p></div></div>
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
      if (!response.ok) return alert(data.detail);
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
      <div class="issue-row" data-issue="${issue.id}">
        <div><b>${issue.title}</b><div class="dash-subtitle">${issue.file_path || issue.category}</div></div>
        <span class="pill ${issue.severity}">${issue.severity}</span>
        <span>${issue.status}</span>
        <span>${issue.category}</span>
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
    const proposals = (issue.proposals || []).map((item) => `<button class="text-link" data-fix="${item.id}" type="button">${item.provider} · ${item.status}</button>`).join("") || "<p class='empty-state'>No proposals yet.</p>";
    viewRoot.innerHTML = `
      <div class="dash-heading"><div><h2>${issue.title}</h2><p class="dash-subtitle">${issue.category} · ${issue.severity} · ${issue.status}</p></div></div>
      <p>${issue.evidence || ""}</p>
      <h3>Diagnosis</h3>
      <pre class="log-block">${issue.diagnosis || "Not investigated yet."}</pre>
      ${/heuristic/i.test(issue.diagnosis || "") ? banner("This diagnosis used heuristic fallback, not a configured AI provider.") : ""}
      <div class="action-row">
        <button class="button button-dark" id="diagnose" type="button">Investigate</button>
        <button class="button button-dark" id="propose" type="button">Propose fix</button>
      </div>
      <h3>Fix proposals</h3>${proposals}`;
    document.querySelector("#diagnose").addEventListener("click", async () => {
      const response = await api(`/api/v1/issues/${issue.id}/diagnose`, { method: "POST" });
      const data = await response.json();
      if (!response.ok) return alert(data.detail);
      await waitForJob(data.job_id);
      render();
    });
    document.querySelector("#propose").addEventListener("click", async () => {
      const response = await api(`/api/v1/issues/${issue.id}/propose-fix`, { method: "POST" });
      const data = await response.json();
      if (!response.ok) return alert(data.detail);
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
      <p>${fix.summary || ""}</p>
      <pre class="diff">${JSON.stringify(fix.patch, null, 2)}</pre>
      <div class="action-row">
        <button class="button button-dark" id="verify" type="button">Verify in sandbox</button>
        <button class="button button-dark" id="pr" type="button">Open pull request</button>
      </div>
      <h3>Verification</h3>${runs}
      <p class="feature-note">A pull request is created only after a verified sandbox run. Unverified AI output is never treated as correct.</p>`;
    document.querySelector("#verify").addEventListener("click", async () => {
      const result = await api(`/api/v1/fixes/${fix.id}/verify`, { method: "POST" });
      const data = await result.json();
      if (!result.ok) return alert(data.detail);
      const job = await waitForJob(data.job_id);
      alert(job?.result?.status ? `Verification ${job.result.status}` : (job?.error || job?.status));
      render();
    });
    document.querySelector("#pr").addEventListener("click", async () => {
      const result = await api(`/api/v1/fixes/${fix.id}/pull-request`, { method: "POST" });
      const data = await result.json();
      if (!result.ok) return alert(data.detail);
      const job = await waitForJob(data.job_id);
      alert(job?.result?.url || job?.error || job?.status);
      render();
    });
  }

  async function renderPRs() {
    const rows = await (await api("/api/v1/pull-requests")).json();
    viewRoot.innerHTML = `<div class="dash-heading"><div><h2>Pull requests</h2></div></div>` + (
      rows.length
        ? rows.map((row) => `<div class="issue-row"><div><b>${row.title || "Untitled"}</b><div class="dash-subtitle">${row.url || "no url"}</div></div><span>${row.status}</span><span>#${row.number || "—"}</span></div>`).join("")
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
    viewRoot.innerHTML = `
      <div class="dash-heading"><div><h2>Integrations</h2><p class="dash-subtitle">GitHub access uses a GitHub App. Tokens are stored only on the server.</p></div></div>
      ${github.configured ? `<p>GitHub App is configured. ${github.connected ? `Connected as ${github.account_login || github.github_login}.` : "Not installed yet."} OAuth: ${github.oauth?.status || "missing"}.</p>`
        <div class="action-row">
          <button class="button button-dark" id="install-gh" type="button">Install GitHub App</button>
          <button class="button button-dark" id="oauth-gh" type="button">Connect GitHub identity</button>
          <button class="text-link" id="revoke-gh" type="button">Revoke GitHub identity</button>
        </div>` : banner("GitHub is not configured. Set GITHUB_APP_* environment variables on the server.")}
      ${available.length ? `<h3>Authorized repositories</h3>` + available.map((repo) => `
        <label class="repo-row"><input type="checkbox" value="${repo.github_repo_id}"> <span class="repo-name"><b>${repo.full_name}</b><small>${repo.private ? "private" : "public"} · ${repo.default_branch}</small></span></label>`).join("") + `<div class="action-row"><button class="button button-dark" id="connect-repos" type="button">Connect selected</button></div>` : ""}`;
    document.querySelector("#install-gh")?.addEventListener("click", async () => {
      const response = await api("/api/v1/github/install-url");
      const data = await response.json();
      if (!response.ok) return alert(data.detail);
      window.location.href = data.url;
    });
    document.querySelector("#oauth-gh")?.addEventListener("click", async () => {
      const response = await api("/api/v1/github/oauth/start");
      const data = await response.json();
      if (!response.ok) return alert(data.detail);
      window.location.href = data.url;
    });
    document.querySelector("#revoke-gh")?.addEventListener("click", async () => {
      const response = await api("/api/v1/github/oauth/revoke", { method: "POST" });
      const data = await response.json();
      alert(response.ok ? "GitHub identity revoked." : (data.detail || "Revoke failed"));
      render();
    });
    document.querySelector("#connect-repos")?.addEventListener("click", async () => {
      const ids = [...viewRoot.querySelectorAll("input[type=checkbox]:checked")].map((item) => item.value);
      const response = await api("/api/v1/github/repos/connect", { method: "POST", body: JSON.stringify({ github_repo_ids: ids }) });
      const data = await response.json();
      alert(response.ok ? `Connected ${data.length} repositories` : data.detail);
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
      ${status.checkout_enabled ? "" : banner("Stripe is not configured. Checkout is disabled.")}
      <p>Current plan: <b>${status.plan}</b> (${status.status})</p>
      ${status.checkout_enabled ? `<div class="action-row"><button class="button button-dark" id="portal" type="button">Customer portal</button><button class="button button-dark" id="cancel-sub" type="button">Cancel at period end</button></div>` : ""}
      ${plans.plans.map((plan) => `
        <article class="panel" style="margin-top:10px">
          <h3>${plan.name}</h3>
          <p>${plan.monthly ? `$${plan.monthly}/month` : "Custom"}</p>
          ${plan.id !== "enterprise" && status.checkout_enabled ? `<button class="button button-dark" data-plan="${plan.id}" type="button">Checkout</button>
          <button class="text-link" data-change="${plan.id}" type="button">Change to this plan</button>` : ""}
        </article>`).join("")}`;
    viewRoot.querySelectorAll("[data-plan]").forEach((button) => {
      button.addEventListener("click", async () => {
        const response = await api("/api/v1/billing/checkout", { method: "POST", body: JSON.stringify({ plan: button.dataset.plan }) });
        const data = await response.json();
        if (!response.ok) return alert(data.detail);
        window.location.href = data.url;
      });
    });
    viewRoot.querySelectorAll("[data-change]").forEach((button) => {
      button.addEventListener("click", async () => {
        const response = await api("/api/v1/billing/change-plan", { method: "POST", body: JSON.stringify({ plan: button.dataset.change }) });
        const data = await response.json();
        alert(data.detail || data.ok);
      });
    });
    document.querySelector("#portal")?.addEventListener("click", async () => {
      const response = await api("/api/v1/billing/portal", { method: "POST" });
      const data = await response.json();
      if (!response.ok) return alert(data.detail);
      window.location.href = data.url;
    });
    document.querySelector("#cancel-sub")?.addEventListener("click", async () => {
      const response = await api("/api/v1/billing/cancel", { method: "POST" });
      const data = await response.json();
      alert(data.detail || data.ok);
    });
  }

  async function renderAccount() {
    const settings = await (await api("/api/v1/settings")).json();
    viewRoot.innerHTML = `
      <div class="dash-heading"><div><h2>Account</h2></div></div>
      <form id="account-form">
        <label>Name</label>
        <input name="full_name" value="${settings.profile.full_name || ""}">
        <p class="dash-subtitle">${settings.profile.email}</p>
        <button class="button button-dark" type="submit">Save</button>
      </form>
      <p class="feature-note">AI path: ${settings.ai.provider}. Sandbox verification requires Docker on the server.</p>`;
    document.querySelector("#account-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      const full_name = new FormData(event.target).get("full_name");
      await api("/api/v1/settings", { method: "PATCH", body: JSON.stringify({ full_name }) });
      state.me = await requireSession();
      render();
    });
  }

  requireSession().then((me) => {
    if (!me) return;
    state.me = me;
    render();
  });
}
