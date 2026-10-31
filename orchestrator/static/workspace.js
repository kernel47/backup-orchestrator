/* Bind every request to this tab's workspace, independently of other tabs. */
(() => {
  const zone = document.querySelector('meta[name="workspace-zone"]')?.content;
  if (!zone) return;
  const original = window.fetch.bind(window);
  window.fetch = (input, options = {}) => {
    const url = new URL(
      input instanceof Request ? input.url : input,
      location.href,
    );
    if (url.origin === location.origin) {
      const headers = new Headers(
        options.headers ||
          (input instanceof Request ? input.headers : undefined),
      );
      headers.set("X-Orchestrator-Zone", zone);
      options = {
        ...options,
        headers,
      };
    }
    return original(input, options);
  };

  function bind() {
    const role = document.body.dataset.userRole;
    if (role === "operator") {
      document
        .querySelectorAll(
          ".schedule-edit button,#group-schedule,.worker-policy button,[data-update-collector] button,#create-collector button,#scope-form button,#source-form button,#zone-form button,.zone-edit button,.host-group-form button",
        )
        .forEach((b) => {
          b.disabled = true;
          b.title = "Administrator access required";
        });
    }
    if (role === "viewer") {
      document
        .querySelectorAll(
          'form:not(.filters):not(.pagination-size):not(.workspace-picker):not([action="/logout"]) button,[data-plan],[data-retry-one],[data-rerun-one],#retry-selected,#rerun-selected,[data-rotate-worker],[data-host-state],[data-group-state],[data-delete-zone]',
        )
        .forEach((b) => {
          b.disabled = true;
          b.title = "Read-only workspace access";
        });
    }
    document.querySelectorAll("a[href]").forEach((a) => {
      const url = new URL(a.href, location.href);
      if (
        url.origin !== location.origin ||
        (!["/", "/docs"].includes(url.pathname) &&
          !url.pathname.startsWith("/collections/"))
      )
        return;
      url.searchParams.set("workspace", zone);
      a.href = url.pathname + url.search + url.hash;
    });
    document
      .querySelectorAll('form[method="get"],form.filters')
      .forEach((form) => {
        if (!form.querySelector('[name="workspace"]')) {
          const field = document.createElement("input");
          field.type = "hidden";
          field.name = "workspace";
          field.value = zone;
          form.append(field);
        }
      });
  }
  document.addEventListener("DOMContentLoaded", bind);
  document.addEventListener("orchestrator:render", bind);
})();
