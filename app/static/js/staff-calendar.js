/**
 * Календарь занятости персонала (ТЗ §54) — виджет на vis-timeline.
 *
 * Время «на проводе»: сервер отдаёт/принимает наивные строки
 * "YYYY-MM-DDTHH:MM:SS" — это настенные часы в часовом поясе компании
 * (см. app/staff/calendar_router.py). Здесь они превращаются в обычные
 * JS Date через `new Date(str)` (браузер трактует их как локальное время)
 * и обратно тем же способом — без пересчёта поясов на клиенте.
 */
(function () {
  "use strict";

  const root = document.getElementById("sc-app");
  if (!root) return;

  const CSRF = root.dataset.csrf;
  const WORK_START = root.dataset.workStart || "08:00";
  const WORK_END = root.dataset.workEnd || "18:00";
  const isGrid = root.dataset.view === "grid";
  const state = {
    view: root.dataset.view,
    availability: "all",
    filters: { q: "", department_id: "", position: "", project_id: "", type: "", status: "" },
    employees: [],
    items: [],
    projectBars: [],
    visibleStart: new Date(root.dataset.rangeStart + "T00:00:00"),
    visibleEnd: new Date(root.dataset.rangeEnd + "T23:59:59"),
    gridItems: [],
    gridProjectBars: [],
    gridRange: null,
  };

  // Пресеты цветовой маркировки проекта — держать в синхроне с
  // PROJECT_COLOR_PRESETS в app/projects/enums.py (тот же список для формы
  // проекта на сервере).
  const PROJECT_COLOR_PRESETS = [
    "#7986cb", "#33b679", "#8e24aa", "#e67c73", "#f6bf26", "#f4511e",
    "#039be5", "#616161", "#3f51b5", "#0b8043", "#d50000",
  ];

  // Цвет текста, читаемый на произвольном фоне (упрощённая яркость по sRGB —
  // точности WCAG здесь не нужно, только выбор между тёмным и белым текстом).
  function contrastText(hex) {
    const h = (hex || "").replace("#", "");
    if (h.length !== 6) return "#fff";
    const r = parseInt(h.substring(0, 2), 16);
    const g = parseInt(h.substring(2, 4), 16);
    const b = parseInt(h.substring(4, 6), 16);
    const luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255;
    return luminance > 0.6 ? "#1f2430" : "#ffffff";
  }

  function pad(n) {
    return String(n).padStart(2, "0");
  }

  function toWire(date) {
    return (
      date.getFullYear() +
      "-" + pad(date.getMonth() + 1) +
      "-" + pad(date.getDate()) +
      "T" + pad(date.getHours()) +
      ":" + pad(date.getMinutes()) +
      ":" + pad(date.getSeconds())
    );
  }

  function toDatetimeLocal(date) {
    return (
      date.getFullYear() +
      "-" + pad(date.getMonth() + 1) +
      "-" + pad(date.getDate()) +
      "T" + pad(date.getHours()) +
      ":" + pad(date.getMinutes())
    );
  }

  function fromDatetimeLocal(value) {
    return new Date(value.length === 16 ? value + ":00" : value);
  }

  async function api(url, options) {
    const resp = await fetch(url, options);
    let body = null;
    try {
      body = await resp.json();
    } catch (e) {
      /* тело может отсутствовать */
    }
    return { status: resp.status, ok: resp.ok, body };
  }

  function formParams(fields) {
    const params = new URLSearchParams();
    for (const [k, v] of Object.entries(fields)) {
      if (Array.isArray(v)) {
        v.forEach((item) => params.append(k, item));
      } else if (v !== null && v !== undefined && v !== "") {
        params.append(k, v);
      }
    }
    params.append("csrf_token", CSRF);
    return params;
  }

  // --- vis-timeline (режимы Day/Week/Month) -------------------------------
  //
  // В режиме "grid" (сетка месяца) виджет не создаётся вовсе — контейнер
  // скрыт сервером через class="d-none", а рендерит эту вьюху renderGrid().

  const groupsDS = new vis.DataSet([]);
  const itemsDS = new vis.DataSet([]);
  const container = document.getElementById("sc-timeline");
  let timeline = null;

  if (!isGrid) {
    timeline = new vis.Timeline(container, itemsDS, groupsDS, {
      start: state.visibleStart,
      end: state.visibleEnd,
      orientation: "top",
      stack: true,
      zoomMin: 1000 * 60 * 60, // 1 час
      zoomMax: 1000 * 60 * 60 * 24 * 120, // 120 дней
      editable: { add: false, updateTime: true, updateGroup: true, remove: false },
      margin: { item: 4 },
      locale: "ru",
      // По умолчанию шкала "day" (режим Месяц) подписывает деления только
      // числом («1», «3», ...) — добавляем день недели, как уже показывает
      // соседняя шкала "weekday" (режимы День/Неделя).
      format: { minorLabels: { day: "dd D" } },
      onMove: handleMove,
      // Контент строится через escapeHtml() на каждом пользовательском поле —
      // встроенный XSS-фильтр vis-timeline не нужен и вырезает наши CSS-классы.
      xss: { disabled: true },
    });
  }

  // --- Загрузка данных -----------------------------------------------------

  function currentAssignmentFilters() {
    return {
      start: toWire(state.visibleStart),
      end: toWire(state.visibleEnd),
      department_id: state.filters.department_id,
      position: state.filters.position,
      project_id: state.filters.project_id,
      types: state.filters.type,
      statuses: state.filters.status,
    };
  }

  async function loadEmployees() {
    const params = new URLSearchParams({
      q: state.filters.q,
      department_id: state.filters.department_id,
      position: state.filters.position,
    });
    const { body } = await api("/calendar/api/resources?" + params.toString());
    state.employees = body || [];
    populateEmployeeSelects(state.employees);
    if (isGrid) {
      renderGrid();
    } else {
      applyGroups();
    }
  }

  async function loadProjectBars(start, end) {
    const params = new URLSearchParams({
      start: toWire(start),
      end: toWire(end),
      project_id: state.filters.project_id,
    });
    const { body } = await api("/calendar/api/project-bars?" + params.toString());
    return body || [];
  }

  async function loadAssignments() {
    const params = new URLSearchParams(currentAssignmentFilters());
    const [assignmentsRes, bars] = await Promise.all([
      api("/calendar/api/assignments?" + params.toString()),
      loadProjectBars(state.visibleStart, state.visibleEnd),
    ]);
    state.items = assignmentsRes.body || [];
    state.projectBars = bars;
    applyItems();
  }

  function refreshAssignments() {
    if (isGrid) {
      loadGridAssignments();
    } else {
      loadAssignments();
    }
  }

  function employeeLabel(e) {
    const meta = [e.position, e.department].filter(Boolean).join(" · ");
    return (
      '<div class="sc-employee"><div class="sc-employee-name">' +
      escapeHtml(e.full_name) +
      "</div>" +
      (meta ? '<div class="sc-employee-meta">' + escapeHtml(meta) + "</div>" : "") +
      "</div>"
    );
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[c]));
  }

  function busyEmployeeIds() {
    const ids = new Set();
    for (const it of state.items) {
      if (it.blocks) ids.add(it.employee_id);
    }
    return ids;
  }

  // Псевдогруппа-строка "Проекты" сверху таймлайна — полосы на весь срок
  // проекта (см. buildProjectBarItems()). Не настоящий сотрудник, поэтому
  // не участвует в фильтре «Показывать: свободные/занятые».
  const PROJECTS_GROUP_ID = "__projects__";

  function applyGroups() {
    const busy = busyEmployeeIds();
    let visible = state.employees;
    if (state.availability === "free") visible = visible.filter((e) => !busy.has(e.id));
    if (state.availability === "busy") visible = visible.filter((e) => busy.has(e.id));
    groupsDS.clear();
    const groups = visible.map((e) => ({ id: e.id, content: employeeLabel(e) }));
    if (!isGrid && state.projectBars.length) {
      groups.unshift({
        id: PROJECTS_GROUP_ID,
        content: '<div class="sc-employee-name">Проекты</div>',
        className: "sc-projects-group",
      });
    }
    groupsDS.add(groups);
  }

  const TYPE_ICON = {
    project: "▶", busy: "●", vacation: "☀", sick: "⚕", unavailable: "✕", other: "…",
  };

  // --- Разделение по месяцам (общее для сетки и timeline-режимов) -----------

  const MONTH_NAMES = [
    "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
    "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь",
  ];
  // Сокращения для подписи первого числа месяца («1 сент.», как в Google
  // Calendar). Май в такой связке читается как «1 мая» — родительный падеж.
  const MONTH_SHORT = [
    "янв.", "февр.", "мар.", "апр.", "мая", "июн.",
    "июл.", "авг.", "сент.", "окт.", "нояб.", "дек.",
  ];

  // Заголовок «Август 2026» (+ бейдж «текущий месяц», если сегодня попадает
  // в диапазон [start, end)). Если диапазон пересекает несколько месяцев —
  // «Август – Сентябрь 2026» / «Декабрь 2026 – Январь 2027».
  function renderMonthHeading(start, end) {
    const lastDay = new Date(end.getTime() - 1);
    const startLabel = MONTH_NAMES[start.getMonth()] + " " + start.getFullYear();
    const sameMonth =
      start.getFullYear() === lastDay.getFullYear() && start.getMonth() === lastDay.getMonth();
    let text = startLabel;
    if (!sameMonth) {
      const endLabel = MONTH_NAMES[lastDay.getMonth()] + " " + lastDay.getFullYear();
      text =
        start.getFullYear() === lastDay.getFullYear()
          ? MONTH_NAMES[start.getMonth()] + " – " + endLabel
          : startLabel + " – " + endLabel;
    }
    const today = new Date();
    const isCurrent = today >= start && today < end;
    return (
      '<span class="sc-grid-month">' + text + "</span>" +
      (isCurrent ? '<span class="sc-grid-nowbadge">текущий месяц</span>' : "")
    );
  }

  // Фоновые элементы vis-timeline на каждый календарный месяц видимого
  // диапазона: слабая чередующаяся подложка + подсветка текущего месяца +
  // жирная граница слева на первом дне месяца (кроме самого первого сегмента
  // — там это просто край видимого окна, а не настоящая граница месяца).
  function buildMonthBackgroundItems(start, end) {
    const items = [];
    const today = new Date();
    const cursor = new Date(start.getFullYear(), start.getMonth(), 1);
    let first = true;
    let guard = 0;
    while (cursor < end && guard < 60) {
      guard++;
      const monthEnd = new Date(cursor.getFullYear(), cursor.getMonth() + 1, 1);
      const isCurrent =
        cursor.getFullYear() === today.getFullYear() && cursor.getMonth() === today.getMonth();
      const cls = ["sc-bg-month"];
      if (cursor.getMonth() % 2 === 1) cls.push("sc-bg-month-odd");
      if (isCurrent) cls.push("sc-bg-month-current");
      if (!first) cls.push("sc-bg-month-boundary");
      first = false;
      items.push({
        id: "bg-month-" + cursor.getFullYear() + "-" + cursor.getMonth(),
        type: "background",
        start: cursor < start ? new Date(start) : new Date(cursor),
        end: monthEnd > end ? new Date(end) : monthEnd,
        className: cls.join(" "),
      });
      cursor.setMonth(cursor.getMonth() + 1);
    }
    return items;
  }

  function itemContent(a) {
    const icon = a.status === "cancelled" ? "✕ " : (TYPE_ICON[a.type] || "") + " ";
    if (a.type === "project" && a.project_number) {
      const loc = a.project_address ? '<div class="sc-item-line">' + escapeHtml(a.project_address) + "</div>" : "";
      return (
        '<div class="sc-item-title">' + icon + escapeHtml(a.project_number) + "</div>" +
        (a.project_customer ? '<div class="sc-item-line">' + escapeHtml(a.project_customer) + "</div>" : "") +
        loc
      );
    }
    return '<div class="sc-item-title">' + icon + escapeHtml(a.title || a.type_label) + "</div>";
  }

  function itemClass(a) {
    const cls = ["sc-item", "sc-type-" + a.type];
    if (a.status === "cancelled") cls.push("sc-status-cancelled");
    return cls.join(" ");
  }

  // Цвет проекта переопределяет цвет типа (см. приписку к легенде в
  // calendar.html) — но не для отменённых: там диагональная штриховка на
  // !important в .sc-status-cancelled, инлайновый стиль её не должен перебить.
  function itemStyle(a) {
    if (a.type !== "project" || !a.project_color || a.status === "cancelled") return "";
    return (
      "background-color:" + a.project_color + ";border-color:" + a.project_color +
      ";color:" + contrastText(a.project_color) + ";"
    );
  }

  let bgIds = [];

  function buildBackgroundItems(start, end) {
    const items = buildMonthBackgroundItems(start, end);
    const [workStartH, workStartM] = WORK_START.split(":").map(Number);
    const [workEndH, workEndM] = WORK_END.split(":").map(Number);
    const today = new Date();
    const spanDays = (end - start) / 86400000;
    const day = new Date(start.getFullYear(), start.getMonth(), start.getDate());
    let guard = 0;
    while (day < end && guard < 400) {
      guard++;
      const dow = day.getDay();
      const isWeekend = dow === 0 || dow === 6;
      const nextDay = new Date(day.getFullYear(), day.getMonth(), day.getDate() + 1);
      const isToday =
        day.getFullYear() === today.getFullYear() &&
        day.getMonth() === today.getMonth() &&
        day.getDate() === today.getDate();

      if (isWeekend) {
        items.push({
          id: "bg-weekend-" + day.toISOString(),
          type: "background",
          start: new Date(day),
          end: nextDay,
          className: "sc-bg-weekend",
        });
      } else if (spanDays <= 40) {
        const workStart = new Date(day.getFullYear(), day.getMonth(), day.getDate(), workStartH, workStartM);
        const workEnd = new Date(day.getFullYear(), day.getMonth(), day.getDate(), workEndH, workEndM);
        if (workStart > day) {
          items.push({
            id: "bg-off-am-" + day.toISOString(), type: "background",
            start: new Date(day), end: workStart, className: "sc-bg-offhours",
          });
        }
        if (workEnd < nextDay) {
          items.push({
            id: "bg-off-pm-" + day.toISOString(), type: "background",
            start: workEnd, end: nextDay, className: "sc-bg-offhours",
          });
        }
      }
      if (isToday) {
        items.push({
          id: "bg-today-" + day.toISOString(), type: "background",
          start: new Date(day), end: nextDay, className: "sc-bg-today",
        });
      }
      day.setDate(day.getDate() + 1);
    }
    return items;
  }

  // Полоса проекта на весь его срок (ТЗ §54.3) — обычный item vis-timeline в
  // псевдогруппе "Проекты", а не абсолютно спозиционированный оверлей: сам
  // vis пересчитывает координаты при зуме/прокрутке. editable:false — иначе
  // handleMove() принял бы PROJECTS_GROUP_ID за employee_id при перетаскивании.
  function buildProjectBarItems() {
    return state.projectBars.map((p) => {
      const start = new Date(p.start_date + "T00:00:00");
      const end = addDays(new Date(p.end_date + "T00:00:00"), 1);
      return {
        id: "projbar-" + p.id,
        group: PROJECTS_GROUP_ID,
        start,
        end,
        content: escapeHtml(p.number),
        className: "sc-project-bar",
        editable: false,
        style: "background-color:" + p.color + ";border-color:" + p.color + ";color:" + contrastText(p.color) + ";",
        title: p.number + (p.name ? " — " + p.name : ""),
      };
    });
  }

  function applyItems() {
    itemsDS.clear();
    itemsDS.add(
      state.items.map((a) => ({
        id: a.id,
        group: a.employee_id,
        start: new Date(a.starts_at),
        end: new Date(a.ends_at),
        content: itemContent(a),
        className: itemClass(a),
        style: itemStyle(a),
        title: tooltipText(a),
        _raw: a,
      }))
    );
    itemsDS.add(buildProjectBarItems());
    const bg = buildBackgroundItems(state.visibleStart, state.visibleEnd);
    bgIds = bg.map((b) => b.id);
    itemsDS.add(bg);
    applyGroups();
    const head = document.getElementById("sc-timeline-head");
    if (head) head.innerHTML = renderMonthHeading(state.visibleStart, state.visibleEnd);
  }

  function tooltipText(a) {
    const lines = [
      state.employees.find((e) => e.id === a.employee_id)?.full_name || ("Сотрудник #" + a.employee_id),
      a.project_number ? "Проект: " + a.project_number + (a.project_name ? " — " + a.project_name : "") : "Без проекта",
      "Начало: " + a.starts_at.replace("T", " "),
      "Окончание: " + a.ends_at.replace("T", " "),
      "Тип: " + a.type_label,
      "Статус: " + a.status_label,
    ];
    if (a.comment) lines.push("Комментарий: " + a.comment);
    return lines.join("\n");
  }

  function populateEmployeeSelects(employees) {
    const sorted = [...employees].sort((a, b) => a.full_name.localeCompare(b.full_name, "ru"));
    const single = document.getElementById("sc-f-employee");
    single.innerHTML = sorted.map((e) => `<option value="${e.id}">${escapeHtml(e.full_name)}</option>`).join("");
    const multi = document.getElementById("sc-b-employees");
    multi.innerHTML = sorted.map((e) => `<option value="${e.id}">${escapeHtml(e.full_name)}</option>`).join("");
  }

  // --- Тулбар фильтров -----------------------------------------------------

  let qTimer = null;
  document.getElementById("sc-q").addEventListener("input", (e) => {
    state.filters.q = e.target.value;
    clearTimeout(qTimer);
    qTimer = setTimeout(loadEmployees, 300);
  });
  document.getElementById("sc-department").addEventListener("change", (e) => {
    state.filters.department_id = e.target.value;
    loadEmployees();
    refreshAssignments();
  });
  document.getElementById("sc-position").addEventListener("change", (e) => {
    state.filters.position = e.target.value;
    loadEmployees();
    refreshAssignments();
  });
  document.getElementById("sc-project").addEventListener("change", (e) => {
    state.filters.project_id = e.target.value;
    refreshAssignments();
  });
  document.getElementById("sc-type").addEventListener("change", (e) => {
    state.filters.type = e.target.value;
    refreshAssignments();
  });
  document.getElementById("sc-status").addEventListener("change", (e) => {
    state.filters.status = e.target.value;
    refreshAssignments();
  });
  document.getElementById("sc-availability").addEventListener("change", (e) => {
    state.availability = e.target.value;
    applyGroups();
  });
  if (isGrid) {
    document.getElementById("sc-availability-wrap").classList.add("d-none");
  }

  // --- Диапазон таймлайна: подгрузка только видимого окна -------------------

  if (!isGrid) {
    timeline.on("rangechanged", (props) => {
      state.visibleStart = props.start;
      state.visibleEnd = props.end;
      loadAssignments();
    });
  }

  // --- Drag & drop / resize -------------------------------------------------

  function handleMove(item, callback) {
    const raw = item._raw;
    const payload = {
      employee_id: item.group,
      project_id: raw.project_id || "",
      type: raw.type,
      status: raw.status,
      starts_at: toWire(item.start),
      ends_at: toWire(item.end),
      title: raw.title || "",
      comment: raw.comment || "",
    };
    api(`/calendar/api/assignments/${item.id}`, {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: formParams(payload),
    }).then(({ status, ok, body }) => {
      if (ok) {
        loadAssignments();
        callback(item);
        return;
      }
      if (status === 409) {
        const msg = conflictMessage(body.conflicts) + "\n\nВсё равно сохранить?";
        if (confirm(msg)) {
          api(`/calendar/api/assignments/${item.id}`, {
            method: "POST",
            headers: { "Content-Type": "application/x-www-form-urlencoded" },
            body: formParams({ ...payload, confirm: "1" }),
          }).then(() => {
            loadAssignments();
          });
        }
      } else {
        alert("Не удалось сохранить изменения.");
      }
      callback(null); // отменить визуальное перемещение — перезагрузим данные сами
    });
  }

  function conflictMessage(conflicts) {
    const lines = conflicts.map(
      (c) => "— " + (c.project_number ? c.project_number + " " : c.type_label + " ") +
        c.starts_at.replace("T", " ") + "–" + c.ends_at.replace("T", " ")
    );
    return "У сотрудника уже есть занятость в этот период:\n" + lines.join("\n");
  }

  if (!isGrid) {
    // --- Клик по блоку → редактирование ---------------------------------

    timeline.on("click", (props) => {
      if (props.item == null) return;
      const item = itemsDS.get(props.item);
      // item._raw отсутствует у фоновых элементов и у полос проектов
      // (buildProjectBarItems()) — им нечего редактировать через эту модалку.
      if (item && item._raw) openEditModal(item._raw);
    });

    // --- Создание занятости: правая кнопка мыши ---------------------------
    //
    // Левая кнопка на пустом месте зарезервирована самим vis-timeline под
    // прокрутку шкалы времени (панорамирование) — использовать её же для
    // создания занятости означало бы конфликтовать с этим встроенным жестом.
    // Поэтому создание — по правому клику (contextmenu): одиночный клик
    // ставит блок на 1 час по умолчанию, клик с зажатием и протягиванием —
    // выделяет произвольный диапазон.

    let rightDragAnchor = null;
    container.addEventListener("mousedown", (e) => {
      if (e.button !== 2) return;
      const props = timeline.getEventProperties(e);
      if (props.item == null && props.group != null && props.group !== PROJECTS_GROUP_ID) {
        rightDragAnchor = props;
      } else {
        rightDragAnchor = null;
      }
    });
    container.addEventListener("contextmenu", (e) => {
      e.preventDefault();
      const props = timeline.getEventProperties(e);
      // В строке "Проекты" создавать занятость нельзя — это не сотрудник.
      if (props.item != null || props.group == null || props.group === PROJECTS_GROUP_ID) {
        rightDragAnchor = null;
        return;
      }
      const groupId = props.group;
      let start = props.time;
      let end;
      if (rightDragAnchor && rightDragAnchor.group === groupId) {
        start = rightDragAnchor.time < props.time ? rightDragAnchor.time : props.time;
        end = rightDragAnchor.time < props.time ? props.time : rightDragAnchor.time;
      }
      rightDragAnchor = null;
      if (!end || end.getTime() - start.getTime() < 5 * 60 * 1000) {
        // Клик без протягивания — блок по умолчанию на 1 час.
        end = new Date(start.getTime() + 60 * 60 * 1000);
      }
      openCreateModal(groupId, start, end);
    });
  }

  // --- Сетка месяца (общий вид на всех сотрудников) -------------------------

  function startOfWeek(d) {
    const copy = new Date(d.getFullYear(), d.getMonth(), d.getDate());
    const dow = (copy.getDay() + 6) % 7; // понедельник = 0
    copy.setDate(copy.getDate() - dow);
    return copy;
  }

  function addDays(d, n) {
    return new Date(d.getFullYear(), d.getMonth(), d.getDate() + n);
  }

  function computeGridRange() {
    const monthStart = new Date(root.dataset.rangeStart + "T00:00:00");
    const monthLastDay = new Date(root.dataset.rangeEnd + "T00:00:00");
    return { start: startOfWeek(monthStart), end: addDays(startOfWeek(monthLastDay), 7) };
  }

  async function loadGridAssignments() {
    const range = state.gridRange || (state.gridRange = computeGridRange());
    const params = new URLSearchParams({
      start: toWire(range.start),
      end: toWire(range.end),
      department_id: state.filters.department_id,
      position: state.filters.position,
      project_id: state.filters.project_id,
      types: state.filters.type,
      statuses: state.filters.status,
    });
    const [assignmentsRes, bars] = await Promise.all([
      api("/calendar/api/assignments?" + params.toString()),
      loadProjectBars(range.start, range.end),
    ]);
    state.gridItems = assignmentsRes.body || [];
    state.gridProjectBars = bars;
    renderGrid();
  }

  function gridChip(a) {
    const icon = a.status === "cancelled" ? "✕" : TYPE_ICON[a.type] || "";
    const emp = state.employees.find((e) => e.id === a.employee_id);
    const name = emp ? emp.full_name.split(" ")[0] : "#" + a.employee_id;
    const label = a.type === "project" && a.project_number ? a.project_number : a.title || a.type_label;
    const cls = ["sc-grid-chip", "sc-type-" + a.type];
    if (a.status === "cancelled") cls.push("sc-status-cancelled");
    const style = itemStyle(a);
    return (
      '<div class="' + cls.join(" ") + '" data-assignment-id="' + a.id + '"' +
      (style ? ' style="' + style + '"' : "") + ' title="' +
      escapeHtml(tooltipText(a)) + '">' + icon + " " + escapeHtml(name) + " — " + escapeHtml(label) + "</div>"
    );
  }

  // Тонкая полоса-стрелка на весь срок проекта (ТЗ §54.3), над списком
  // занятости дня — не более 3 одновременно, лишние сворачиваются в "+N".
  // Скругление только на настоящих границах проекта (start_date/end_date),
  // чтобы полоса читалась непрерывной при переносе через недели.
  function gridBarsHtml(dayBars, day) {
    if (!dayBars.length) return "";
    const MAX = 3;
    const shown = dayBars.slice(0, MAX);
    const extra = dayBars.length - shown.length;
    const bars = shown
      .map((p) => {
        const s = new Date(p.start_date + "T00:00:00");
        const e = new Date(p.end_date + "T00:00:00");
        const cls = ["sc-grid-bar"];
        if (day.getTime() === s.getTime()) cls.push("sc-grid-bar-start");
        if (day.getTime() === e.getTime()) cls.push("sc-grid-bar-end");
        const titleText = p.number + (p.name ? " — " + p.name : "");
        return (
          '<div class="' + cls.join(" ") + '" style="background-color:' + p.color +
          ';" title="' + escapeHtml(titleText) + '"></div>'
        );
      })
      .join("");
    const more = extra > 0 ? '<div class="sc-grid-bar-more">+' + extra + "</div>" : "";
    return '<div class="sc-grid-bars">' + bars + more + "</div>";
  }

  function renderGrid() {
    const gridEl = document.getElementById("sc-grid");
    const range = state.gridRange || computeGridRange();
    const employeeIds = new Set(state.employees.map((e) => e.id));
    const monthAnchor = new Date(root.dataset.rangeStart + "T00:00:00");
    const monthNum = monthAnchor.getMonth();
    const todayStr = root.dataset.today;
    const weekdayNames = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"];
    const monthNext = new Date(monthAnchor.getFullYear(), monthAnchor.getMonth() + 1, 1);

    let html =
      '<div class="sc-grid-head">' + renderMonthHeading(monthAnchor, monthNext) + "</div>" +
      "<table class=\"sc-grid-table\"><thead><tr>" +
      weekdayNames.map((w) => "<th>" + w + "</th>").join("") +
      "</tr></thead><tbody>";

    let day = new Date(range.start);
    while (day < range.end) {
      html += "<tr>";
      for (let i = 0; i < 7; i++) {
        const dayEnd = addDays(day, 1);
        const dateStr = toWire(day).slice(0, 10);
        const dayOfMonth = day.getDate();
        const cls = ["sc-grid-day"];
        if (day.getMonth() !== monthNum) cls.push("sc-grid-outside");
        if (dateStr === todayStr) cls.push("sc-grid-today");
        if (day.getDay() === 0 || day.getDay() === 6) cls.push("sc-grid-weekend");
        // Граница месяца идёт «лесенкой»: жирная линия слева от 1-го числа
        // (если оно не в первой колонке) и сверху над первой неделей месяца
        // (дни 1–7 — их верхний сосед, день−7, всегда из прошлого месяца).
        if (dayOfMonth === 1 && i > 0) cls.push("sc-grid-mbound-left");
        if (dayOfMonth <= 7) cls.push("sc-grid-mbound-top");

        const dayItems = state.gridItems.filter(
          (a) =>
            employeeIds.has(a.employee_id) &&
            new Date(a.starts_at) < dayEnd &&
            new Date(a.ends_at) > day
        );
        const dayBars = state.gridProjectBars.filter(
          (p) => new Date(p.start_date + "T00:00:00") < dayEnd && new Date(p.end_date + "T00:00:00") >= day
        );

        const numCls = "sc-grid-daynum-value" + (dateStr === todayStr ? " sc-grid-daynum-today" : "");
        const numText = dayOfMonth === 1 ? "1 " + MONTH_SHORT[day.getMonth()] : String(dayOfMonth);

        html +=
          '<td class="' + cls.join(" ") + '">' +
          '<div class="sc-grid-daynum"><span class="' + numCls + '">' + numText + "</span>" +
          '<button type="button" class="sc-grid-add" data-date="' + dateStr +
          '" title="Добавить занятость">+</button></div>' +
          gridBarsHtml(dayBars, day) +
          '<div class="sc-grid-items">' + dayItems.map(gridChip).join("") + "</div></td>";
        day = addDays(day, 1);
      }
      html += "</tr>";
    }
    html += "</tbody></table>";
    gridEl.innerHTML = html;
  }

  if (isGrid) {
    document.getElementById("sc-grid").addEventListener("click", (e) => {
      const addBtn = e.target.closest(".sc-grid-add");
      if (addBtn) {
        const day = new Date(addBtn.dataset.date + "T00:00:00");
        const defaultEmployeeId = state.employees[0] ? state.employees[0].id : "";
        openCreateModal(defaultEmployeeId, day, new Date(day.getTime() + 60 * 60 * 1000));
        return;
      }
      const chip = e.target.closest(".sc-grid-chip");
      if (chip) {
        const assignment = state.gridItems.find((a) => String(a.id) === chip.dataset.assignmentId);
        if (assignment) openEditModal(assignment);
      }
    });
  }

  // --- Виджет выбора цвета проекта (модалки занятости/массового назначения) --
  //
  // Тот же набор классов .sc-color-swatch/.sc-color-swatch-none/.sc-color-custom,
  // что и в project_form.html — общий CSS (app.css), независимая разметка.

  function createColorPicker(pickerEl) {
    pickerEl.innerHTML =
      '<button type="button" class="sc-color-swatch sc-color-swatch-none" data-color="" title="Без цвета">×</button>' +
      PROJECT_COLOR_PRESETS.map(
        (hex) =>
          '<button type="button" class="sc-color-swatch" data-color="' + hex +
          '" style="background-color: ' + hex + ';" title="' + hex + '"></button>'
      ).join("") +
      '<input type="color" class="sc-color-swatch sc-color-custom" title="Свой цвет" value="#7986cb">';

    const customInput = pickerEl.querySelector(".sc-color-custom");
    const buttons = () => pickerEl.querySelectorAll(".sc-color-swatch[data-color]");
    let value = "";

    function highlight() {
      let matched = false;
      buttons().forEach((btn) => {
        const isMatch = btn.dataset.color === value;
        btn.classList.toggle("selected", isMatch);
        if (isMatch) matched = true;
      });
      customInput.classList.toggle("selected", !matched && value !== "");
    }

    buttons().forEach((btn) => {
      btn.addEventListener("click", () => {
        value = btn.dataset.color;
        if (value) customInput.value = value;
        highlight();
      });
    });
    customInput.addEventListener("input", () => {
      value = customInput.value;
      highlight();
    });

    return {
      setValue(hex) {
        value = hex || "";
        if (value) customInput.value = value;
        highlight();
      },
      getValue() {
        return value;
      },
    };
  }

  // Подставляет в пикер текущий цвет/флаг полосы выбранного в select проекта
  // (данные лежат в data-атрибутах <option> — см. calendar.html) и
  // показывает/скрывает сам блок пикера в зависимости от того, выбран ли проект.
  function syncColorPicker(selectEl, wrapEl, picker, barCheckbox) {
    const opt = selectEl.options[selectEl.selectedIndex];
    if (!opt || !opt.value) {
      wrapEl.classList.add("d-none");
      return;
    }
    wrapEl.classList.remove("d-none");
    picker.setValue(opt.dataset.color || "");
    barCheckbox.checked = opt.dataset.calendarBar === "1";
  }

  // Сохраняет цвет/полосу проекта отдельным запросом при сохранении занятости
  // (проект — общий для всех его занятостей объект, поэтому цвет не поле
  // самой занятости). Ничего не делает, если проект не выбран.
  function saveProjectColorIfNeeded(projectId, picker, barCheckbox) {
    if (!projectId) return Promise.resolve();
    return api(`/calendar/api/projects/${projectId}/color`, {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: formParams({ color: picker.getValue(), calendar_bar: barCheckbox.checked ? "1" : "" }),
    });
  }

  const colorPickerSingle = createColorPicker(document.getElementById("sc-f-color-picker"));
  const colorPickerBulk = createColorPicker(document.getElementById("sc-b-color-picker"));

  document.getElementById("sc-f-project").addEventListener("change", (e) => {
    syncColorPicker(
      e.target,
      document.getElementById("sc-f-color-wrap"),
      colorPickerSingle,
      document.getElementById("sc-f-color-bar")
    );
  });
  document.getElementById("sc-b-project").addEventListener("change", (e) => {
    syncColorPicker(
      e.target,
      document.getElementById("sc-b-color-wrap"),
      colorPickerBulk,
      document.getElementById("sc-b-color-bar")
    );
  });

  // --- Модальное окно занятости ----------------------------------------------

  const modalEl = document.getElementById("sc-modal");
  const modal = new bootstrap.Modal(modalEl);
  const form = document.getElementById("sc-form");
  const conflictBox = document.getElementById("sc-f-conflict");
  const deleteBtn = document.getElementById("sc-f-delete");
  let pendingConfirm = false;
  let editingId = null;

  function openCreateModal(employeeId, start, end) {
    editingId = null;
    pendingConfirm = false;
    document.getElementById("sc-modal-title").textContent = "Новая занятость";
    document.getElementById("sc-f-id").value = "";
    document.getElementById("sc-f-employee").value = employeeId;
    document.getElementById("sc-f-start").value = toDatetimeLocal(start);
    document.getElementById("sc-f-end").value = toDatetimeLocal(end);
    document.getElementById("sc-f-type").value = "busy";
    document.getElementById("sc-f-project").value = "";
    document.getElementById("sc-f-title").value = "";
    document.getElementById("sc-f-status").value = "planned";
    document.getElementById("sc-f-comment").value = "";
    syncColorPicker(
      document.getElementById("sc-f-project"),
      document.getElementById("sc-f-color-wrap"),
      colorPickerSingle,
      document.getElementById("sc-f-color-bar")
    );
    conflictBox.classList.add("d-none");
    deleteBtn.classList.add("d-none");
    modal.show();
  }

  function openEditModal(a) {
    editingId = a.id;
    pendingConfirm = false;
    document.getElementById("sc-modal-title").textContent = "Занятость сотрудника";
    document.getElementById("sc-f-id").value = a.id;
    document.getElementById("sc-f-employee").value = a.employee_id;
    document.getElementById("sc-f-start").value = toDatetimeLocal(new Date(a.starts_at));
    document.getElementById("sc-f-end").value = toDatetimeLocal(new Date(a.ends_at));
    document.getElementById("sc-f-type").value = a.type;
    document.getElementById("sc-f-project").value = a.project_id || "";
    document.getElementById("sc-f-title").value = a.title || "";
    document.getElementById("sc-f-status").value = a.status;
    document.getElementById("sc-f-comment").value = a.comment || "";
    syncColorPicker(
      document.getElementById("sc-f-project"),
      document.getElementById("sc-f-color-wrap"),
      colorPickerSingle,
      document.getElementById("sc-f-color-bar")
    );
    conflictBox.classList.add("d-none");
    deleteBtn.classList.remove("d-none");
    modal.show();
  }

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const payload = {
      employee_id: document.getElementById("sc-f-employee").value,
      project_id: document.getElementById("sc-f-project").value,
      type: document.getElementById("sc-f-type").value,
      status: document.getElementById("sc-f-status").value,
      starts_at: toWire(fromDatetimeLocal(document.getElementById("sc-f-start").value)),
      ends_at: toWire(fromDatetimeLocal(document.getElementById("sc-f-end").value)),
      title: document.getElementById("sc-f-title").value,
      comment: document.getElementById("sc-f-comment").value,
    };
    if (pendingConfirm) payload.confirm = "1";

    const url = editingId ? `/calendar/api/assignments/${editingId}` : "/calendar/api/assignments";
    const { status, ok, body } = await api(url, {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: formParams(payload),
    });

    if (ok) {
      modal.hide();
      saveProjectColorIfNeeded(
        payload.project_id,
        colorPickerSingle,
        document.getElementById("sc-f-color-bar")
      ).then(refreshAssignments);
      return;
    }
    if (status === 409) {
      conflictBox.textContent = conflictMessage(body.conflicts) + " Нажмите «Сохранить» ещё раз, чтобы подтвердить.";
      conflictBox.classList.remove("d-none");
      pendingConfirm = true;
    } else {
      alert("Не удалось сохранить занятость.");
    }
  });

  deleteBtn.addEventListener("click", async () => {
    if (!editingId) return;
    if (!confirm("Удалить эту занятость?")) return;
    await api(`/calendar/api/assignments/${editingId}/delete`, {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: formParams({}),
    });
    modal.hide();
    refreshAssignments();
  });

  // --- Массовое назначение ----------------------------------------------------

  const bulkModalEl = document.getElementById("sc-bulk-modal");
  const bulkModal = new bootstrap.Modal(bulkModalEl);
  const bulkForm = document.getElementById("sc-bulk-form");
  const bulkConflictBox = document.getElementById("sc-b-conflict");
  let bulkPendingConfirm = false;

  document.getElementById("sc-bulk-btn").addEventListener("click", () => {
    bulkPendingConfirm = false;
    bulkConflictBox.classList.add("d-none");
    bulkForm.reset();
    document.getElementById("sc-b-start").value = toDatetimeLocal(state.visibleStart);
    document.getElementById("sc-b-end").value = toDatetimeLocal(state.visibleEnd);
    syncColorPicker(
      document.getElementById("sc-b-project"),
      document.getElementById("sc-b-color-wrap"),
      colorPickerBulk,
      document.getElementById("sc-b-color-bar")
    );
    bulkModal.show();
  });

  bulkForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const employeeIds = Array.from(document.getElementById("sc-b-employees").selectedOptions).map((o) => o.value);
    if (employeeIds.length === 0) {
      alert("Выберите хотя бы одного сотрудника.");
      return;
    }
    const payload = {
      employee_ids: employeeIds,
      project_id: document.getElementById("sc-b-project").value,
      type: document.getElementById("sc-b-type").value,
      starts_at: toWire(fromDatetimeLocal(document.getElementById("sc-b-start").value)),
      ends_at: toWire(fromDatetimeLocal(document.getElementById("sc-b-end").value)),
      title: document.getElementById("sc-b-title").value,
    };
    if (bulkPendingConfirm) payload.confirm = "1";

    const { status, ok, body } = await api("/calendar/api/assignments/bulk", {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: formParams(payload),
    });

    if (ok) {
      bulkModal.hide();
      saveProjectColorIfNeeded(
        payload.project_id,
        colorPickerBulk,
        document.getElementById("sc-b-color-bar")
      ).then(refreshAssignments);
      return;
    }
    if (status === 409) {
      const parts = Object.entries(body.conflicts).map(([empId, conflicts]) => {
        const emp = state.employees.find((e) => String(e.id) === String(empId));
        return (emp ? emp.full_name : "Сотрудник #" + empId) + ":\n" + conflictMessage(conflicts);
      });
      bulkConflictBox.textContent = parts.join("\n\n") + "\n\nНажмите «Назначить» ещё раз, чтобы подтвердить.";
      bulkConflictBox.classList.remove("d-none");
      bulkPendingConfirm = true;
    } else {
      alert("Не удалось выполнить массовое назначение.");
    }
  });

  // --- Инициализация -----------------------------------------------------

  loadEmployees().then(refreshAssignments);
})();
