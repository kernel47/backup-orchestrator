const csrf = document.querySelector('meta[name="csrf"]').content;

function toast(message, error = false) {
  const t = document.querySelector("#toast");
  t.textContent = message;
  t.className = error ? "error" : "";
  t.style.display = "block";
  setTimeout(() => (t.style.display = "none"), 7000);
}
async function api(path, method = "POST", data = {}) {
  if (!csrf) {
    location.assign(
      "/login?next=" + encodeURIComponent(location.pathname + location.search),
    );
    return null;
  }
  const r = await fetch(path, {
    method,
    headers: {
      "Content-Type": "application/json",
      "X-CSRF-Token": csrf,
    },
    body: JSON.stringify(data),
  });
  if (r.status === 401) {
    location.assign(
      "/login?next=" + encodeURIComponent(location.pathname + location.search),
    );
    return null;
  }
  const out = await r.json();
  if (!r.ok)
    throw Error(
      typeof out.detail === "string" ? out.detail : JSON.stringify(out.detail),
    );
  return out;
}
async function action(button, path, method, data, success, redirect = null) {
  button.disabled = true;
  window.orchestratorMutationPending =
    (window.orchestratorMutationPending || 0) + 1;
  try {
    const out = await api(path, method, data);
    if (out) {
      toast(success(out));
      if (redirect) location.assign(redirect(out));
      else if (window.OrchestratorLive?.enabled())
        await window.OrchestratorLive.refresh({
          force: true,
        });
      else setTimeout(() => location.reload(), 1200);
    }
  } catch (e) {
    toast(e.message, true);
  } finally {
    button.disabled = false;
    window.orchestratorMutationPending--;
  }
}
// Delegation keeps collection actions working after fragment replacement.
document.addEventListener("click", (event) => {
  const b = event.target.closest(
    "[data-plan],[data-retry-one],[data-rerun-one],#retry-selected,#rerun-selected",
  );
  if (!b || b.disabled) return;
  if (b.matches("[data-plan]"))
    action(
      b,
      "/api/collections/plan",
      "POST",
      {
        collector_id: b.dataset.plan,
        date: b.dataset.date,
        zone: b.dataset.zone,
        host: b.dataset.host,
        host_group_id: b.dataset.hostGroup || "",
        schedule_id: b.dataset.scheduleId || "",
      },
      () => "Eligible windows queued. Satellites will execute the scripts.",
    );
  else if (b.matches("[data-rerun-one]"))
    action(
      b,
      "/api/collections/retry",
      "POST",
      {
        ids: [b.dataset.rerunOne],
        mode: "rerun",
      },
      (o) =>
        `${o.ids.length} collection queued again. Previous attempts are preserved.`,
    );
  else if (b.matches("[data-retry-one]"))
    action(
      b,
      "/api/collections/retry",
      "POST",
      {
        ids: [b.dataset.retryOne],
      },
      (o) => `${o.ids.length} collection queued.`,
    );
  else {
    const mode = b.id === "rerun-selected" ? "rerun" : "retry";
    const ids = [...document.querySelectorAll('[name="collection"]:checked')]
      .filter((c) =>
        mode === "rerun"
          ? c.dataset.status === "SUCCESS"
          : c.dataset.status !== "SUCCESS",
      )
      .map((c) => c.value);
    if (!ids.length) {
      toast(
        "Select eligible rows: Retry for failures or missing runs; Run again for successful runs.",
        true,
      );
      return;
    }
    action(
      b,
      "/api/collections/retry",
      "POST",
      {
        ids,
        mode,
      },
      (o) =>
        `${o.ids.length} collection(s) queued for retry. Previous attempts are preserved.`,
    );
  }
});
document.addEventListener("change", (event) => {
  if (event.target.id === "select-all")
    document
      .querySelectorAll('[name="collection"]')
      .forEach((c) => (c.checked = event.target.checked));
});
document.querySelectorAll("[data-update-collector]").forEach((form) =>
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const f = new FormData(form);
    try {
      const data = {
        interval_seconds: Number(f.get("interval_seconds")),
        timeout_seconds: Number(f.get("timeout_seconds")),
        max_retries: Number(f.get("max_retries")),
        enabled: f.get("enabled") === "true",
        parameters: JSON.parse(f.get("parameters")),
      };
      if (
        !data.parameters ||
        Array.isArray(data.parameters) ||
        typeof data.parameters !== "object"
      )
        throw Error("Parameters must be a JSON object.");
      action(
        form.querySelector("button"),
        "/api/collectors/" + form.dataset.updateCollector,
        "PUT",
        data,
        () => "Configuration saved. Historical windows are preserved.",
        form.dataset.returnToView
          ? () =>
              "/?view=settings&section=collectors&collector_id=" +
              encodeURIComponent(form.dataset.updateCollector)
          : null,
      );
    } catch (e) {
      toast("Invalid JSON : " + e.message, true);
    }
  }),
);
let dashboardRefresh = 12000;
try {
  const saved = localStorage.getItem("orchestrator.refresh");
  if (["0", "12000", "30000", "60000"].includes(saved))
    dashboardRefresh = Number(saved);
} catch (e) {}

document.querySelector("#create-collector")?.addEventListener("submit", (e) => {
  e.preventDefault();
  const form = e.target,
    f = new FormData(form);
  action(
    form.querySelector("button"),
    "/api/collectors",
    "POST",
    {
      id: f.get("id"),
      name: f.get("name"),
      scope: f.get("scope"),
      script: f.get("script"),
      interval_seconds: Number(f.get("interval_seconds")),
      timeout_seconds: 30,
      max_retries: 2,
      enabled: true,
      parameters: {},
    },
    () => "Collector added. It is available in the Collectors view.",
    (out) =>
      "/?view=settings&section=collectors&collector_id=" +
      encodeURIComponent(out.id),
  );
});

const sidebarToggle = document.querySelector("#sidebar-toggle");

function updateSidebarToggle() {
  const mobile = matchMedia("(max-width:720px)").matches;
  const expanded = mobile
    ? document.body.classList.contains("sidebar-expanded")
    : !document.body.classList.contains("sidebar-collapsed");
  sidebarToggle?.setAttribute("aria-expanded", String(expanded));
  sidebarToggle?.setAttribute(
    "aria-label",
    expanded ? "Collapse sidebar" : "Expand sidebar",
  );
  if (sidebarToggle)
    sidebarToggle.title = expanded ? "Collapse sidebar" : "Expand sidebar";
}
sidebarToggle?.addEventListener("click", () => {
  if (matchMedia("(max-width:720px)").matches) {
    document.body.classList.toggle("sidebar-expanded");
  } else {
    document.body.classList.toggle("sidebar-collapsed");
    try {
      localStorage.setItem(
        "orchestrator.sidebarCollapsed",
        String(document.body.classList.contains("sidebar-collapsed")),
      );
    } catch (e) {}
  }
  updateSidebarToggle();
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    document.body.classList.remove("sidebar-expanded");
    updateSidebarToggle();
  }
});
document.addEventListener("click", (e) => {
  if (
    document.body.classList.contains("sidebar-expanded") &&
    !e.target.closest(".sidebar,#sidebar-toggle")
  ) {
    document.body.classList.remove("sidebar-expanded");
    updateSidebarToggle();
  }
});
matchMedia("(max-width:720px)").addEventListener("change", updateSidebarToggle);
updateSidebarToggle();
const refreshSelect = document.querySelector("#dashboard-refresh");
if (refreshSelect) {
  refreshSelect.value = String(dashboardRefresh);
  refreshSelect.dispatchEvent(
    new Event("change", {
      bubbles: true,
    }),
  );
  refreshSelect.addEventListener("change", () => {
    try {
      localStorage.setItem("orchestrator.refresh", refreshSelect.value);
      document.querySelector("#preferences-saved").textContent =
        "Preference saved for this browser.";
    } catch (e) {
      toast("Browser storage is unavailable.", true);
    }
  });
}
document.querySelector("#scope-form")?.addEventListener("submit", (e) => {
  e.preventDefault();
  const form = e.target;
  action(
    form.querySelector("button"),
    "/api/scopes",
    "POST",
    {
      id: new FormData(form).get("id").trim(),
    },
    () => "Scope added. Its collector tab is now available.",
  );
});
document
  .querySelectorAll("[data-delete-scope]")
  .forEach((button) =>
    button.addEventListener("click", () =>
      action(
        button,
        "/api/scopes/" + encodeURIComponent(button.dataset.deleteScope),
        "DELETE",
        {},
        () => "Unused scope removed.",
      ),
    ),
  );

const accountToggle = document.querySelector("#account-toggle"),
  accountMenu = document.querySelector("#account-menu");

function closeAccountMenu() {
  if (!accountMenu) return;
  accountMenu.hidden = true;
  accountToggle.setAttribute("aria-expanded", "false");
}
accountToggle?.addEventListener("click", () => {
  const open = accountMenu.hidden;
  accountMenu.hidden = !open;
  accountToggle.setAttribute("aria-expanded", String(open));
});
document.addEventListener("click", (e) => {
  if (!e.target.closest(".account-dropdown")) closeAccountMenu();
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && accountMenu && !accountMenu.hidden) {
    closeAccountMenu();
    accountToggle.focus();
  }
});
accountMenu?.parentElement.addEventListener("focusout", (e) => {
  if (!e.currentTarget.contains(e.relatedTarget)) closeAccountMenu();
});
// Keep the selected entry visible when the sidebar navigation overflows.
function revealActiveNavigation() {
  const nav = document.querySelector(".sidebar>nav");
  let link = nav?.querySelector('[aria-current="page"]');
  if (!link) return;
  if (["documentation", "technical-architecture"].includes(link.dataset.navKey))
    link = nav.querySelector('[data-nav-key="technical-architecture"]') || link;
  const menu = nav.getBoundingClientRect(),
    item = link.getBoundingClientRect();
  if (item.top < menu.top) nav.scrollTop -= menu.top - item.top;
  else if (item.bottom > menu.bottom)
    nav.scrollTop += item.bottom - menu.bottom;
}
revealActiveNavigation();
document.addEventListener("orchestrator:render", revealActiveNavigation);
