/* Orchestrator date picker. The original ISO date input remains the form value. */
(() => {
  const DAY = 86400000;
  const parse = (value) => {
    const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value || "");
    if (!match) return null;
    const date = new Date(0);
    date.setUTCFullYear(
      Number(match[1]),
      Number(match[2]) - 1,
      Number(match[3]),
    );
    date.setUTCHours(0, 0, 0, 0);
    return date.getUTCFullYear() === Number(match[1]) &&
      date.getUTCMonth() === Number(match[2]) - 1 &&
      date.getUTCDate() === Number(match[3])
      ? date
      : null;
  };
  const iso = (date) =>
    `${String(date.getUTCFullYear()).padStart(4, "0")}-${String(date.getUTCMonth() + 1).padStart(2, "0")}-${String(date.getUTCDate()).padStart(2, "0")}`;
  const addDays = (date, count) => new Date(date.getTime() + count * DAY);
  const shiftMonth = (date, count) => {
    const first = new Date(date);
    first.setUTCDate(1);
    first.setUTCMonth(first.getUTCMonth() + count);
    const last = new Date(first);
    last.setUTCMonth(last.getUTCMonth() + 1, 0);
    first.setUTCDate(Math.min(date.getUTCDate(), last.getUTCDate()));
    return first;
  };
  // This export also allows calendar arithmetic checks without a browser.
  if (typeof module !== "undefined" && module.exports)
    module.exports = {
      parse,
      iso,
      addDays,
      shiftMonth,
    };
  if (typeof document === "undefined") return;
  const monthFormat = new Intl.DateTimeFormat("en-GB", {
    month: "long",
    year: "numeric",
    timeZone: "UTC",
  });
  const dateFormat = new Intl.DateTimeFormat("en-GB", {
    dateStyle: "long",
    timeZone: "UTC",
  });
  const today = () => parse(new Date().toISOString().slice(0, 10));
  let serial = 0;

  window.orchestratorEnhanceCalendars = (root = document) => {
    root.querySelectorAll('input[type="date"]').forEach((input) => {
      if (input.dataset.orchestratorEnhanced) return;
      input.dataset.orchestratorEnhanced = "true";
      const controller = new AbortController(),
        signal = controller.signal;
      input.orchestratorCleanup = () => {
        controller.abort();
        panel.remove();
      };
      const id = `orchestrator-calendar-${++serial}`;
      const label = input.closest("label");
      const caption = label
        ? [...label.childNodes]
            .filter((n) => n.nodeType === Node.TEXT_NODE)
            .map((n) => n.textContent)
            .join(" ")
            .trim()
        : "Date";
      let active = parse(input.value) || today();
      let month = new Date(active);
      month.setUTCDate(1);
      const trigger = document.createElement("button");
      trigger.id = `${id}-trigger`;
      trigger.type = "button";
      trigger.className = "date-trigger";
      trigger.setAttribute("aria-haspopup", "dialog");
      trigger.setAttribute("aria-expanded", "false");
      trigger.setAttribute("aria-controls", id);
      trigger.innerHTML =
        '<span class="date-value"></span><svg class="date-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" aria-hidden="true"><rect x="3.5" y="5" width="17" height="15.5" rx="2"/><path d="M7.5 3v4M16.5 3v4M3.5 10h17M8 14h2M14 14h2M8 17h2"/></svg>';
      input.before(trigger);
      input.hidden = true;
      if (label) label.htmlFor = trigger.id;
      const panel = document.createElement("div");
      panel.id = id;
      panel.className = "orchestrator-calendar";
      panel.hidden = true;
      panel.setAttribute("role", "dialog");
      panel.setAttribute("aria-modal", "true");
      panel.setAttribute("aria-labelledby", `${id}-title`);
      panel.innerHTML = `<div class="calendar-header"><button type="button" class="calendar-nav" aria-label="Previous month"><svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.7" aria-hidden="true"><path d="m12 5-5 5 5 5"/></svg></button><div class="calendar-title" id="${id}-title" aria-live="polite"></div><button type="button" class="calendar-nav" aria-label="Next month"><svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.7" aria-hidden="true"><path d="m8 5 5 5-5 5"/></svg></button></div><div class="calendar-weekdays" aria-hidden="true"><span>Mo</span><span>Tu</span><span>We</span><span>Th</span><span>Fr</span><span>Sa</span><span>Su</span></div><div class="calendar-days" role="grid" aria-labelledby="${id}-title"></div><div class="calendar-footer"><button type="button" class="calendar-today">Today <small>UTC</small></button><button type="button" class="calendar-close">Close</button></div>`;
      document.body.append(panel);
      const grid = panel.querySelector(".calendar-days");
      const title = panel.querySelector(".calendar-title");
      const allowed = (date) =>
        date.getUTCFullYear() >= 1 &&
        date.getUTCFullYear() <= 9999 &&
        (!input.min || iso(date) >= input.min) &&
        (!input.max || iso(date) <= input.max);

      function sync() {
        const selected = parse(input.value);
        trigger.querySelector(".date-value").textContent = selected
          ? `${String(selected.getUTCDate()).padStart(2, "0")}/${String(selected.getUTCMonth() + 1).padStart(2, "0")}/${selected.getUTCFullYear()}`
          : "Choisir une date";
        trigger.setAttribute(
          "aria-label",
          caption + (selected ? " : " + dateFormat.format(selected) : ""),
        );
        trigger.disabled = input.disabled;
      }

      function position() {
        const rect = trigger.getBoundingClientRect();
        const width = Math.min(292, window.innerWidth - 24);
        panel.style.width = `${width}px`;
        panel.style.left = `${Math.max(12, Math.min(rect.left, window.innerWidth - width - 12))}px`;
        panel.style.maxHeight = `${Math.max(160, window.innerHeight - 24)}px`;
        const height = Math.min(
          panel.scrollHeight + 2,
          window.innerHeight - 24,
        );
        panel.style.top = `${Math.max(12, Math.min(rect.bottom + 7 + height > window.innerHeight - 12 ? rect.top - height - 7 : rect.bottom + 7, window.innerHeight - height - 12))}px`;
      }

      function render(focusDay = false) {
        title.textContent = monthFormat.format(month);
        const selected = input.value;
        const current = iso(today());
        const first = addDays(month, -((month.getUTCDay() + 6) % 7));
        grid.replaceChildren();
        for (let week = 0; week < 6; week++) {
          const row = document.createElement("div");
          row.className = "calendar-week";
          row.role = "row";
          for (let day = 0; day < 7; day++) {
            const date = addDays(first, week * 7 + day);
            const value = iso(date);
            const cell = document.createElement("div");
            cell.role = "gridcell";
            cell.setAttribute("aria-selected", String(value === selected));
            const button = document.createElement("button");
            button.type = "button";
            button.className = "calendar-day";
            button.textContent = date.getUTCDate();
            button.dataset.date = value;
            button.tabIndex = value === iso(active) ? 0 : -1;
            button.disabled = !allowed(date);
            button.setAttribute("aria-label", dateFormat.format(date));
            if (date.getUTCMonth() !== month.getUTCMonth())
              button.classList.add("outside-month");
            if (value === selected) button.classList.add("is-selected");
            if (value === current) {
              button.classList.add("is-today");
              button.setAttribute("aria-current", "date");
            }
            button.addEventListener("click", () => choose(date));
            cell.append(button);
            row.append(cell);
          }
          grid.append(row);
        }
        if (!panel.hidden) position();
        if (focusDay)
          grid.querySelector(`[data-date="${iso(active)}"]`)?.focus({
            preventScroll: true,
          });
      }

      function close(restoreFocus = true) {
        panel.hidden = true;
        trigger.setAttribute("aria-expanded", "false");
        if (restoreFocus)
          trigger.focus({
            preventScroll: true,
          });
      }

      function open() {
        if (trigger.disabled) return;
        document.dispatchEvent(
          new CustomEvent("orchestrator:calendar-open", {
            detail: id,
          }),
        );
        active = parse(input.value) || today();
        month = new Date(active);
        month.setUTCDate(1);
        panel.hidden = false;
        trigger.setAttribute("aria-expanded", "true");
        render(true);
      }

      function choose(date) {
        if (!allowed(date)) return;
        input.value = iso(date);
        active = date;
        sync();
        close();
        input.dispatchEvent(
          new Event("input", {
            bubbles: true,
          }),
        );
        input.dispatchEvent(
          new Event("change", {
            bubbles: true,
          }),
        );
      }

      function moveMonth(amount, focusDay = false) {
        const next = shiftMonth(active, amount);
        if (next.getUTCFullYear() < 1 || next.getUTCFullYear() > 9999) return;
        active = next;
        month = new Date(active);
        month.setUTCDate(1);
        render(focusDay);
      }
      trigger.addEventListener("click", () =>
        panel.hidden ? open() : close(),
      );
      trigger.addEventListener("keydown", (event) => {
        if (event.key === "ArrowDown") {
          event.preventDefault();
          open();
        }
      });
      const nav = panel.querySelectorAll(".calendar-nav");
      nav[0].addEventListener("click", () => moveMonth(-1));
      nav[1].addEventListener("click", () => moveMonth(1));
      panel
        .querySelector(".calendar-today")
        .addEventListener("click", () => choose(today()));
      panel
        .querySelector(".calendar-close")
        .addEventListener("click", () => close());
      panel.addEventListener("keydown", (event) => {
        if (event.key === "Escape") {
          event.preventDefault();
          event.stopPropagation();
          close();
          return;
        }
        if (event.key === "Tab") {
          const focusable = [
            ...panel.querySelectorAll("button:not(:disabled)"),
          ].filter((button) => button.tabIndex >= 0);
          if (event.shiftKey && document.activeElement === focusable[0]) {
            event.preventDefault();
            focusable.at(-1)?.focus();
          } else if (
            !event.shiftKey &&
            document.activeElement === focusable.at(-1)
          ) {
            event.preventDefault();
            focusable[0]?.focus();
          }
          return;
        }
        if (!event.target.matches(".calendar-day")) return;
        active = parse(event.target.dataset.date);
        if (event.key === "PageUp" || event.key === "PageDown") {
          event.preventDefault();
          moveMonth(
            (event.key === "PageUp" ? -1 : 1) * (event.shiftKey ? 12 : 1),
            true,
          );
          return;
        }
        const delta = {
          ArrowLeft: -1,
          ArrowRight: 1,
          ArrowUp: -7,
          ArrowDown: 7,
          Home: -((active.getUTCDay() + 6) % 7),
          End: 6 - ((active.getUTCDay() + 6) % 7),
        }[event.key];
        if (delta === undefined) return;
        event.preventDefault();
        const next = addDays(active, delta);
        if (!allowed(next)) return;
        active = next;
        month = new Date(active);
        month.setUTCDate(1);
        render(true);
      });
      document.addEventListener(
        "pointerdown",
        (event) => {
          if (
            !panel.hidden &&
            !panel.contains(event.target) &&
            !trigger.contains(event.target)
          )
            close(false);
        },
        {
          signal,
        },
      );
      document.addEventListener(
        "orchestrator:calendar-open",
        (event) => {
          if (event.detail !== id) close(false);
        },
        {
          signal,
        },
      );
      document.addEventListener(
        "focusin",
        (event) => {
          if (
            !panel.hidden &&
            !panel.contains(event.target) &&
            event.target !== trigger
          )
            close(false);
        },
        {
          signal,
        },
      );
      window.addEventListener(
        "resize",
        () => {
          if (!panel.hidden) position();
        },
        {
          signal,
        },
      );
      document.addEventListener(
        "scroll",
        (event) => {
          if (!panel.hidden && !panel.contains(event.target)) position();
        },
        {
          capture: true,
          signal,
        },
      );
      input.addEventListener("change", sync);
      input.form?.addEventListener("reset", () => setTimeout(sync, 0));
      sync();
    });
  };
  window.orchestratorResetCalendars = (root) =>
    root
      .querySelectorAll('input[type="date"]')
      .forEach((input) => input.orchestratorCleanup?.());
  window.orchestratorEnhanceCalendars();
})();
