/* Linked choices are local: changing a parent never requests collection history. */
(() => {
  function bind() {
    document.querySelectorAll('form[data-linked-filters]').forEach((form) => {
      if (form.dataset.linkedBound) return;
      form.dataset.linkedBound = 'true';
      const scope = form.elements.namedItem('scope');
      const collector = form.elements.namedItem('collector_id');
      const group = form.elements.namedItem('host_group_id');
      const host = form.elements.namedItem('host');

      function update(select, eligible) {
        for (const option of select.options) {
          const visible = !option.value || eligible(option);
          option.hidden = !visible;
          option.disabled = !visible;
        }
        if (select.selectedOptions[0]?.disabled) select.value = '';
        select.dispatchEvent(new Event('change', { bubbles: true }));
      }
      function collectors() {
        update(collector, (option) => !scope.value || option.dataset.scope === scope.value);
      }
      function hosts() {
        update(host, (option) => !group.value || JSON.parse(option.dataset.groups || '[]').includes(group.value));
      }
      scope.addEventListener('change', collectors);
      group.addEventListener('change', hosts);
      collectors();
      hosts();
    });
  }
  bind();
  document.addEventListener('orchestrator:render', bind);
})();
