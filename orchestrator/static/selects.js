/* Accessible select enhancement. Native controls remain the form data source. */
(() => {
  let opened = null;
  let serial = 0;
  const chevron =
    '<svg class="select-chevron" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><path d="m7 10 5 5 5-5"/></svg>';
  const check =
    '<svg class="select-check" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><path d="m3 8 3 3 7-7"/></svg>';

  window.orchestratorEnhanceSelects = (root = document) => {
    root.querySelectorAll("select:not([multiple])").forEach((select) => {
      if (select.dataset.orchestratorEnhanced) return;
      select.dataset.orchestratorEnhanced = "true";
      const id = `styled-select-${++serial}`;
      const label = select.closest("label") || select.labels?.[0];
      const labelText = label
        ? [...label.childNodes]
            .filter((n) => n.nodeType === Node.TEXT_NODE)
            .map((n) => n.textContent)
            .join(" ")
            .trim()
        : select.name;
      const control = document.createElement("div");
      control.className = "select-control";
      select.before(control);
      control.append(select);
      select.hidden = true;

      const button = document.createElement("button");
      button.type = "button";
      button.id = `${id}-trigger`;
      button.className = "select-trigger";
      button.setAttribute("role", "combobox");
      button.setAttribute("aria-label", labelText);
      button.setAttribute("aria-haspopup", "listbox");
      button.setAttribute("aria-expanded", "false");
      button.setAttribute("aria-controls", `${id}-list`);
      button.innerHTML = '<span class="select-value"></span>' + chevron;
      control.append(button);
      if (label) label.htmlFor = button.id;

      const workspace = select.id === "workspace-zone";
      if (workspace) {
        control.classList.add("workspace-select");
        button.innerHTML =
          '<svg class="workspace-selector-icon" viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true"><circle cx="12" cy="12" r="9"/><ellipse cx="12" cy="12" rx="4" ry="9"/><path d="M3 12h18M5 6.5h14M5 17.5h14"/></svg><span class="workspace-selector-copy"><small>Zone workspace</small><span class="select-value"></span></span>' +
          chevron;
      }
      const menu = document.createElement("div");
      menu.id = `${id}-list`;
      menu.className =
        "select-menu" + (workspace ? " workspace-select-menu" : "");
      menu.role = "listbox";
      menu.setAttribute("aria-label", labelText);
      menu.hidden = true;
      document.body.append(menu);
      select.orchestratorCleanup = () => {
        if (opened?.button === button) opened.close();
        menu.remove();
      };
      const options = [...select.options];
      let active = select.selectedIndex;
      let search = "";
      let lastTyped = 0;
      const items = options.map((option, index) => {
        const item = document.createElement("div");
        item.id = `${id}-option-${index}`;
        item.className = "select-option";
        item.role = "option";
        item.setAttribute("aria-disabled", String(option.disabled));
        const text = document.createElement("span");
        text.textContent = option.textContent;
        item.append(text);
        item.insertAdjacentHTML("beforeend", check);
        item.addEventListener("pointermove", () => {
          if (!option.disabled) highlight(index, false);
        });
        item.addEventListener("pointerdown", (event) => event.preventDefault());
        item.addEventListener("click", () => choose(index));
        menu.append(item);
        return item;
      });

      function sync() {
        button.querySelector(".select-value").textContent =
          options[select.selectedIndex]?.textContent || "";
        button.disabled = select.disabled;
        if (workspace)
          button.title =
            "Workspace: " +
            (options[select.selectedIndex]?.textContent ||
              "Create your first zone");
        items.forEach((item, index) =>
          item.setAttribute(
            "aria-selected",
            String(index === select.selectedIndex),
          ),
        );
      }

      function position() {
        const rect = button.getBoundingClientRect();
        const width = Math.min(
          Math.max(rect.width, 190),
          window.innerWidth - 24,
        );
        menu.style.width = `${width}px`;
        menu.style.left = `${Math.max(12, Math.min(rect.left, window.innerWidth - width - 12))}px`;
        const height = Math.min(menu.scrollHeight + 2, 240);
        const below = window.innerHeight - rect.bottom - 12;
        const above = rect.top - 12;
        if (workspace || (below < height && above > below)) {
          menu.style.maxHeight = `${Math.min(240, above - 6)}px`;
          menu.style.top = `${rect.top - Math.min(height, above - 6) - 6}px`;
        } else {
          menu.style.maxHeight = `${Math.max(60, Math.min(240, below - 6))}px`;
          menu.style.top = `${rect.bottom + 6}px`;
        }
      }

      function highlight(index, scroll = true) {
        active = index;
        items.forEach((item, i) =>
          item.classList.toggle("is-active", i === active),
        );
        button.setAttribute("aria-activedescendant", items[active]?.id || "");
        if (scroll && items[active]) {
          const item = items[active];
          if (item.offsetTop < menu.scrollTop) menu.scrollTop = item.offsetTop;
          else if (
            item.offsetTop + item.offsetHeight >
            menu.scrollTop + menu.clientHeight
          )
            menu.scrollTop =
              item.offsetTop + item.offsetHeight - menu.clientHeight;
        }
      }

      function close() {
        menu.hidden = true;
        button.setAttribute("aria-expanded", "false");
        button.removeAttribute("aria-activedescendant");
        if (opened?.button === button) opened = null;
      }

      function open() {
        if (button.disabled) return;
        opened?.close();
        menu.hidden = false;
        button.setAttribute("aria-expanded", "true");
        opened = {
          button,
          menu,
          close,
          position,
        };
        position();
        highlight(select.selectedIndex >= 0 ? select.selectedIndex : 0);
      }

      function choose(index) {
        if (!options[index] || options[index].disabled) return;
        select.selectedIndex = index;
        sync();
        close();
        button.focus();
        select.dispatchEvent(
          new Event("input", {
            bubbles: true,
          }),
        );
        select.dispatchEvent(
          new Event("change", {
            bubbles: true,
          }),
        );
      }

      function move(direction) {
        let index = active;
        for (let n = 0; n < options.length; n++) {
          index = (index + direction + options.length) % options.length;
          if (!options[index].disabled) {
            highlight(index);
            return;
          }
        }
      }
      button.addEventListener("click", (event) => {
        event.preventDefault();
        menu.hidden ? open() : close();
      });
      button.addEventListener("keydown", (event) => {
        const key = event.key;
        if (
          ["ArrowDown", "ArrowUp", "Home", "End", "Enter", " "].includes(key)
        ) {
          event.preventDefault();
          if (menu.hidden) {
            open();
            return;
          }
          if (key === "ArrowDown" || key === "ArrowUp")
            move(key === "ArrowDown" ? 1 : -1);
          else if (key === "Home" || key === "End")
            highlight(key === "Home" ? 0 : options.length - 1);
          else choose(active);
        } else if (key === "Escape") {
          event.preventDefault();
          close();
        } else if (key === "Tab") close();
        else if (
          key.length === 1 &&
          !event.ctrlKey &&
          !event.metaKey &&
          !event.altKey
        ) {
          event.preventDefault();
          if (menu.hidden) open();
          search = Date.now() - lastTyped > 700 ? key : search + key;
          lastTyped = Date.now();
          const index = options.findIndex(
            (option) =>
              !option.disabled &&
              option.textContent
                .toLocaleLowerCase()
                .startsWith(search.toLocaleLowerCase()),
          );
          if (index >= 0) highlight(index);
        }
      });
      select.addEventListener("change", sync);
      select.form?.addEventListener("reset", () => setTimeout(sync, 0));
      sync();
    });
  };
  window.orchestratorResetSelects = (root) =>
    root
      .querySelectorAll("select")
      .forEach((select) => select.orchestratorCleanup?.());
  window.orchestratorEnhanceSelects();
  document.addEventListener("pointerdown", (event) => {
    if (
      opened &&
      !opened.button.contains(event.target) &&
      !opened.menu.contains(event.target)
    )
      opened.close();
  });
  window.addEventListener("resize", () => opened?.position());
  document.addEventListener(
    "scroll",
    (event) => {
      if (opened && !opened.menu.contains(event.target)) opened.position();
    },
    true,
  );
})();
