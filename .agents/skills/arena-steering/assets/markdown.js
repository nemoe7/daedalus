// Safe offline Markdown renderer for messages, acks, and report blocks.

import { BRACES_14_SVG as BRACES_SVG, COPY_14_SVG as COPY_SVG } from "./icons.js";

let taskLabelSeq = 0;

const CODE_LANG_NAMES = {
  bash: "Bash",
  c: "C",
  cjs: "JavaScript",
  cpp: "C++",
  cs: "C#",
  css: "CSS",
  diff: "Diff",
  go: "Go",
  html: "HTML",
  java: "Java",
  javascript: "JavaScript",
  js: "JavaScript",
  json: "JSON",
  jsx: "JSX",
  kt: "Kotlin",
  markdown: "Markdown",
  md: "Markdown",
  mermaid: "Mermaid",
  mjs: "JavaScript",
  php: "PHP",
  plaintext: "Text",
  py: "Python",
  python: "Python",
  rb: "Ruby",
  rs: "Rust",
  rust: "Rust",
  sh: "Shell",
  shell: "Shell",
  sql: "SQL",
  svg: "SVG",
  swift: "Swift",
  text: "Text",
  toml: "TOML",
  ts: "TypeScript",
  tsx: "TSX",
  txt: "Text",
  typescript: "TypeScript",
  xml: "XML",
  yaml: "YAML",
  yml: "YAML",
  zsh: "Zsh",
};

export function escapeHtml(text) {
  return String(text ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

export function slugifyHeading(text) {
  return (
    "sec-" +
    String(text || "")
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "")
  );
}

function formatCodeLang(raw) {
  const key = String(raw || "").trim().toLowerCase();
  if (!key) return "Code";
  return CODE_LANG_NAMES[key] || key.charAt(0).toUpperCase() + key.slice(1);
}

function highlightDiffLines(lines) {
  return lines
    .map((line) => {
      const escaped = escapeHtml(line);
      if (
        line.startsWith("diff ") ||
        line.startsWith("index ") ||
        line.startsWith("--- ") ||
        line.startsWith("+++ ")
      ) {
        return `<span class="token comment">${escaped}</span>`;
      }
      if (line.startsWith("@@")) {
        return `<span class="token coord">${escaped}</span>`;
      }
      if (line.startsWith("+")) {
        return `<span class="token inserted">${escaped}</span>`;
      }
      if (line.startsWith("-")) {
        return `<span class="token deleted">${escaped}</span>`;
      }
      return escaped;
    })
    .join("\n");
}

function renderCodeFence(lang, codeLines) {
  const rawCode = codeLines.join("\n");
  const escapedCode = escapeHtml(rawCode);
  const dataCode = escapedCode.replace(/\n/g, "&#10;");
  const langKey = String(lang || "").trim().toLowerCase();
  const langLabel = escapeHtml(formatCodeLang(langKey));
  const langClass = langKey ? ` class="language-${escapeHtml(langKey)}"` : "";
  const prismGrammar =
    langKey &&
    langKey !== "mermaid" &&
    langKey !== "diff" &&
    langKey !== "patch"
      ? globalThis.Prism?.languages?.[langKey]
      : null;
  const innerHtml =
    langKey === "diff" || langKey === "patch"
      ? highlightDiffLines(codeLines)
      : prismGrammar && typeof globalThis.Prism?.highlight === "function"
        ? globalThis.Prism.highlight(rawCode, prismGrammar, langKey)
        : escapedCode;
  const highlightedAttr = prismGrammar ? ` data-highlighted="true"` : "";
  const mermaidBtn =
    langKey === "mermaid"
      ? `<button type="button" class="preview-code-mermaid quiet-btn" aria-label="Show Mermaid code" title="Show Mermaid code" aria-pressed="false" data-code="${dataCode}">${BRACES_SVG}</button>`
      : "";
  const mermaidStateAttr =
    langKey === "mermaid" ? ` data-mermaid-state="pending"` : "";
  return `
    <div class="code-block"${mermaidStateAttr}>
      ${mermaidBtn}
      <button type="button" class="copy-code quiet-btn" aria-label="Copy code" data-code="${dataCode}">${COPY_SVG}</button>
      <div class="code-block-header">
        <span class="code-block-lang">${BRACES_SVG}<span>${langLabel}</span></span>
      </div>
      <pre><code${langClass}${highlightedAttr}>${innerHtml}</code></pre>
    </div>
  `;
}

export function highlightCodeBlocks(rootEl = document) {
  if (!rootEl?.querySelectorAll) return;
  const prism = globalThis.Prism;
  if (!prism?.languages || typeof prism.highlight !== "function") return;
  const codeNodes = rootEl.querySelectorAll(".code-block > pre > code[class*='language-']");
  for (const codeEl of codeNodes) {
    if (codeEl.dataset.highlighted === "true") continue;
    const match = (codeEl.className || "").match(/\blanguage-([\w+-]+)/);
    const langKey = match ? match[1].toLowerCase() : "";
    if (!langKey || langKey === "mermaid" || langKey === "diff" || langKey === "patch") {
      continue;
    }
    const grammar = prism.languages[langKey];
    if (!grammar) continue;
    const rawCode = codeEl.textContent || "";
    codeEl.innerHTML = prism.highlight(rawCode, grammar, langKey);
    codeEl.dataset.highlighted = "true";
  }
}

const MERMAID_SVG_ELEMENTS = new Set([
  "svg", "g", "defs", "marker", "path", "rect", "circle", "ellipse", "line",
  "polyline", "polygon", "text", "tspan", "style", "title", "desc", "clipPath",
  "mask", "pattern", "linearGradient", "radialGradient", "stop", "use", "filter",
  "feDropShadow", "symbol",
]);

export function sanitizeMermaidSvg(svgText) {
  if (typeof svgText !== "string") return null;
  const trimmed = svgText.trim();
  if (!/^<svg\b[\s\S]*<\/svg>$/i.test(trimmed) || trimmed.includes("<!")) return null;
  if (
    /<(script|foreignobject|iframe|object|embed|audio|video|canvas|applet|meta|link)\b/i.test(
      trimmed
    )
  ) {
    return null;
  }
  if (
    /\bon[a-z]+\s*=|javascript:|vbscript:|data:text\/html|@import|\burl\s*\(\s*["']?\s*(?!#)/i.test(
      trimmed
    )
  ) {
    return null;
  }
  if (/\b(?:xlink:)?href\s*=\s*["']?\s*(?!#)/i.test(trimmed)) {
    return null;
  }
  if (typeof DOMParser === "undefined") return null;
  const parsed = new DOMParser().parseFromString(trimmed, "image/svg+xml");
  if (parsed.documentElement.localName !== "svg" || parsed.querySelector("parsererror")) return null;
  const rootId = parsed.documentElement.id;
  if (!/^mermaid-steering-\d+$/.test(rootId)) return null;
  for (const element of parsed.querySelectorAll("*")) {
    if (element.namespaceURI !== "http://www.w3.org/2000/svg" || !MERMAID_SVG_ELEMENTS.has(element.localName)) return null;
    const values = [...element.attributes].map((attribute) => {
      const name = attribute.localName.toLowerCase();
      if (name.startsWith("on") || (name === "href" && !attribute.value.startsWith("#"))) return null;
      return attribute.value;
    });
    if (values.includes(null)) return null;
    if (element.localName === "style") {
      const css = element.textContent || "";
      values.push(css);
      try {
        const sheet = new CSSStyleSheet();
        sheet.replaceSync(css);
        for (const rule of sheet.cssRules) {
          if (rule.type === CSSRule.KEYFRAMES_RULE && ["dash", "edge-animation-frame"].includes(rule.name)) continue;
          if (rule.type !== CSSRule.STYLE_RULE) return null;
          const scope = `#${rootId}`;
          if (rule.selectorText.split(",").some((selector) => {
            const value = selector.trim();
            return (value !== scope && !value.startsWith(`${scope} `)) || /[+~]/.test(value);
          })) return null;
        }
      } catch {
        return null;
      }
    }
    if (values.some((value) => /\\|javascript:|vbscript:|data:text\/html|@import|expression\s*\(|url\s*\(\s*["']?\s*(?!#)/i.test(value))) return null;
  }
  return trimmed.replace(/^<svg\b[^>]*>/i, (opening) => {
    if (/\bclass\s*=\s*["'][^"']*\bmermaid-svg\b/i.test(opening)) return opening;
    if (/\bclass\s*=\s*["']/i.test(opening)) {
      return opening.replace(/\bclass\s*=\s*(["'])/i, 'class=$1mermaid-svg ');
    }
    return opening.replace(/^<svg\b/i, '<svg class="mermaid-svg"');
  });
}

let mermaidSeq = 0;
let mermaidInitialized = false;
const mermaidSvgCache = new Map();
const mermaidPending = new WeakSet();
const MERMAID_CACHE_PREFIX = "steer:mermaid:v2:";
const MERMAID_BROWSER_CACHE_LIMIT = 16;

function rememberMermaidSvg(source, svg) {
  mermaidSvgCache.delete(source);
  mermaidSvgCache.set(source, svg);
  if (mermaidSvgCache.size > MERMAID_BROWSER_CACHE_LIMIT) {
    mermaidSvgCache.delete(mermaidSvgCache.keys().next().value);
  }
}

async function mermaidCacheKey(source) {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(MERMAID_CACHE_PREFIX + source));
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
}

function readMermaidBrowserCache(key) {
  try {
    return sanitizeMermaidSvg(localStorage.getItem(MERMAID_CACHE_PREFIX + key));
  } catch {
    return null;
  }
}

function writeMermaidBrowserCache(key, svg) {
  try {
    const keys = Object.keys(localStorage).filter((item) => item.startsWith(MERMAID_CACHE_PREFIX));
    while (keys.length >= MERMAID_BROWSER_CACHE_LIMIT) localStorage.removeItem(keys.shift());
    localStorage.setItem(MERMAID_CACHE_PREFIX + key, svg);
  } catch {
    // Cache storage is optional.
  }
}

async function readMermaidServerCache(key) {
  try {
    const response = await fetch(`/mermaid-cache/${key}`);
    if (!response.ok) return null;
    return sanitizeMermaidSvg((await response.json()).svg);
  } catch {
    return null;
  }
}

function writeMermaidServerCache(source, svg) {
  fetch("/mermaid-cache", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ source: MERMAID_CACHE_PREFIX + source, svg }),
  }).catch(() => {});
}

export function initializeMermaid() {
  const lib = globalThis.mermaid;
  if (!lib?.initialize) return null;
  if (!mermaidInitialized) {
    lib.initialize({
      startOnLoad: false,
      securityLevel: "strict",
      htmlLabels: false,
      maxTextSize: 50000,
      maxEdges: 500,
      suppressErrorRendering: true,
      theme: "base",
      themeVariables: {
        darkMode: true,
        background: "#252523",
        fontFamily:
          '"Basel Grotesk", -apple-system, BlinkMacSystemFont, sans-serif',
        fontSize: "13px",
        primaryColor: "#2c2b28",
        primaryTextColor: "#f4f0eb",
        primaryBorderColor: "#e5bb8b",
        secondaryColor: "#292624",
        secondaryTextColor: "#f4f0eb",
        secondaryBorderColor: "#524c47",
        tertiaryColor: "#1b1a19",
        tertiaryTextColor: "#c7c2bc",
        tertiaryBorderColor: "#413d39",
        lineColor: "#c7c2bc",
        textColor: "#f4f0eb",
        mainBkg: "#2c2b28",
        secondBkg: "#292624",
        nodeBorder: "#e5bb8b",
        clusterBkg: "#1b1a19",
        clusterBorder: "#413d39",
        defaultLinkColor: "#c7c2bc",
        titleColor: "#f4f0eb",
        edgeLabelBackground: "#252523",
        actorBkg: "#2c2b28",
        actorBorder: "#e5bb8b",
        actorTextColor: "#f4f0eb",
        actorLineColor: "#c7c2bc",
        signalColor: "#f4f0eb",
        signalTextColor: "#f4f0eb",
        labelBoxBkgColor: "#292624",
        labelBoxBorderColor: "#524c47",
        labelTextColor: "#f4f0eb",
        loopTextColor: "#c7c2bc",
        noteBkgColor: "#292624",
        noteTextColor: "#f4f0eb",
        noteBorderColor: "#e5bb8b",
        dropShadow: "none",
      },
      flowchart: { htmlLabels: false },
    });
    mermaidInitialized = true;
  }
  return lib;
}

function mountMermaidDiagram(block, codeEl, rawSource, safeSvg) {
  if (!block.querySelector(".preview-code-mermaid")) {
    const dataCode = escapeHtml(rawSource).replace(/\n/g, "&#10;");
    block.insertAdjacentHTML(
      "afterbegin",
      `<button type="button" class="preview-code-mermaid quiet-btn" aria-label="Show Mermaid code" title="Show Mermaid code" aria-pressed="false" data-code="${dataCode}">${BRACES_SVG}</button>`
    );
  }
  let diagramDiv = block.querySelector(":scope > .mermaid-diagram");
  if (!diagramDiv) {
    diagramDiv = document.createElement("div");
    diagramDiv.className = "mermaid-diagram";
    diagramDiv.setAttribute("role", "img");
    diagramDiv.setAttribute("aria-label", "Mermaid diagram");
    const preEl = codeEl.closest("pre");
    block.insertBefore(diagramDiv, preEl);
  }
  diagramDiv.innerHTML = safeSvg;
  // Cached diagrams can have the same Mermaid IDs, even when their sources differ.
  // Give each mounted copy its own IDs and local references.
  const svg = diagramDiv.querySelector("svg");
  const instance = ++mermaidSeq;
  const ids = new Map();
  for (const element of [svg, ...svg.querySelectorAll("[id]")]) {
    if (!element.id) continue;
    ids.set(element.id, element === svg ? `mermaid-steering-${instance}` : `mermaid-instance-${instance}-${element.id}`);
  }
  const rewriteRefs = (value) => value.replace(/#([\w-]+)/g, (match, id) => ids.has(id) ? `#${ids.get(id)}` : match);
  for (const element of [svg, ...svg.querySelectorAll("*")]) {
    for (const attribute of [...element.attributes]) {
      if (attribute.name === "id" && ids.has(attribute.value)) element.setAttribute("id", ids.get(attribute.value));
      else element.setAttribute(attribute.name, rewriteRefs(attribute.value));
    }
    if (element.localName === "style") element.textContent = rewriteRefs(element.textContent || "");
  }
  block.dataset.mermaidState = "rendered";
}

export async function hydrateMermaidBlocks(rootEl = document) {
  if (!rootEl?.querySelectorAll) return;
  const codeNodes = rootEl.querySelectorAll(
    ".code-block > pre > code.language-mermaid"
  );
  if (!codeNodes.length) return;
  const lib = initializeMermaid();
  for (const codeEl of codeNodes) {
    const block = codeEl.closest(".code-block");
    if (
      !block || mermaidPending.has(block) ||
      (block.dataset.mermaidState &&
        block.dataset.mermaidState !== "pending")
    ) {
      continue;
    }
    mermaidPending.add(block);
    try {
      const rawSource = (codeEl.textContent || "").trim();
      if (!rawSource) {
        block.dataset.mermaidState = "fallback";
        block.querySelector(".preview-code-mermaid")?.remove();
        continue;
      }
      let key = null;
      try {
        key = await mermaidCacheKey(rawSource);
      } catch {
        // Rendering still works without a cache key.
      }
      const cachedSvg = mermaidSvgCache.get(rawSource) ||
        (key && (readMermaidBrowserCache(key) || await readMermaidServerCache(key)));
      if (cachedSvg) {
        rememberMermaidSvg(rawSource, cachedSvg);
        if (key) writeMermaidBrowserCache(key, cachedSvg);
        if (block.isConnected) mountMermaidDiagram(block, codeEl, rawSource, cachedSvg);
        continue;
      }
      if (!lib?.render) {
        block.dataset.mermaidState = "fallback";
        block.querySelector(".preview-code-mermaid")?.remove();
        continue;
      }
      try {
        mermaidSeq += 1;
        const rendered = await lib.render(
          `mermaid-steering-${mermaidSeq}`,
          rawSource
        );
        const svgRaw = typeof rendered === "string" ? rendered : rendered?.svg;
        const safeSvg = sanitizeMermaidSvg(svgRaw);
        if (!safeSvg) {
          block.dataset.mermaidState = "fallback";
          block.querySelector(".preview-code-mermaid")?.remove();
          continue;
        }
        rememberMermaidSvg(rawSource, safeSvg);
        if (key) {
          writeMermaidBrowserCache(key, safeSvg);
          writeMermaidServerCache(rawSource, safeSvg);
        }
        if (!block.isConnected) continue;
        mountMermaidDiagram(block, codeEl, rawSource, safeSvg);
      } catch {
        block.dataset.mermaidState = "fallback";
        block.querySelector(".preview-code-mermaid")?.remove();
      }
    } finally {
      mermaidPending.delete(block);
    }
  }
}

export function toggleCodeMermaid(button) {
  const block = button?.closest?.(".code-block");
  if (
    !block ||
    !block.dataset.mermaidState ||
    block.dataset.mermaidState === "fallback" ||
    block.dataset.mermaidState === "pending"
  ) {
    return;
  }
  const showingCode = block.dataset.mermaidState === "code";
  if (showingCode) {
    block.dataset.mermaidState = "rendered";
    button.setAttribute("aria-pressed", "false");
    button.setAttribute("aria-label", "Show Mermaid code");
    button.title = "Show Mermaid code";
  } else {
    block.dataset.mermaidState = "code";
    button.setAttribute("aria-pressed", "true");
    button.setAttribute("aria-label", "Show Mermaid diagram");
    button.title = "Show Mermaid diagram";
  }
}

export function renderInlineCode(text) {
  return escapeHtml(text).replace(/(`+)([^`\n]+?)\1/g, (_m, ticks, inner) => {
    const trimmed =
      ticks.length > 1 &&
      inner.startsWith(" ") &&
      inner.endsWith(" ") &&
      inner.trim().length > 0
        ? inner.slice(1, -1)
        : inner;
    return `<code>${trimmed}</code>`;
  });
}

export function renderInlineMarkdown(text) {
  let out = renderInlineCode(text);
  out = out.replace(
    /!\[([^\]]*)\]\(((?:https?:\/\/|\/|data:image\/)[^\s)]+)\)/g,
    (_m, alt, src) => `<img src="${src}" alt="${alt}" loading="lazy">`
  );
  out = out.replace(
    /\[([^\]]+)\]\(((?:https?:\/\/|\/|#)[^\s)]+)\)/g,
    (_m, label, url) => {
      const ext =
        url.startsWith("#") || url.startsWith("/")
          ? ""
          : ' target="_blank" rel="noopener noreferrer"';
      return `<a href="${url}"${ext}>${label}</a>`;
    }
  );
  out = out.replace(
    /(^|[\s(])(https?:\/\/[^\s<>()]+?)(?=[.,;:!?]?(?:[\s)]|$))/g,
    '$1<a href="$2" target="_blank" rel="noopener noreferrer">$2</a>'
  );
  out = out.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  out = out.replace(/\*([^*]+)\*/g, "<em>$1</em>");
  out = out.replace(/~~([^~]+)~~/g, "<del>$1</del>");
  out = out.replace(/==([^=]+)==/g, "<mark>$1</mark>");
  return out;
}

export function renderMarkdownBlock(markdown, { headings = null } = {}) {
  const lines = String(markdown ?? "").split(/\r?\n/);
  const html = [];
  let inCode = false;
  let codeLang = "";
  let codeLines = [];
  let inList = false;
  let tableRows = [];
  let listItems = [];
  let paragraphLines = [];

  const closeParagraph = () => {
    if (!paragraphLines.length) return;
    html.push(`<p>${renderInlineMarkdown(paragraphLines.join("\n"))}</p>`);
    paragraphLines = [];
  };

  const renderList = () => {
    if (!listItems.length) return "";
    const out = [];
    const depths = [];
    for (const item of listItems) {
      while (depths.length && item.indent < depths[depths.length - 1]) {
        out.push("</li></ul>");
        depths.pop();
      }
      if (!depths.length || item.indent > depths[depths.length - 1]) {
        out.push("<ul>");
        depths.push(item.indent);
      } else {
        out.push("</li>");
      }
      out.push(`<li${item.task ? ' class="task-list-item"' : ""}>${item.html}`);
    }
    while (depths.length) {
      out.push("</li></ul>");
      depths.pop();
    }
    return out.join("");
  };

  const closeList = () => {
    if (inList) {
      html.push(renderList());
      listItems = [];
      inList = false;
    }
  };

  const closeTable = () => {
    if (!tableRows.length) return;
    const headerCells = tableRows[0];
    const bodyRows = tableRows.slice(
      tableRows.length > 1 &&
        tableRows[1].every((c) => /^:?-{2,}:?$/.test(c.trim()))
        ? 2
        : 1
    );
    const thead = `<thead><tr>${headerCells
      .map((c) => `<th>${renderInlineMarkdown(c.trim())}</th>`)
      .join("")}</tr></thead>`;
    const tbody = bodyRows.length
      ? `<tbody>${bodyRows
          .map(
            (r) =>
              `<tr>${r
                .map((c) => `<td>${renderInlineMarkdown(c.trim())}</td>`)
                .join("")}</tr>`
          )
          .join("")}</tbody>`
      : "";
    html.push(`<table>${thead}${tbody}</table>`);
    tableRows = [];
  };

  for (const line of lines) {
    const fenceMatch = line.trim().match(/^```([\w+-]*)\s*$/);
    if (fenceMatch || (inCode && line.trim().startsWith("```"))) {
      closeParagraph();
      closeList();
      closeTable();
      if (inCode) {
        html.push(renderCodeFence(codeLang, codeLines));
        codeLines = [];
        codeLang = "";
        inCode = false;
      } else {
        inCode = true;
        codeLang = fenceMatch ? fenceMatch[1] : "";
      }
      continue;
    }
    if (inCode) {
      codeLines.push(line);
      continue;
    }
    const trimmed = line.trim();
    if (!trimmed) {
      closeParagraph();
      closeList();
      closeTable();
      continue;
    }
    if (trimmed.startsWith("|") && trimmed.endsWith("|")) {
      closeParagraph();
      closeList();
      const cells = trimmed.slice(1, -1).split("|");
      tableRows.push(cells);
      continue;
    }
    closeTable();
    const heading = trimmed.match(/^(#{1,4})\s+(.+)$/);
    if (heading) {
      closeParagraph();
      closeList();
      const level = heading[1].length;
      const rawTitle = heading[2].trim();
      const id = slugifyHeading(rawTitle);
      if (Array.isArray(headings)) {
        headings.push({ id, level, text: rawTitle });
      }
      html.push(
        `<h${level} id="${escapeHtml(id)}">${renderInlineMarkdown(rawTitle)}</h${level}>`
      );
      continue;
    }
    const quote = trimmed.match(/^>\s?(.*)$/);
    if (quote) {
      closeParagraph();
      closeList();
      html.push(
        `<blockquote><p>${renderInlineMarkdown(quote[1])}</p></blockquote>`
      );
      continue;
    }
    const bullet = trimmed.match(/^[-*]\s+(.+)$/);
    if (bullet) {
      closeParagraph();
      inList = true;
      const indent = line.length - line.trimStart().length;
      const taskMatch = bullet[1].match(/^\[([ xX])\]\s+(.+)$/);
      if (taskMatch) {
        const labelId = `task-label-${++taskLabelSeq}`;
        const checkedAttr =
          taskMatch[1].toLowerCase() === "x" ? " checked" : "";
        listItems.push({
          indent,
          task: true,
          html: `<input type="checkbox" class="task-list-item-checkbox" disabled${checkedAttr} aria-labelledby="${labelId}"><span id="${labelId}">${renderInlineMarkdown(taskMatch[2])}</span>`,
        });
      } else {
        listItems.push({
          indent,
          task: false,
          html: renderInlineMarkdown(bullet[1]),
        });
      }
      continue;
    }
    if (/^ {0,3}([-*_])(?:\s*\1){2,}\s*$/.test(line)) {
      closeParagraph();
      closeList();
      closeTable();
      html.push("<hr>");
      continue;
    }
    closeList();
    paragraphLines.push(trimmed);
  }
  closeParagraph();
  closeList();
  closeTable();
  if (inCode) {
    html.push(renderCodeFence(codeLang, codeLines));
  }
  return html.join("\n");
}
