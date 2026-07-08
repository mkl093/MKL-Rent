/* Сканирование штрих-кода камерой в поле ввода (html5-qrcode).
 *
 * Разметка:
 *   <input id="my-input" ...>
 *   <button type="button" data-scan-target="my-input" data-scan-reader="my-reader">📷</button>
 *   <div id="my-reader" style="display:none"></div>
 *
 * Требуется камера и HTTPS (см. локальный HTTPS проекта). Работает и как 1D-сканер.
 */
(function () {
  "use strict";

  function initButton(btn) {
    var input = document.getElementById(btn.getAttribute("data-scan-target"));
    var reader = document.getElementById(btn.getAttribute("data-scan-reader"));
    if (!input || !reader) return;

    var idleLabel = btn.getAttribute("data-label") || btn.textContent || "Сканировать";
    btn.textContent = idleLabel;
    var scanner = null;
    var running = false;

    function stop() {
      var p = running && scanner ? scanner.stop() : Promise.resolve();
      return p.catch(function () {}).then(function () {
        running = false;
        reader.style.display = "none";
        btn.textContent = idleLabel;
        btn.classList.remove("active");
      });
    }

    btn.addEventListener("click", function () {
      if (running) {
        stop();
        return;
      }
      if (typeof Html5Qrcode === "undefined") {
        alert("Сканер штрих-кодов недоступен.");
        return;
      }
      scanner = scanner || new Html5Qrcode(reader.id);
      reader.style.display = "block";
      btn.textContent = "Остановить";
      btn.classList.add("active");
      scanner
        .start(
          { facingMode: "environment" },
          { fps: 10, qrbox: { width: 280, height: 170 } },
          function onDecoded(text) {
            input.value = (text || "").trim();
            if (navigator.vibrate) {
              try {
                navigator.vibrate(80);
              } catch (e) {}
            }
            stop().then(function () {
              input.focus();
            });
          },
          function onError() {}
        )
        .then(function () {
          running = true;
        })
        .catch(function () {
          reader.style.display = "none";
          btn.textContent = idleLabel;
          btn.classList.remove("active");
          alert("Камера недоступна. Разрешите доступ к камере (нужен HTTPS).");
        });
    });
  }

  function init() {
    var buttons = document.querySelectorAll("[data-scan-target]");
    for (var i = 0; i < buttons.length; i++) initButton(buttons[i]);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
