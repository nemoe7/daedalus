import { escapeHtml } from "./markdown.js";

// Reconcile rendered view markup without detaching unchanged controls or cards.
// Reparenting a focused input drops focus in Chromium, even if it is reinserted.
const KEY_ATTRIBUTES = ["id", "data-msg-id", "data-task-id", "data-report-id", "data-field-id", "data-toc-target"];

function nodeKey(node) {
  if (node.nodeType !== Node.ELEMENT_NODE) return null;
  for (const name of KEY_ATTRIBUTES) {
    const value = node.getAttribute(name);
    if (value) return `${name}:${value}`;
  }
  if (node.localName === "input" && ["checkbox", "radio"].includes(node.type)) {
    return `input:${node.name}:${node.value}`;
  }
  return null;
}

function compatible(current, next) {
  if (current.nodeType !== next.nodeType) return false;
  if (current.nodeType !== Node.ELEMENT_NODE) return true;
  if (current.localName !== next.localName) return false;
  const oldKey = nodeKey(current);
  const newKey = nodeKey(next);
  if (oldKey || newKey) return oldKey === newKey;
  return current.localName !== "input" ||
    (current.type === next.type && current.name === next.name);
}

function updateNode(current, next) {
  if (current.nodeType !== Node.ELEMENT_NODE) {
    if (current.nodeValue !== next.nodeValue) current.nodeValue = next.nodeValue;
    return;
  }
  const previousMarkdownKey = current.getAttribute("data-markdown-key");
  const nextMarkdownKey = next.getAttribute("data-markdown-key");
  const sameMarkdown = previousMarkdownKey && previousMarkdownKey === nextMarkdownKey;
  const textControl = current instanceof HTMLTextAreaElement ||
    (current instanceof HTMLInputElement && !["checkbox", "radio", "file"].includes(current.type));
  const oldValue = textControl ? current.value : null;
  const wasDirty = textControl && current.value !== current.defaultValue;
  const checkControl = current instanceof HTMLInputElement &&
    ["checkbox", "radio"].includes(current.type);
  const oldChecked = checkControl ? current.checked : null;
  const checkedWasDirty = checkControl && current.checked !== current.defaultChecked;

  for (const attribute of Array.from(current.attributes)) {
    // A transient highlight outlives the render that follows a reference jump.
    if (attribute.name === "data-highlight") continue;
    if (!next.hasAttribute(attribute.name)) current.removeAttribute(attribute.name);
  }
  for (const attribute of next.attributes) {
    if (current.getAttribute(attribute.name) !== attribute.value) {
      current.setAttribute(attribute.name, attribute.value);
    }
  }
  if (!sameMarkdown) {
    if (previousMarkdownKey || nextMarkdownKey) current.replaceChildren(...next.childNodes);
    else updateChildren(current, next);
  }
  if (textControl) current.value = wasDirty ? oldValue : next.value;
  if (checkControl) current.checked = checkedWasDirty ? oldChecked : next.checked;
}

function updateChildren(current, next) {
  let cursor = current.firstChild;
  for (const child of Array.from(next.childNodes)) {
    if (cursor && compatible(cursor, child)) {
      updateNode(cursor, child);
      cursor = cursor.nextSibling;
      continue;
    }
    const key = nodeKey(child);
    let match = null;
    if (key) {
      for (let sibling = cursor?.nextSibling; sibling; sibling = sibling.nextSibling) {
        if (compatible(sibling, child)) {
          match = sibling;
          break;
        }
      }
    }
    if (match) {
      current.insertBefore(match, cursor);
      updateNode(match, child);
    } else {
      current.insertBefore(child, cursor);
    }
  }
  while (cursor) {
    const nextSibling = cursor.nextSibling;
    cursor.remove();
    cursor = nextSibling;
  }
}

export function reconcileView(container, markup, prepare = null) {
  const doc = container.ownerDocument;
  if (!doc) {
    container.innerHTML = markup;
    return;
  }
  const template = doc.createElement("template");
  template.innerHTML = markup;
  prepare?.(template.content);
  updateChildren(container, template.content);
}

export function reconcileElement(current, next) {
  if (!compatible(current, next)) {
    current.replaceWith(next);
    return;
  }
  updateNode(current, next);
}

export function renderParentChip(type, id) {
  const label = escapeHtml(`${type} ${id}`);
  return `<button type="button" class="reply-parent-chip" data-reference-type="${escapeHtml(type)}" data-reference-id="${escapeHtml(id)}" aria-label="Open ${label}"><code>${escapeHtml(id)}</code></button>`;
}

export function renderAnswerBlock(content, { pending = false, first = true } = {}) {
  const text = pending
    ? '<div class="answer reply" role="status">Sending answer…</div>'
    : content;
  return `<div class="answer reply message-text${first ? "" : " reply-block"}">${text}</div>`;
}
