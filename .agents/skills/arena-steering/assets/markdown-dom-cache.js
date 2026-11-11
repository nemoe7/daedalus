// Cache inert parsed Markdown, not live nodes: each mounted copy is independent.
// Live copies with the same key can be retained during view reconciliation.
const MAX_ENTRIES = 128;
const MAX_HTML_LENGTH = 32768;
const entries = new Map();
let sequence = 0;

function hashText(text) {
  let hash = 2166136261;
  for (let i = 0; i < text.length; i += 1) {
    hash = Math.imul(hash ^ text.charCodeAt(i), 16777619);
  }
  return (hash >>> 0).toString(36);
}

export function createMarkdownBatch() {
  const pending = new Map();
  let serial = 0;
  return {
    slot(html, className, variant = "") {
      html = String(html ?? "");
      if (html.length > MAX_HTML_LENGTH || typeof document.createElement !== "function") {
        return `<div class="${className}">${html}</div>`;
      }
      const input = JSON.stringify([html, className, variant]);
      const hash = hashText(input);
      let entry = entries.get(input);
      if (entry) {
        entries.delete(input);
      } else {
        const template = document.createElement("template");
        template.innerHTML = html;
        entry = { key: `${hash}-${++sequence}`, content: template.content };
      }
      entries.set(input, entry);
      while (entries.size > MAX_ENTRIES) entries.delete(entries.keys().next().value);
      const token = String(++serial);
      pending.set(token, entry);
      return `<div class="${className}" data-markdown-key="${entry.key}" data-markdown-slot="${token}"></div>`;
    },
    mount(root) {
      if (!pending.size) return;
      for (const node of root.querySelectorAll("[data-markdown-slot]")) {
        const entry = pending.get(node.dataset.markdownSlot);
        if (entry) node.replaceChildren(entry.content.cloneNode(true));
        node.removeAttribute("data-markdown-slot");
      }
      pending.clear();
    },
  };
}
