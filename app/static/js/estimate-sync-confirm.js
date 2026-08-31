// Кнопка обновления сметы по факту активна, только если отмечена хотя бы одна
// позиция и в модалке набрано слово "update".
(function () {
  const modal = document.getElementById("estimate-sync-modal");
  const input = document.getElementById("estimate-sync-confirm-input");
  const btn = document.getElementById("estimate-sync-confirm-btn");
  if (!modal || !input || !btn) return;
  const checkboxes = modal.querySelectorAll('input[name="items"]');

  const check = () => {
    const anyChecked = Array.from(checkboxes).some((cb) => cb.checked);
    btn.disabled = !anyChecked || input.value.trim().toLowerCase() !== "update";
  };

  input.addEventListener("input", check);
  checkboxes.forEach((cb) => cb.addEventListener("change", check));
  modal.addEventListener("shown.bs.modal", () => {
    input.value = "";
    check();
    input.focus();
  });
})();
