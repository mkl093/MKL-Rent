// Drag-and-drop поверх чекбоксов на доске распределения по транспорту.
// Чекбоксы + форма "/assign" работают без JS; drag — просто ускоритель.
(function () {
  const board = document.getElementById("transport-board");
  if (!board || board.dataset.editable !== "true") return;

  const csrfToken = board.dataset.csrf;
  const moveUrl = board.dataset.moveUrl;

  let dragged = null;

  board.querySelectorAll(".mkl-drag-item[draggable='true']").forEach((el) => {
    el.addEventListener("dragstart", (e) => {
      dragged = { lineId: el.dataset.lineId, source: el.dataset.source };
      e.dataTransfer.effectAllowed = "move";
      el.classList.add("mkl-dragging");
    });
    el.addEventListener("dragend", () => el.classList.remove("mkl-dragging"));
  });

  board.querySelectorAll(".mkl-drop-zone").forEach((zone) => {
    zone.addEventListener("dragover", (e) => {
      e.preventDefault();
      e.dataTransfer.dropEffect = "move";
      zone.classList.add("mkl-drop-target");
    });
    zone.addEventListener("dragleave", () => zone.classList.remove("mkl-drop-target"));
    zone.addEventListener("drop", async (e) => {
      e.preventDefault();
      zone.classList.remove("mkl-drop-target");
      if (!dragged) return;
      const target = zone.dataset.dropzone;
      if (target === dragged.source) return;

      const body = new URLSearchParams({
        csrf_token: csrfToken,
        line_id: dragged.lineId,
        source: dragged.source,
        target: target,
      });
      dragged = null;
      try {
        const res = await fetch(moveUrl, {
          method: "POST",
          headers: { "Content-Type": "application/x-www-form-urlencoded" },
          body: body.toString(),
        });
        const data = await res.json();
        if (!data.ok) {
          alert(data.message || "Не удалось переместить позицию.");
          return;
        }
        if (data.overloaded && data.overloaded.length) {
          alert("Внимание, перегруз: " + data.overloaded.join(", "));
        }
        window.location.reload();
      } catch (err) {
        alert("Ошибка сети при перемещении.");
      }
    });
  });
})();
