// Кнопка удаления модели активна, только если в модалке набрано слово "delete".
(function () {
  const modal = document.getElementById("delete-model-modal");
  const input = document.getElementById("delete-model-confirm-input");
  const btn = document.getElementById("delete-model-confirm-btn");
  if (!modal || !input || !btn) return;

  const check = () => {
    btn.disabled = input.value.trim().toLowerCase() !== "delete";
  };

  input.addEventListener("input", check);
  modal.addEventListener("shown.bs.modal", () => {
    input.value = "";
    check();
    input.focus();
  });
})();
