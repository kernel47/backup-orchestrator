/* Local clocks keep ticking; management pages never poll or replace their forms. */
(() => {
  const origins = new WeakMap();

  function render() {
    for (const node of document.querySelectorAll("[data-local-clock]")) {
      if (!node.dataset.localClock) continue;
      try {
        if (!origins.has(node))
          origins.set(node, {
            epoch: Number(node.dataset.clockEpoch),
            started: performance.now(),
          });
        const origin = origins.get(node),
          date = new Date(origin.epoch + performance.now() - origin.started);
        const p = Object.fromEntries(
          new Intl.DateTimeFormat("en-GB", {
            timeZone: node.dataset.localClock,
            year: "numeric",
            month: "2-digit",
            day: "2-digit",
            hour: "2-digit",
            minute: "2-digit",
            second: "2-digit",
            hourCycle: "h23",
            timeZoneName: "short",
          })
            .formatToParts(date)
            .map((part) => [part.type, part.value]),
        );
        node.textContent = `${p.year}-${p.month}-${p.day} ${p.hour}:${p.minute}:${p.second} ${p.timeZoneName}`;
      } catch (e) {
        /* Preserve server text if the timezone is unsupported. */
      }
    }
  }
  window.orchestratorRenderClocks = render;
  render();
  setInterval(render, 1000);
})();
