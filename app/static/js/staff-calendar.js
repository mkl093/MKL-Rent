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
  const state = {
    view: root.dataset.view,
    availability: "all",
    filters: { q: "", department_id: "", position: "", project_id: "", type: "", status: "" },
    employees: [],
    items: [],
    visibleStart: new Date(root.dataset.rangeStart + "T00:00:00"),
    visibleEnd: new Date(root.dataset.rangeEnd + "T23:59:59"),
  };

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

  // --- vis-timeline ------------------------------------------------------

  const groupsDS = new vis.DataSet([]);
  const itemsDS = new vis.DataSet([]);

  const container = document.getElementById("sc-timeline");
  const timeline = new vis.Timeline(container, itemsDS, groupsDS, {
    start: state.visibleStart,
    end: state.visibleEnd,
    orientation: "top",
    stack: true,
    zoomMin: 1000 * 60 * 60, // 1 час
    zoomMax: 1000 * 60 * 60 * 24 * 120, // 120 дней
    editable: { add: false, updateTime: true, updateGroup: true, remove: false },
    margin: { item: 4 },
    locale: "ru",
    onMove: handleMove,
    // Контент строится через escapeHtml() на каждом пользовательском поле —
    // встроенный XSS-фильтр vis-timeline не нужен и вырезает наши CSS-классы.
    xss: { disabled: true },
  });

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
    applyGroups();
  }

  async function loadAssignments() {
    const params = new URLSearchParams(currentAssignmentFilters());
    const { body } = await api("/calendar/api/assignments?" + params.toString());
    state.items = body || [];
    applyItems();
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

  function applyGroups() {
    const busy = busyEmployeeIds();
    let visible = state.employees;
    if (state.availability === "free") visible = visible.filter((e) => !busy.has(e.id));
    if (state.availability === "busy") visible = visible.filter((e) => busy.has(e.id));
    groupsDS.clear();
    groupsDS.add(
      visible.map((e) => ({ id: e.id, content: employeeLabel(e) }))
    );
  }

  const TYPE_ICON = {
    project: "▶", busy: "●", vacation: "☀", sick: "⚕", unavailable: "✕", other: "…",
  };

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

  let bgIds = [];

  function buildBackgroundItems(start, end) {
    const items = [];
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
        title: tooltipText(a),
        _raw: a,
      }))
    );
    const bg = buildBackgroundItems(state.visibleStart, state.visibleEnd);
    bgIds = bg.map((b) => b.id);
    itemsDS.add(bg);
    applyGroups();
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
    loadAssignments();
  });
  document.getElementById("sc-position").addEventListener("change", (e) => {
    state.filters.position = e.target.value;
    loadEmployees();
    loadAssignments();
  });
  document.getElementById("sc-project").addEventListener("change", (e) => {
    state.filters.project_id = e.target.value;
    loadAssignments();
  });
  document.getElementById("sc-type").addEventListener("change", (e) => {
    state.filters.type = e.target.value;
    loadAssignments();
  });
  document.getElementById("sc-status").addEventListener("change", (e) => {
    state.filters.status = e.target.value;
    loadAssignments();
  });
  document.getElementById("sc-availability").addEventListener("change", (e) => {
    state.availability = e.target.value;
    applyGroups();
  });

  // --- Диапазон таймлайна: подгрузка только видимого окна -------------------

  timeline.on("rangechanged", (props) => {
    state.visibleStart = props.start;
    state.visibleEnd = props.end;
    loadAssignments();
  });

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

  // --- Клик по блоку → редактирование ---------------------------------------

  timeline.on("click", (props) => {
    if (props.item == null) return;
    const item = itemsDS.get(props.item);
    if (item) openEditModal(item._raw);
  });

  // --- Выделение свободного места → создание -------------------------------

  let dragAnchor = null;
  container.addEventListener("mousedown", (e) => {
    const props = timeline.getEventProperties(e);
    if (props.item == null && props.group != null) {
      dragAnchor = props;
    }
  });
  container.addEventListener("mouseup", (e) => {
    if (!dragAnchor) return;
    const props = timeline.getEventProperties(e);
    const groupId = dragAnchor.group;
    let start = dragAnchor.time < props.time ? dragAnchor.time : props.time;
    let end = dragAnchor.time < props.time ? props.time : dragAnchor.time;
    dragAnchor = null;
    if (end.getTime() - start.getTime() < 5 * 60 * 1000) {
      // Клик без выделения — предложить блок по умолчанию (1 час).
      end = new Date(start.getTime() + 60 * 60 * 1000);
    }
    openCreateModal(groupId, start, end);
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
      loadAssignments();
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
    loadAssignments();
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
      loadAssignments();
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

  loadEmployees().then(loadAssignments);
})();
