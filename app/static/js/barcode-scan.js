/* Сканирование штрих-кода камерой (html5-qrcode).
 *
 * Экспортирует общий конфиг сканера через window.MklScan, чтобы все экраны
 * (поиск по складу, packing-лист, поля ввода с кнопкой) использовали одинаковые
 * настройки, надёжные для линейных кодов Code 128 и др.
 *
 * Кнопка-поле:
 *   <input id="my-input" ...>
 *   <button type="button" data-scan-target="my-input" data-scan-reader="my-reader">📷</button>
 *   <div id="my-reader" style="display:none"></div>
 *
 * Требуется камера и HTTPS (см. локальный HTTPS проекта). Работает и как 1D-сканер.
 */
(function () {
  "use strict";

  // Форматы: QR + линейные (Code 128 — наши штрих-коды вида NVPRO_00237).
  // Явный список ускоряет и стабилизирует декодирование по сравнению с «все форматы».
  function supportedFormats() {
    if (typeof Html5QrcodeSupportedFormats === "undefined") return undefined;
    var F = Html5QrcodeSupportedFormats;
    return [
      F.QR_CODE,
      F.CODE_128,
      F.CODE_39,
      F.CODE_93,
      F.CODABAR,
      F.ITF,
      F.EAN_13,
      F.EAN_8,
      F.UPC_A,
      F.UPC_E,
    ];
  }

  // Широкая зона сканирования под линейные коды: квадрат обрезает края штрих-кода
  // (стартовые/стоповые зоны) и Code 128 не распознаётся. Ширину берём почти во весь
  // кадр, высоту — низкой. Клампим по размеру видео, иначе html5-qrcode бросает ошибку.
  function qrbox(viewW, viewH) {
    var w = Math.max(200, Math.floor(Math.min(viewW, 640) * 0.9));
    if (w > viewW - 16) w = Math.max(120, viewW - 16);
    var h = Math.max(110, Math.floor(w * 0.45));
    if (h > viewH - 16) h = Math.max(80, viewH - 16);
    return { width: w, height: h };
  }

  // Новый сканер с нативным BarcodeDetector (если поддерживается браузером —
  // Android Chrome и др.): он на порядок надёжнее JS-декодера для Code 128.
  function newScanner(readerId) {
    var cfg = {
      verbose: false,
      experimentalFeatures: { useBarCodeDetectorIfSupported: true },
    };
    var fmts = supportedFormats();
    if (fmts) cfg.formatsToSupport = fmts;
    return new Html5Qrcode(readerId, cfg);
  }

  // Конфиг start(): широкая зона + скорость. extra перекрывает поля при нужде.
  function startConfig(extra) {
    var base = { fps: 10, qrbox: qrbox };
    if (extra) {
      for (var k in extra) {
        if (Object.prototype.hasOwnProperty.call(extra, k)) base[k] = extra[k];
      }
    }
    return base;
  }

  window.MklScan = { newScanner: newScanner, startConfig: startConfig, qrbox: qrbox };

  // --- Кнопка-поле: data-scan-target/-reader --------------------------------

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
      scanner = scanner || newScanner(reader.id);
      reader.style.display = "block";
      btn.textContent = "Остановить";
      btn.classList.add("active");
      scanner
        .start(
          { facingMode: "environment" },
          startConfig(),
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
