// Tasks tab view for Arena Steering (`Current`, `Upcoming`, `Finished`).

import { reconcileView } from "./dom.js";
import { escapeHtml } from "./markdown.js";
import { relativeTime } from "./timestamps.js";

import { CHEVRON_SELECT_SVG as CHEVRON_DOWN_SVG } from "./icons.js";

const TASK_TYPES = ["report", "code", "docs", "ops", "misc"];
const FINISHED_TASK_PAGE_SIZE = 10;
const TASK_SEARCH_KEY = "steer:taskSearch";
const TASK_TYPE_KEY = "steer:taskTypeFilter";

function readTaskSession(key) {
  try {
    return typeof sessionStorage !== "undefined" ? sessionStorage.getItem(key) || "" : "";
  } catch {
    return "";
  }
}

function writeTaskSession(key, value) {
  try {
    if (typeof sessionStorage === "undefined") return;
    if (value) sessionStorage.setItem(key, value);
    else sessionStorage.removeItem(key);
  } catch {
    // Session storage is optional.
  }
}

let taskSearchInput = readTaskSession(TASK_SEARCH_KEY);
let taskSearchQuery = taskSearchInput.trim().toLowerCase();
const savedTaskType = readTaskSession(TASK_TYPE_KEY);
let taskTypeFilter = TASK_TYPES.includes(savedTaskType) ? savedTaskType : "all";
let visibleFinishedCount = FINISHED_TASK_PAGE_SIZE;
const openTaskDetails = new Set();
let taskRenderEvents = null;

export function expandTaskTarget(id) {
  if (taskSearchQuery) openTaskDetails.clear();
  taskSearchInput = "";
  taskSearchQuery = "";
  taskTypeFilter = "all";
  writeTaskSession(TASK_SEARCH_KEY, "");
  writeTaskSession(TASK_TYPE_KEY, "");
  visibleFinishedCount = Infinity;
  openTaskDetails.add(id);
}

function formatTypeBadge(type, state) {
  const normalized = (type || "misc").toLowerCase();
  const label =
    normalized === "ops" || normalized === "operations"
      ? "Ops"
      : normalized.charAt(0).toUpperCase() + normalized.slice(1);
  return `<span class="task-type" data-task-type="${escapeHtml(normalized)}" data-phase-state="${state}">${escapeHtml(label)}</span>`;
}

function formatTaskPhases(task) {
  const types = task.types?.length ? task.types : [task.type || "misc"];
  const index = Number.isInteger(task.phase_index)
    ? task.phase_index
    : task.status === "finished" ? types.length : 0;
  const chips = types.map((type, position) =>
    formatTypeBadge(type, position < index ? "finished" : position === index ? "current" : "upcoming")
  );
  return types.length === 1
    ? chips[0]
    : `<span class="task-phases" aria-label="Ordered task phases">${chips.join("")}</span>`;
}

function taskDetailLines(task) {
  const lines = [];
  if (task.priority) lines.push(`Priority: ${task.effective_priority || task.priority}`);
  if (task.size != null) lines.push(`Size: ${task.size}`);
  if (task.scope) {
    lines.push(`Scope: ${task.scope}`);
  }
  const links = task.links || {};
  const taskIds = links.task_ids || task.task_ids || task.deps || [];
  const reportIds = links.report_ids || task.report_ids || [];
  const gateNames = links.gate_names || [];
  for (const dep of taskIds) {
    lines.push(`Needs: ${dep}`);
  }
  for (const rep of reportIds) {
    lines.push(`Report: ${rep}`);
  }
  for (const gate of gateNames) lines.push(`Gate: ${gate}`);
  for (const d of task.details || []) {
    lines.push(d);
  }
  return lines;
}

function taskMatchesFilter(task) {
  if (taskTypeFilter !== "all" && (task.type || "misc") !== taskTypeFilter) {
    return false;
  }
  if (!taskSearchQuery) {
    return true;
  }
  const haystack = [
    task.id,
    task.title,
    task.type || "",
    ...(task.types || []),
    ...taskDetailLines(task),
  ]
    .join(" ")
    .toLowerCase();
  return haystack.includes(taskSearchQuery);
}

function highlightTaskText(text) {
  if (!taskSearchQuery) return escapeHtml(text);
  const lowered = text.toLowerCase();
  const parts = [];
  let start = 0;
  while (true) {
    const index = lowered.indexOf(taskSearchQuery, start);
    if (index === -1) break;
    parts.push(escapeHtml(text.slice(start, index)));
    parts.push(`<mark class="search-hit">${escapeHtml(text.slice(index, index + taskSearchQuery.length))}</mark>`);
    start = index + taskSearchQuery.length;
  }
  parts.push(escapeHtml(text.slice(start)));
  return parts.join("");
}

const TASK_IMAGE_PATTERN = /\.(?:png|jpe?g|gif|webp|avif|bmp|svg)$/i;

function renderTaskAttachments(task) {
  const attachments = task.attachments || [];
  if (!attachments.length) return "";
  const cells = attachments
    .map((item) => {
      const label = escapeHtml(item.name || item.path || "attachment");
      const lost = Boolean(item.lost) || item.present === false;
      const thumb =
        !lost &&
        typeof item.url === "string" &&
        TASK_IMAGE_PATTERN.test(String(item.name || ""));
      const inner = thumb
        ? `<img class="task-attachment-thumb" src="${escapeHtml(item.url)}" alt="${label}" loading="lazy" decoding="async">`
        : `<span class="task-attachment-name">${label}${lost ? " (lost)" : ""}</span>`;
      return `<li class="task-attachment${lost ? " lost" : ""}" title="${label}">${inner}</li>`;
    })
    .join("");
  return `<ul class="task-attachments">${cells}</ul>`;
}

function renderTaskRow(task, selectedParentRefs) {
  const details = taskDetailLines(task);
  const count = details.length;
  const selected = selectedParentRefs.some((ref) => ref.type === "task" && ref.id === task.id);
  const isOpen =
    openTaskDetails.has(task.id) ||
    Boolean(
      taskSearchQuery &&
        details.some((d) => d.toLowerCase().includes(taskSearchQuery))
    );

  const detailsHtml = count
    ? `
      <details class="task-details" data-task-id="${escapeHtml(task.id)}" ${isOpen ? "open" : ""}>
        <summary>${count} detail${count === 1 ? "" : "s"}</summary>
        <ul class="task-detail-list">
          ${details.map((line) => `<li class="task-detail">${highlightTaskText(line)}</li>`).join("")}
        </ul>
      </details>
    `
    : "";

  const attachmentsHtml = renderTaskAttachments(task);

  return `
    <li class="task" data-task-id="${escapeHtml(task.id)}">
      <button type="button" class="task-title" data-blocked="${task.blocked ? "true" : "false"}" data-gate-running="${task.status === "upcoming" && task.running_gate === true ? "true" : "false"}" data-parent-type="task" data-parent-id="${escapeHtml(task.id)}" aria-label="${escapeHtml(task.title)}: task ${escapeHtml(task.id)} as message parent" aria-pressed="${selected ? "true" : "false"}" title="${escapeHtml(task.id)}">${highlightTaskText(task.title)}</button>
      ${formatTaskPhases(task)}
      ${attachmentsHtml}
      ${detailsHtml}
    </li>
  `;
}

export function renderTasksView(container, state) {
  taskRenderEvents?.abort();
  taskRenderEvents = new AbortController();
  const signal = taskRenderEvents.signal;
  const prevScrollTop = container.scrollTop || 0;
  const allTasks = state.tasks || [];
  const selectedParentRefs = state.replyParentRefs || [];

  const activeTasks = allTasks.filter((t) => t.status !== "finished");
  const finishedTasks = allTasks.filter((t) => t.status === "finished");

  const firstUnblocked = activeTasks.find((t) => !t.blocked) || null;
  const currentSet = new Set(firstUnblocked ? [firstUnblocked.id] : []);

  const currentTasks = activeTasks
    .filter((t) => currentSet.has(t.id))
    .filter(taskMatchesFilter);
  const upcomingTasks = activeTasks
    .filter((t) => !currentSet.has(t.id))
    .filter(taskMatchesFilter);
  const filteredFinished = finishedTasks.filter(taskMatchesFilter);
  const hasMoreFinished =
    !taskSearchQuery &&
    taskTypeFilter === "all" &&
    filteredFinished.length > visibleFinishedCount;
  const shownFinished =
    taskSearchQuery || taskTypeFilter !== "all"
      ? filteredFinished
      : filteredFinished.slice(0, visibleFinishedCount);

  const latestModified = allTasks.reduce(
    (acc, t) => (t.modified_at && t.modified_at > acc ? t.modified_at : acc),
    ""
  );
  const statusHtml = allTasks.length
    ? `Updated <time datetime="${escapeHtml(latestModified)}" data-timestamp="${escapeHtml(latestModified)}">${escapeHtml(relativeTime(latestModified))}</time>`
    : "No task list yet.";

  reconcileView(container, `
    <div class="tasks-layout">
      <div class="row tight tasks-toolbar">
        <p id="tasks-status" class="muted" role="status">${statusHtml}</p>
        <input
          id="task-search"
          type="search"
          class="input"
          value="${escapeHtml(taskSearchInput)}"
          placeholder="Search tasks"
          aria-label="Search task list"
        />
        <div class="select-wrap task-filter">
          <select id="task-type-filter" class="select" aria-label="Filter tasks by type">
            <option value="all" ${taskTypeFilter === "all" ? "selected" : ""}>All types</option>
            ${TASK_TYPES.map(
              (t) =>
                `<option value="${t}" ${taskTypeFilter === t ? "selected" : ""}>${t === "ops" ? "Ops" : t.charAt(0).toUpperCase() + t.slice(1)}</option>`
            ).join("")}
          </select>
          ${CHEVRON_DOWN_SVG}
        </div>
      </div>

      <div id="tasks-current" class="task-div" ${currentTasks.length ? "" : "hidden"}>
        <h2 id="tasks-current-title" class="task-section-title">Current</h2>
        <ul id="tasks-current-body" class="task-list">
          ${currentTasks.map((task) => renderTaskRow(task, selectedParentRefs)).join("")}
        </ul>
      </div>

      <div id="tasks-upcoming" class="task-div" ${upcomingTasks.length ? "" : "hidden"}>
        <h2 id="tasks-upcoming-title" class="task-section-title">Upcoming</h2>
        <ul id="tasks-upcoming-body" class="task-list">
          ${upcomingTasks.map((task) => renderTaskRow(task, selectedParentRefs)).join("")}
        </ul>
      </div>

      <div id="tasks-finished" class="task-div" ${shownFinished.length ? "" : "hidden"}>
        <h2 id="tasks-finished-title" class="task-section-title">Finished</h2>
        <ul id="tasks-finished-body" class="task-list">
          ${shownFinished.map((task) => renderTaskRow(task, selectedParentRefs)).join("")}
        </ul>
      </div>
    </div>
  `);

  const searchEl = container.querySelector("#task-search");
  searchEl?.addEventListener("input", () => {
    taskSearchInput = searchEl.value;
    const nextQuery = taskSearchInput.trim().toLowerCase();
    if (taskSearchQuery && !nextQuery) openTaskDetails.clear();
    taskSearchQuery = nextQuery;
    writeTaskSession(TASK_SEARCH_KEY, taskSearchInput.trim());
    renderTasksView(container, state);
  }, { signal });

  const filterEl = container.querySelector("#task-type-filter");
  filterEl?.addEventListener("change", () => {
    taskTypeFilter = filterEl.value;
    writeTaskSession(TASK_TYPE_KEY, taskTypeFilter === "all" ? "" : taskTypeFilter);
    renderTasksView(container, state);
  }, { signal });

  for (const detailsEl of container.querySelectorAll("details.task-details")) {
    detailsEl.querySelector("summary")?.addEventListener("click", () => {
      const id = detailsEl.dataset.taskId;
      if (!id) return;
      if (detailsEl.open) {
        openTaskDetails.delete(id);
      } else {
        openTaskDetails.add(id);
      }
    }, { signal });
  }

  container.scrollTop = prevScrollTop;
  container.onscroll = () => {
    if (!hasMoreFinished) return;
    if (
      container.scrollTop + container.clientHeight >=
      container.scrollHeight - 64
    ) {
      visibleFinishedCount += FINISHED_TASK_PAGE_SIZE;
      renderTasksView(container, state);
    }
  };
}
