// Подсказки поиска по складу (ТЗ §25).
//
// Сам список подсказок приходит htmx-фрагментом с /inventory/search/suggest в
// #q-suggest (атрибуты hx-* на поле #q). Здесь — только поведение меню:
// стрелки ↑/↓ выбирают пункт, Enter открывает выбранный (без выбора — обычная
// отправка формы фильтров), Esc и клик мимо закрывают меню.
(function () {
  "use strict";

  var input = document.getElementById("q");
  var box = document.getElementById("q-suggest");
  if (!input || !box) return;

  var active = -1;

  function options() {
    return Array.prototype.slice.call(box.querySelectorAll('[role="option"]'));
  }

  function isOpen() {
    return box.children.length > 0;
  }

  function setActive(index) {
    var opts = options();
    opts.forEach(function (el, i) {
      var on = i === index;
      el.classList.toggle("active", on);
      el.setAttribute("aria-selected", on ? "true" : "false");
    });
    active = index;
    if (index >= 0 && opts[index]) {
      input.setAttribute("aria-activedescendant", opts[index].id);
      opts[index].scrollIntoView({ block: "nearest" });
    } else {
      input.removeAttribute("aria-activedescendant");
    }
  }

  function close() {
    box.innerHTML = "";
    active = -1;
    input.setAttribute("aria-expanded", "false");
    input.removeAttribute("aria-activedescendant");
  }

  // Новый фрагмент подсказок: сбросить выбор и обновить aria-expanded.
  document.body.addEventListener("htmx:afterSwap", function (evt) {
    if (evt.detail.target !== box) return;
    active = -1;
    input.removeAttribute("aria-activedescendant");
    input.setAttribute("aria-expanded", isOpen() ? "true" : "false");
  });

  input.addEventListener("keydown", function (evt) {
    if (!isOpen()) return;
    var opts = options();
    if (evt.key === "ArrowDown") {
      evt.preventDefault();
      setActive(opts.length ? (active + 1) % opts.length : -1);
    } else if (evt.key === "ArrowUp") {
      evt.preventDefault();
      setActive(opts.length ? (active <= 0 ? opts.length - 1 : active - 1) : -1);
    } else if (evt.key === "Enter") {
      if (active >= 0 && opts[active]) {
        evt.preventDefault();
        opts[active].click();
      }
      // Без выбранного пункта — обычная отправка формы фильтров.
    } else if (evt.key === "Escape") {
      evt.preventDefault();
      close();
    }
  });

  // Наведение мышью подсвечивает пункт так же, как стрелки.
  box.addEventListener("mousemove", function (evt) {
    var opt = evt.target.closest('[role="option"]');
    if (!opt) return;
    var index = options().indexOf(opt);
    if (index !== active) setActive(index);
  });

  document.addEventListener("click", function (evt) {
    if (evt.target === input || box.contains(evt.target)) return;
    close();
  });

  // Поле очищено крестиком type="search" — закрыть меню сразу, без запроса.
  input.addEventListener("search", function () {
    if (!input.value) close();
  });

  if (input.form) input.form.addEventListener("submit", close);
})();
