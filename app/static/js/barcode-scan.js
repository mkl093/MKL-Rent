/* Сканирование штрих-кода камерой — свой цикл на getUserMedia + ZXing/BarcodeDetector.
 *
 * Причина отказа от html5-qrcode: библиотека рисует кроп кадра в canvas размером
 * с рамку-подсказку в CSS-пикселях (~350px на телефоне), выбрасывая разрешение
 * потока целиком. Для Code 128 вида NVPRO_00789 (156 модулей + quiet zones) этого
 * категорически не хватает — декодер получает ~1.2 px/модуль вместо нужных ~3.5-4.
 * Здесь кроп берётся и декодируется в НАТИВНОМ разрешении видео, без downsampling
 * до размера превью.
 *
 * API:
 *   window.MklScan.start(containerEl, {
 *     formats: ["code_128", ...],   // ключи в нотации BarcodeDetector, по умолчанию ["code_128"]
 *     onDecode: function (text) {},
 *     onError: function (err) {},   // err.code, err.message — ошибка старта камеры
 *     onEnded: function (reason) {} // сканер сам остановился (камера отозвана / вкладка скрыта)
 *   }) -> Promise<{ stop() }>
 *
 * Кнопка-поле (сохранено для совместимости):
 *   <input id="my-input" ...>
 *   <button type="button" data-scan-target="my-input" data-scan-reader="my-reader">📷</button>
 *   <div id="my-reader" style="display:none"></div>
 *
 * Требуется камера и HTTPS (см. локальный HTTPS проекта, scripts/make_cert.py).
 */
(function () {
  "use strict";

  var ZX = window.ZXing;

  // --- Геометрия сканирования -------------------------------------------
  //
  // Рамка-подсказка (визуальная, см. CSS .mkl-scan-guide) — 86% ширины,
  // 30% высоты кадра, по центру. Область ДЕКОДИРОВАНИЯ шире рамки на 8% с
  // каждой стороны: Code128Reader.findStartPattern требует quiet zone ≥50%
  // ширины стартового паттерна и иначе бросает NotFoundException — обрезка
  // впритык к рамке была бы вторым независимым источником отказа помимо
  // разрешения.
  var GUIDE_W_FRAC = 0.86;
  var GUIDE_H_FRAC = 0.30;
  var DECODE_PAD_FRAC = 0.08;
  // Вертикальное сжатие 1D-полосы: линейный код не несёт вертикальной
  // информации, лёгкое усреднение по высоте улучшает SNR. Больше 2x уже
  // заметно увеличивает эффективный угол наклона кода в кадре.
  var ONED_COMPRESS = 2;
  var ONED_MIN_HEIGHT = 40;
  // QR декодируется реже 1D (дороже: полный blockwise-анализ HybridBinarizer),
  // и по отдельному, более крупному центральному квадрату кадра.
  var QR_CADENCE = 5;
  var QR_CROP_FRAC = 0.85;
  var QR_CANVAS_SIZE = 480;

  var DEBOUNCE_MS_DEFAULT = 1400;
  var FPS_FALLBACK = 8; // для браузеров без requestVideoFrameCallback

  var FORMAT_MAP = ZX
    ? {
        code_128: ZX.BarcodeFormat.CODE_128,
        code_39: ZX.BarcodeFormat.CODE_39,
        code_93: ZX.BarcodeFormat.CODE_93,
        codabar: ZX.BarcodeFormat.CODABAR,
        itf: ZX.BarcodeFormat.ITF,
        ean_13: ZX.BarcodeFormat.EAN_13,
        ean_8: ZX.BarcodeFormat.EAN_8,
        upc_a: ZX.BarcodeFormat.UPC_A,
        upc_e: ZX.BarcodeFormat.UPC_E,
        qr_code: ZX.BarcodeFormat.QR_CODE,
      }
    : {};

  function ScanError(code, message) {
    this.code = code;
    this.message = message;
    this.name = "ScanError";
  }
  ScanError.prototype = Object.create(Error.prototype);

  function mapGetUserMediaError(e) {
    var name = e && e.name;
    if (name === "NotAllowedError" || name === "PermissionDeniedError") {
      return new ScanError("not-allowed", "Доступ к камере запрещён. Разрешите доступ в настройках браузера.");
    }
    if (name === "NotReadableError" || name === "TrackStartError") {
      return new ScanError("not-readable", "Камера занята другим приложением.");
    }
    if (name === "NotFoundError" || name === "DevicesNotFoundError") {
      return new ScanError("not-found", "Камера не найдена на устройстве.");
    }
    return new ScanError("unknown", "Камера недоступна: " + (e && e.message ? e.message : e));
  }

  // --- Scanner ------------------------------------------------------------

  function Scanner(containerEl, opts) {
    this.container = containerEl;
    this.opts = opts || {};
    var formats = this.opts.formats && this.opts.formats.length ? this.opts.formats : ["code_128"];
    this.wantsQr = formats.indexOf("qr_code") !== -1;
    this.formats1d = formats.filter(function (f) {
      return f !== "qr_code";
    });
    this.debounceMs = this.opts.debounceMs || DEBOUNCE_MS_DEFAULT;

    this.video = null;
    this.overlay = null;
    this.stream = null;
    this.track = null;
    this.running = false;
    this.inFlight = false;
    this.rvfcHandle = null;
    this.timeoutHandle = null;
    this.lastCode = "";
    this.lastTime = 0;
    this.geom = null;
    this.qrTick = 0;
    this.detector = null;

    this.oneDCanvas = document.createElement("canvas");
    this.oneDCtx = this.oneDCanvas.getContext("2d", { willReadFrequently: true });
    this.qrCanvas = document.createElement("canvas");
    this.qrCtx = this.qrCanvas.getContext("2d", { willReadFrequently: true });
  }

  Scanner.prototype.start = function () {
    var self = this;
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      return Promise.reject(new ScanError("no-media-devices", "Камера доступна только по HTTPS или на localhost."));
    }
    this._buildDom();

    return navigator.mediaDevices
      .getUserMedia({
        video: {
          facingMode: { ideal: "environment" },
          width: { ideal: 1920 },
          height: { ideal: 1080 },
          advanced: [{ focusMode: "continuous" }],
        },
        audio: false,
      })
      .catch(function (e) {
        self._teardownDom();
        throw mapGetUserMediaError(e);
      })
      .then(function (stream) {
        self.stream = stream;
        self.track = stream.getVideoTracks()[0];
        self.video.srcObject = stream;
        return self.video.play().catch(function () {
          /* AbortError штатно летит при смене srcObject — не критично */
        });
      })
      .then(function () {
        return self._ensureDetector();
      })
      .then(function () {
        if (!self.detector && ZX) {
          self._buildOneDReader();
        }
        self._attachLifecycleHandlers();
        self._attachTorch();
        self._attachZoom();
        self.running = true;
        self._scheduleNext();
        return self;
      });
  };

  Scanner.prototype.stop = function () {
    this.running = false;
    if (this.rvfcHandle !== null && this.video && this.video.cancelVideoFrameCallback) {
      try {
        this.video.cancelVideoFrameCallback(this.rvfcHandle);
      } catch (e) {}
    }
    this.rvfcHandle = null;
    if (this.timeoutHandle !== null) {
      clearTimeout(this.timeoutHandle);
      this.timeoutHandle = null;
    }
    this._detachLifecycleHandlers();
    if (this.video) {
      try {
        this.video.pause();
      } catch (e) {}
    }
    if (this.stream) {
      this.stream.getTracks().forEach(function (t) {
        try {
          t.stop();
        } catch (e) {}
      });
    }
    if (this.video) {
      this.video.srcObject = null;
      this.video.removeAttribute("src");
      try {
        this.video.load();
      } catch (e) {}
    }
    this.stream = null;
    this.track = null;
    this._teardownDom();
  };

  // --- DOM ------------------------------------------------------------

  Scanner.prototype._buildDom = function () {
    this.container.innerHTML = "";
    this.container.classList.add("mkl-scan-container");

    var video = document.createElement("video");
    // playsinline/muted атрибутами в разметке ДО srcObject — обязательно для
    // автовоспроизведения на iOS без выхода из user gesture цепочки.
    video.setAttribute("playsinline", "");
    video.setAttribute("muted", "");
    video.muted = true;
    video.setAttribute("autoplay", "");
    video.className = "mkl-scan-video";
    this.container.appendChild(video);
    this.video = video;

    var overlay = document.createElement("div");
    overlay.className = "mkl-scan-guide";
    this.container.appendChild(overlay);
    this.overlay = overlay;
  };

  Scanner.prototype._teardownDom = function () {
    if (this.container) this.container.innerHTML = "";
    this.video = null;
    this.overlay = null;
  };

  // --- Жизненный цикл потока -------------------------------------------

  Scanner.prototype._attachLifecycleHandlers = function () {
    var self = this;

    this._onTrackEnded = function () {
      self.stop();
      if (self.opts.onEnded) self.opts.onEnded("track-ended");
    };
    this.track.addEventListener("ended", this._onTrackEnded);

    this._onVisibility = function () {
      if (document.hidden && self.running) {
        self.stop();
        if (self.opts.onEnded) self.opts.onEnded("hidden");
      }
    };
    document.addEventListener("visibilitychange", this._onVisibility);

    this._onPageHide = function () {
      self.stop();
    };
    window.addEventListener("pagehide", this._onPageHide);

    // На iOS videoWidth/videoHeight меняются местами при повороте, а
    // loadedmetadata срабатывает только один раз — пересчёт геометрии по
    // resize у самого <video> (штатный сигнал смены intrinsic-размеров).
    this._onVideoResize = function () {
      self.geom = null;
    };
    this.video.addEventListener("resize", this._onVideoResize);

    this._onOrientationChange = function () {
      setTimeout(function () {
        self.geom = null;
      }, 300);
    };
    window.addEventListener("orientationchange", this._onOrientationChange);
  };

  Scanner.prototype._detachLifecycleHandlers = function () {
    if (this.track && this._onTrackEnded) this.track.removeEventListener("ended", this._onTrackEnded);
    if (this._onVisibility) document.removeEventListener("visibilitychange", this._onVisibility);
    if (this._onPageHide) window.removeEventListener("pagehide", this._onPageHide);
    if (this.video && this._onVideoResize) this.video.removeEventListener("resize", this._onVideoResize);
    if (this._onOrientationChange) window.removeEventListener("orientationchange", this._onOrientationChange);
  };

  // --- Подсветка и зум (при поддержке — feature-detect по getCapabilities) --

  Scanner.prototype._attachTorch = function () {
    if (!this.track || typeof this.track.getCapabilities !== "function") return;
    var caps;
    try {
      caps = this.track.getCapabilities();
    } catch (e) {
      return;
    }
    if (!caps || !caps.torch) return;

    var track = this.track;
    var on = false;
    var btn = document.createElement("button");
    btn.type = "button";
    btn.className = "btn btn-outline-secondary btn-sm mkl-scan-torch";
    btn.textContent = "Подсветка";
    btn.addEventListener("click", function () {
      on = !on;
      track.applyConstraints({ advanced: [{ torch: on }] }).catch(function () {});
      btn.classList.toggle("active", on);
    });
    this.container.appendChild(btn);
  };

  Scanner.prototype._attachZoom = function () {
    if (!this.track || typeof this.track.getCapabilities !== "function") return;
    var caps;
    try {
      caps = this.track.getCapabilities();
    } catch (e) {
      return;
    }
    if (!caps || !caps.zoom) return;

    var track = this.track;
    var min = caps.zoom.min,
      max = caps.zoom.max;
    var step = caps.zoom.step || Math.max((max - min) / 50, 0.1);
    var current = min;
    try {
      current = track.getSettings().zoom || min;
    } catch (e) {}

    var slider = document.createElement("input");
    slider.type = "range";
    slider.min = min;
    slider.max = max;
    slider.step = step;
    slider.value = current;
    slider.className = "form-range mkl-scan-zoom";
    slider.setAttribute("aria-label", "Зум камеры");
    slider.addEventListener("input", function () {
      track.applyConstraints({ advanced: [{ zoom: parseFloat(slider.value) }] }).catch(function () {});
    });
    this.container.appendChild(slider);
  };

  // --- Драйвер цикла ----------------------------------------------------

  Scanner.prototype._scheduleNext = function () {
    var self = this;
    if (!this.running) return;
    if (this.video.requestVideoFrameCallback) {
      this.rvfcHandle = this.video.requestVideoFrameCallback(function () {
        self._tick();
      });
    } else {
      this.timeoutHandle = setTimeout(function () {
        self._tick();
      }, 1000 / FPS_FALLBACK);
    }
  };

  Scanner.prototype._tick = function () {
    var self = this;
    if (!this.running || this.inFlight) {
      this._scheduleNext();
      return;
    }
    this.inFlight = true;
    this._decodeOnce()
      .catch(function () {})
      .then(function () {
        self.inFlight = false;
        self._scheduleNext();
      });
  };

  Scanner.prototype._decodeOnce = function () {
    var self = this;
    if (this.detector) {
      return this.detector
        .detect(this.video)
        .then(function (codes) {
          if (codes && codes.length) handleDecode(self, codes[0].rawValue);
        })
        .catch(function () {});
    }
    if (!ZX) return Promise.resolve();
    try {
      this._decodeStep();
    } catch (e) {
      /* NotFoundException и подобные — штатный «не нашли в этом кадре» */
    }
    return Promise.resolve();
  };

  Scanner.prototype._decodeStep = function () {
    var geom = this._geometry();
    if (!geom) return;

    var text1d = this._decode1d(geom);
    if (text1d) {
      handleDecode(this, text1d);
      return;
    }

    if (this.wantsQr) {
      this.qrTick = (this.qrTick + 1) % QR_CADENCE;
      if (this.qrTick === 0) {
        var textQr = this._decodeQr(geom);
        if (textQr) handleDecode(this, textQr);
      }
    }
  };

  // --- Геометрия: доли нативного кадра -> crop-прямоугольник -----------

  Scanner.prototype._geometry = function () {
    if (this.geom) return this.geom;
    var vw = this.video.videoWidth,
      vh = this.video.videoHeight;
    if (!vw || !vh) return null;

    var guideW = vw * GUIDE_W_FRAC;
    var guideH = vh * GUIDE_H_FRAC;
    var padW = guideW * DECODE_PAD_FRAC;
    var cropW = Math.min(vw, guideW + 2 * padW);
    var cropH = guideH;

    this.geom = {
      vw: vw,
      vh: vh,
      cropX: (vw - cropW) / 2,
      cropY: (vh - cropH) / 2,
      cropW: cropW,
      cropH: cropH,
    };
    return this.geom;
  };

  Scanner.prototype._buildOneDReader = function () {
    var hints = new Map();
    var fmts = this.formats1d
      .map(function (f) {
        return FORMAT_MAP[f];
      })
      .filter(function (f) {
        return f !== undefined;
      });
    if (fmts.length) hints.set(ZX.DecodeHintType.POSSIBLE_FORMATS, fmts);
    // TRY_HARDER заставляет OneDReader пробовать реверс строки — без него
    // перевёрнутый на 180° штрих-код не распознаётся построчным ридером,
    // хотя тот же кадр через decodeFile (там TRY_HARDER уже стоит) читается.
    hints.set(ZX.DecodeHintType.TRY_HARDER, true);
    this._oneDHints = hints;
    this._oneDReader = new ZX.MultiFormatOneDReader(hints);

    if (this.wantsQr) {
      var qrHints = new Map();
      qrHints.set(ZX.DecodeHintType.POSSIBLE_FORMATS, [ZX.BarcodeFormat.QR_CODE]);
      this._qrHints = qrHints;
      this._qrReader = new ZX.MultiFormatReader();
    }
  };

  Scanner.prototype._decode1d = function (geom) {
    var destW = Math.round(geom.cropW);
    var destH = Math.max(ONED_MIN_HEIGHT, Math.round(geom.cropH / ONED_COMPRESS));
    var canvas = this.oneDCanvas,
      ctx = this.oneDCtx;
    if (canvas.width !== destW || canvas.height !== destH) {
      canvas.width = destW;
      canvas.height = destH;
    }
    ctx.drawImage(this.video, geom.cropX, geom.cropY, geom.cropW, geom.cropH, 0, 0, destW, destH);
    var imgData = ctx.getImageData(0, 0, destW, destH);
    // ZXing сам конвертирует RGBA->luma при BYTES_PER_ELEMENT===4 (packed uint32),
    // поэтому передаём Uint32Array-представление того же буфера без лишнего копирования.
    var u32 = new Uint32Array(imgData.data.buffer);
    var luminance = new ZX.RGBLuminanceSource(u32, destW, destH);
    var bitmap = new ZX.BinaryBitmap(new ZX.GlobalHistogramBinarizer(luminance));
    try {
      var result = this._oneDReader.decode(bitmap, this._oneDHints);
      return result.getText();
    } catch (e) {
      return null;
    }
  };

  Scanner.prototype._decodeQr = function (geom) {
    var side = Math.min(geom.vw, geom.vh) * QR_CROP_FRAC;
    var sx = (geom.vw - side) / 2;
    var sy = (geom.vh - side) / 2;
    var canvas = this.qrCanvas,
      ctx = this.qrCtx;
    if (canvas.width !== QR_CANVAS_SIZE || canvas.height !== QR_CANVAS_SIZE) {
      canvas.width = QR_CANVAS_SIZE;
      canvas.height = QR_CANVAS_SIZE;
    }
    ctx.drawImage(this.video, sx, sy, side, side, 0, 0, QR_CANVAS_SIZE, QR_CANVAS_SIZE);
    var imgData = ctx.getImageData(0, 0, QR_CANVAS_SIZE, QR_CANVAS_SIZE);
    var u32 = new Uint32Array(imgData.data.buffer);
    var luminance = new ZX.RGBLuminanceSource(u32, QR_CANVAS_SIZE, QR_CANVAS_SIZE);
    var bitmap = new ZX.BinaryBitmap(new ZX.HybridBinarizer(luminance));
    try {
      var result = this._qrReader.decode(bitmap, this._qrHints);
      return result.getText();
    } catch (e) {
      return null;
    }
  };

  // --- BarcodeDetector (Android Chrome и др.) ----------------------------

  Scanner.prototype._ensureDetector = function () {
    var self = this;
    if (typeof window.BarcodeDetector === "undefined") return Promise.resolve();
    return window.BarcodeDetector.getSupportedFormats()
      .then(function (supported) {
        var want = self.formats1d.concat(self.wantsQr ? ["qr_code"] : []);
        var usable = want.filter(function (f) {
          return supported.indexOf(f) !== -1;
        });
        if (usable.length) self.detector = new window.BarcodeDetector({ formats: usable });
      })
      .catch(function () {});
  };

  // --- Общий debounce + вибро-отклик -------------------------------------

  function handleDecode(scanner, text) {
    text = (text || "").trim();
    if (!text) return;
    var now = Date.now();
    if (text === scanner.lastCode && now - scanner.lastTime < scanner.debounceMs) return;
    scanner.lastCode = text;
    scanner.lastTime = now;
    if (navigator.vibrate) {
      try {
        navigator.vibrate(80);
      } catch (e) {}
    }
    if (scanner.opts.onDecode) scanner.opts.onDecode(text);
  }

  // --- Однокадровый фоллбэк: фото со штатной камеры устройства -----------
  //
  // Веб-цикл выше упирается в разрешение потока getUserMedia и его downscale
  // для decode. <input capture> передаёт кадр штатному приложению камеры —
  // тап-фокус, оптический зум, полное разрешение сенсора — то есть ровно
  // то, что экспериментально подтвердилось как рабочее на этом штрих-коде.
  // Единственный кадр, поэтому TRY_HARDER здесь не штрафует fps.

  var FILE_MAX_DIMENSION = 2400;

  function loadImageBitmap(file) {
    if (typeof createImageBitmap === "function") {
      return createImageBitmap(file);
    }
    return new Promise(function (resolve, reject) {
      var url = URL.createObjectURL(file);
      var img = new Image();
      img.onload = function () {
        URL.revokeObjectURL(url);
        resolve(img);
      };
      img.onerror = function (e) {
        URL.revokeObjectURL(url);
        reject(e);
      };
      img.src = url;
    });
  }

  function tryBarcodeDetectorOnCanvas(canvas, formats) {
    if (typeof window.BarcodeDetector === "undefined") return Promise.resolve(null);
    return window.BarcodeDetector.getSupportedFormats()
      .then(function (supported) {
        var usable = formats.filter(function (f) {
          return supported.indexOf(f) !== -1;
        });
        if (!usable.length) return null;
        var detector = new window.BarcodeDetector({ formats: usable });
        return detector.detect(canvas).then(function (codes) {
          return codes && codes.length ? codes[0].rawValue : null;
        });
      })
      .catch(function () {
        return null;
      });
  }

  function decodeFile(file, formats) {
    formats = formats && formats.length ? formats : ["code_128", "qr_code"];
    return loadImageBitmap(file).then(function (src) {
      var iw = src.width || src.naturalWidth;
      var ih = src.height || src.naturalHeight;
      var scale = Math.min(1, FILE_MAX_DIMENSION / Math.max(iw, ih));
      var w = Math.round(iw * scale),
        h = Math.round(ih * scale);
      var canvas = document.createElement("canvas");
      canvas.width = w;
      canvas.height = h;
      var ctx = canvas.getContext("2d", { willReadFrequently: true });
      ctx.drawImage(src, 0, 0, w, h);
      if (src.close) src.close();

      return tryBarcodeDetectorOnCanvas(canvas, formats).then(function (text) {
        if (text) return text;
        if (!ZX) return null;
        var imgData = ctx.getImageData(0, 0, w, h);
        var u32 = new Uint32Array(imgData.data.buffer);
        var luminance = new ZX.RGBLuminanceSource(u32, w, h);
        var bitmap = new ZX.BinaryBitmap(new ZX.HybridBinarizer(luminance));
        var hints = new Map();
        var fmts = formats
          .map(function (f) {
            return FORMAT_MAP[f];
          })
          .filter(function (f) {
            return f !== undefined;
          });
        if (fmts.length) hints.set(ZX.DecodeHintType.POSSIBLE_FORMATS, fmts);
        hints.set(ZX.DecodeHintType.TRY_HARDER, true);
        var reader = new ZX.MultiFormatReader();
        try {
          return reader.decode(bitmap, hints).getText();
        } catch (e) {
          return null;
        }
      });
    });
  }

  // --- Публичный API ------------------------------------------------------

  function start(containerEl, opts) {
    var scanner = new Scanner(containerEl, opts);
    return scanner.start();
  }

  window.MklScan = { start: start, decodeFile: decodeFile };

  // --- Кнопка-поле: data-scan-target/-reader --------------------------------

  var BUTTON_FORMATS = [
    "code_128",
    "code_39",
    "code_93",
    "codabar",
    "itf",
    "ean_13",
    "ean_8",
    "upc_a",
    "upc_e",
    "qr_code",
  ];

  function initButton(btn) {
    var input = document.getElementById(btn.getAttribute("data-scan-target"));
    var reader = document.getElementById(btn.getAttribute("data-scan-reader"));
    if (!input || !reader) return;

    var idleLabel = btn.getAttribute("data-label") || btn.textContent || "Сканировать";
    btn.textContent = idleLabel;
    var activeScanner = null;

    function resetUi() {
      activeScanner = null;
      reader.style.display = "none";
      btn.textContent = idleLabel;
      btn.classList.remove("active");
    }

    function stopUi() {
      if (activeScanner) {
        var s = activeScanner;
        activeScanner = null;
        s.stop();
      }
      resetUi();
    }

    btn.addEventListener("click", function () {
      if (activeScanner) {
        stopUi();
        return;
      }
      reader.style.display = "block";
      btn.textContent = "Остановить";
      btn.classList.add("active");
      window.MklScan
        .start(reader, {
          formats: BUTTON_FORMATS,
          onDecode: function (text) {
            input.value = text;
            stopUi();
            input.focus();
          },
          onEnded: resetUi,
        })
        .then(function (scanner) {
          activeScanner = scanner;
        })
        .catch(function (err) {
          resetUi();
          alert((err && err.message) || "Камера недоступна.");
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
