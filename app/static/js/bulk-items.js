// Массовые действия с единицами оборудования на карточке модели.
(function () {
  const selectAll = document.getElementById("bulk-select-all");
  const itemBoxes = () => Array.from(document.querySelectorAll(".bulk-item"));
  const countEl = document.getElementById("bulk-count");
  const countEchoEls = document.querySelectorAll(".bulk-count-echo");
  const buttons = [
    document.getElementById("bulk-status-btn"),
    document.getElementById("bulk-retire-btn"),
    document.getElementById("bulk-delete-btn"),
  ].filter(Boolean);

  function refresh() {
    const n = itemBoxes().filter((cb) => cb.checked).length;
    if (countEl) countEl.textContent = String(n);
    countEchoEls.forEach((el) => (el.textContent = String(n)));
    buttons.forEach((btn) => (btn.disabled = n === 0));
    if (selectAll) {
      const total = itemBoxes().length;
      selectAll.checked = total > 0 && n === total;
      selectAll.indeterminate = n > 0 && n < total;
    }
  }

  if (selectAll) {
    selectAll.addEventListener("change", () => {
      itemBoxes().forEach((cb) => (cb.checked = selectAll.checked));
      refresh();
    });
  }
  itemBoxes().forEach((cb) => cb.addEventListener("change", refresh));

  const statusSelect = document.getElementById("bulk-status-select");
  const usableWrap = document.getElementById("bulk-usable-defect-wrap");
  if (statusSelect && usableWrap) {
    const toggle = () => {
      usableWrap.style.display = statusSelect.value === "defect" ? "" : "none";
    };
    statusSelect.addEventListener("change", toggle);
    toggle();
  }

  refresh();
})();
