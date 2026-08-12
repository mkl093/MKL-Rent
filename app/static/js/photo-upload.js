// Превью и вставка фото модели из буфера обмена (Ctrl+V).
(function () {
  function initPhotoUpload(inputId, dropzoneId, previewImgId, previewEmptyId) {
    const input = document.getElementById(inputId);
    const dropzone = document.getElementById(dropzoneId);
    const previewImg = document.getElementById(previewImgId);
    const previewEmpty = document.getElementById(previewEmptyId);
    if (!input || !dropzone || !previewImg || !previewEmpty) return;

    function showPreview(file) {
      previewImg.src = URL.createObjectURL(file);
      previewImg.style.display = "";
      previewEmpty.style.display = "none";
    }

    input.addEventListener("change", () => {
      if (input.files && input.files[0]) showPreview(input.files[0]);
    });

    dropzone.addEventListener("click", () => input.click());
    dropzone.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        input.click();
      }
    });

    dropzone.addEventListener("paste", (e) => {
      const items = (e.clipboardData || window.clipboardData || {}).items || [];
      for (const item of items) {
        if (item.type.startsWith("image/")) {
          const file = item.getAsFile();
          if (!file) continue;
          const named = new File([file], "pasted-image." + (item.type.split("/")[1] || "png"), { type: item.type });
          const dt = new DataTransfer();
          dt.items.add(named);
          input.files = dt.files;
          showPreview(named);
          e.preventDefault();
          break;
        }
      }
    });
  }

  initPhotoUpload("photo", "photo-dropzone", "photo-preview-img", "photo-preview-empty");
})();
