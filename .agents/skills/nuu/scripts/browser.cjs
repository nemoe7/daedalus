#!/usr/bin/env node
'use strict';

const fs = require('node:fs');
const path = require('node:path');
const VERSION = '0.1.4';
const request = JSON.parse(fs.readFileSync(0, 'utf8'));

function emit(value) {
  process.stdout.write(`${JSON.stringify({version: VERSION, ...value})}\n`);
}

function url(value) {
  const parsed = new URL(value);
  if (!['http:', 'https:'].includes(parsed.protocol) || parsed.username || parsed.password) {
    throw new Error('Page URL must use HTTP(S) without credentials');
  }
  return parsed.href;
}

function dependency() {
  try {
    return require(require.resolve('playwright', {paths: [__dirname]}));
  } catch (error) {
    if (error.code !== 'MODULE_NOT_FOUND') throw error;
    return null;
  }
}

async function overlaps(page, selector) {
  return page.evaluate(selector => {
    const MAX_ELEMENTS = 100, MAX_PAIRS = 50, MAX_ANCESTORS = 12;
    function selectorFor(element) {
      if (element.id) return `#${CSS.escape(element.id)}`;
      const parent = element.parentElement;
      if (!parent) return element.tagName.toLowerCase();
      const siblings = [...parent.children].filter(node => node.tagName === element.tagName);
      return `${selectorFor(parent)} > ${element.tagName.toLowerCase()}:nth-of-type(${siblings.indexOf(element) + 1})`;
    }
    function stacking(element) {
      const chain = [];
      let current = element, truncated = false;
      while (current && current.nodeType === 1) {
        if (chain.length >= MAX_ANCESTORS) { truncated = true; break; }
        const style = getComputedStyle(current);
        chain.push({selector: selectorFor(current), position: style.position, zIndex: style.zIndex,
          opacity: style.opacity, transform: style.transform, isolation: style.isolation,
          mixBlendMode: style.mixBlendMode, filter: style.filter, backdropFilter: style.backdropFilter,
          contain: style.contain, containerType: style.containerType, clipPath: style.clipPath,
          willChange: style.willChange, pointerEvents: style.pointerEvents});
        current = current.parentElement;
      }
      return {ancestors: chain, truncated};
    }
    const matched = [...document.querySelectorAll(selector)];
    const candidates = [];
    for (const element of matched) {
      const style = getComputedStyle(element);
      if (style.display === 'none' || style.visibility === 'hidden') continue;
      const box = element.getBoundingClientRect();
      if (box.width > 0 && box.height > 0) candidates.push({element, selector: selectorFor(element), box});
    }
    const elements = candidates.slice(0, MAX_ELEMENTS);
    const pairs = [];
    let pairsTruncated = false;
    for (let i = 0; i < elements.length; i++) {
      for (let j = i + 1; j < elements.length; j++) {
        const first = elements[i], second = elements[j];
        const x = Math.max(first.box.x, second.box.x), y = Math.max(first.box.y, second.box.y);
        const right = Math.min(first.box.x + first.box.width, second.box.x + second.box.width);
        const bottom = Math.min(first.box.y + first.box.height, second.box.y + second.box.height);
        if (right <= x || bottom <= y) continue;
        if (pairs.length >= MAX_PAIRS) { pairsTruncated = true; break; }
        const width = right - x, height = bottom - y;
        const relation = first.element.contains(second.element) ? 'ancestor'
          : second.element.contains(first.element) ? 'descendant' : 'separate';
        const points = [[x + width / 2, y + height / 2], [x + width * 0.25, y + height * 0.25],
          [x + width * 0.75, y + height * 0.25], [x + width * 0.25, y + height * 0.75],
          [x + width * 0.75, y + height * 0.75]];
        const samples = points.map(([px, py]) => {
          const inside = px >= 0 && py >= 0 && px < innerWidth && py < innerHeight;
          const hit = inside && typeof document.elementsFromPoint === 'function'
            ? document.elementsFromPoint(px, py) : [];
          const order = [];
          for (const candidate of [first, second]) {
            const index = hit.findIndex(node => candidate.element === node || candidate.element.contains(node));
            if (index >= 0) order.push({selector: candidate.selector, index});
          }
          order.sort((a, b) => a.index - b.index);
          return {x: px, y: py, inside_viewport: inside,
            topmost: hit[0] ? selectorFor(hit[0]) : null,
            hit_order: order.map(row => row.selector),
            stack: hit.slice(0, 8).map(selectorFor)};
        });
        const firstWins = samples.some(sample => sample.hit_order[0] === first.selector && sample.hit_order.includes(second.selector));
        const secondWins = samples.some(sample => sample.hit_order[0] === second.selector && sample.hit_order.includes(first.selector));
        const topmost = firstWins && !secondWins ? first.selector : secondWins && !firstWins ? second.selector
          : firstWins && secondWins ? 'mixed' : 'unconfirmed';
        const firstStack = stacking(first.element), secondStack = stacking(second.element);
        pairs.push({first: first.selector, second: second.selector, relation,
          intersection: {x, y, width, height, area: width * height},
          topmost, samples,
          stacking: {[first.selector]: firstStack.ancestors, [second.selector]: secondStack.ancestors},
          ancestors_truncated: firstStack.truncated || secondStack.truncated});
      }
      if (pairsTruncated) break;
    }
    return {status: 'checked', elements_considered: elements.length, elements_total: candidates.length,
      pairs, truncated: {elements: candidates.length > MAX_ELEMENTS, pairs: pairsTruncated},
      limits: {max_elements: MAX_ELEMENTS, max_pairs: MAX_PAIRS, max_ancestors: MAX_ANCESTORS, sample_points: 5,
        z_distance: 'not_computed; CSS z-index is scoped to stacking contexts'}};
  }, selector);
}

async function metrics(page, selector) {
  return page.evaluate(selector => {
    const properties = ['height', 'width', 'padding-top', 'padding-right', 'padding-bottom',
      'padding-left', 'margin-top', 'margin-bottom', 'gap', 'font-size', 'font-weight',
      'line-height', 'border-radius', 'color', 'background-color', 'outline-style',
      'outline-width', 'transition-duration', 'animation-duration'];
    function selectorFor(element) {
      if (element.id) return `#${CSS.escape(element.id)}`;
      const parent = element.parentElement;
      if (!parent) return element.tagName.toLowerCase();
      const siblings = [...parent.children].filter(node => node.tagName === element.tagName);
      return `${selectorFor(parent)} > ${element.tagName.toLowerCase()}:nth-of-type(${siblings.indexOf(element) + 1})`;
    }
    const elements = [...document.querySelectorAll(selector)].map(element => {
      const style = getComputedStyle(element);
      const box = element.getBoundingClientRect();
      const intervals = [];
      if (typeof document.createRange === 'function' && typeof document.createTreeWalker === 'function') {
        const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT);
        let node;
        while ((node = walker.nextNode())) {
          if (!node.nodeValue || !node.nodeValue.trim()) continue;
          const range = document.createRange();
          range.selectNodeContents(node);
          for (const rect of range.getClientRects()) {
            if (rect.width > 0 && rect.height > 0) {
              intervals.push([Math.max(box.x, rect.x), Math.min(box.x + box.width, rect.x + rect.width)]);
            }
          }
        }
      }
      for (const child of element.querySelectorAll('button, input, select, textarea, img, svg, progress, [role="button"]')) {
        const rect = child.getBoundingClientRect();
        if (rect.width > 0 && rect.height > 0) {
          intervals.push([Math.max(box.x, rect.x), Math.min(box.x + box.width, rect.x + rect.width)]);
        }
      }
      intervals.sort((a, b) => a[0] - b[0]);
      let inkWidth = 0;
      let curStart = null, curEnd = null;
      for (const [start, end] of intervals) {
        if (end <= start) continue;
        if (curStart === null) { curStart = start; curEnd = end; }
        else if (start <= curEnd) { curEnd = Math.max(curEnd, end); }
        else { inkWidth += curEnd - curStart; curStart = start; curEnd = end; }
      }
      if (curStart !== null) inkWidth += curEnd - curStart;
      const hasInkContent = intervals.length > 0;
      const inkRatio = box.width > 0 && hasInkContent ? Number((inkWidth / box.width).toFixed(4)) : null;
      const childBoxes = [...(element.children || [])].map(child => {
        const cs = getComputedStyle(child);
        if (cs.display === 'none' || cs.visibility === 'hidden') return null;
        const r = child.getBoundingClientRect();
        return r.width > 0 && r.height > 0 ? r : null;
      }).filter(Boolean).sort((a, b) => a.x - b.x);
      let maxSiblingGap = 0;
      for (let i = 1; i < childBoxes.length; i++) {
        const prev = childBoxes[i - 1], curr = childBoxes[i];
        const verticalOverlap = Math.min(prev.y + prev.height, curr.y + curr.height) - Math.max(prev.y, curr.y);
        if (verticalOverlap > 0) {
          const gap = curr.x - (prev.x + prev.width);
          if (gap > maxSiblingGap) maxSiblingGap = Number(gap.toFixed(2));
        }
      }
      const tag = element.tagName.toLowerCase();
      const role = element.getAttribute('role');
      const inputType = tag === 'input' ? (element.getAttribute('type') || 'text').toLowerCase() : null;
      let rowSlack = 0;
      if ((tag === 'input' || tag === 'select') && element.parentElement && box.width > 0 && box.height > 0) {
        const parent = element.parentElement;
        const parentStyle = getComputedStyle(parent);
        const parentBox = parent.getBoundingClientRect();
        const padRight = parseFloat(parentStyle.getPropertyValue('padding-right')) || 0;
        const rowRight = parentBox.x + parentBox.width - padRight;
        const hasTrailingSibling = [...(parent.children || [])].some(sib => {
          if (sib === element) return false;
          const cs = getComputedStyle(sib);
          if (cs.display === 'none' || cs.visibility === 'hidden') return false;
          const r = sib.getBoundingClientRect();
          if (r.width <= 0 || r.height <= 0) return false;
          const verticalOverlap = Math.min(box.y + box.height, r.y + r.height) - Math.max(box.y, r.y);
          return verticalOverlap > 0 && r.x >= box.x + box.width - 2;
        });
        if (!hasTrailingSibling && rowRight > box.x + box.width) {
          rowSlack = Number((rowRight - (box.x + box.width)).toFixed(2));
        }
      }
      const classAndId = `${element.id || ''} ${typeof element.className === 'string' ? element.className : ''}`;
      const toolbarExempt = ['header', 'nav', 'footer'].includes(tag) ||
        ['banner', 'toolbar', 'navigation', 'menubar', 'tablist'].includes((role || '').toLowerCase()) ||
        Boolean(element.closest && element.closest('header, nav, [role="banner"], [role="toolbar"], [role="navigation"]')) ||
        /(?:^|[-_\s])(topbar|toolbar|header|navbar|menubar|statusbar|action-bar|tab-bar)(?:$|[-_\s])/i.test(classAndId) ||
        (box.height > 0 && box.height <= 64 && style.getPropertyValue('justify-content') === 'space-between');
      return {selector: selectorFor(element),
        tag, inputType, role,
        toolbarExempt,
        label: element.getAttribute('aria-label'),
        labelledBy: element.getAttribute('aria-labelledby'),
        labels: element.labels ? [...element.labels].map(label => label.textContent) : [],
        text: ['INPUT', 'TEXTAREA'].includes(element.tagName) ? null : element.textContent.slice(0, 200),
        bounds: {x: box.x, y: box.y, width: box.width, height: box.height},
        ...(inkRatio !== null ? {inkWidth: Number(inkWidth.toFixed(2)), inkRatio} : {}),
        maxSiblingGap,
        row_slack: rowSlack,
        rowSlack,
        values: Object.fromEntries(properties.map(name => [name, style.getPropertyValue(name)]))};
    });
    return {width: innerWidth, height: innerHeight, elements,
      overflow: document.documentElement.scrollWidth > innerWidth,
      preference: {dark: matchMedia('(prefers-color-scheme: dark)').matches,
        reducedMotion: matchMedia('(prefers-reduced-motion: reduce)').matches,
        coarsePointer: matchMedia('(pointer: coarse)').matches},
      active: {tag: document.activeElement?.tagName,
        id: document.activeElement?.id, role: document.activeElement?.getAttribute('role')}};
  }, selector);
}

async function a11yChecks(page) {
  return page.evaluate(() => {
    function selectorFor(element) {
      if (element.id) return `#${CSS.escape(element.id)}`;
      const parent = element.parentElement;
      if (!parent) return element.tagName.toLowerCase();
      const siblings = [...parent.children].filter(node => node.tagName === element.tagName);
      return `${selectorFor(parent)} > ${element.tagName.toLowerCase()}:nth-of-type(${siblings.indexOf(element) + 1})`;
    }
    function isVisible(element) {
      const style = getComputedStyle(element);
      if (style.display === 'none' || style.visibility === 'hidden' || Number(style.opacity) === 0) return false;
      const box = element.getBoundingClientRect();
      return box.width > 0 && box.height > 0;
    }
    function boundsOf(element) {
      const box = element.getBoundingClientRect();
      return {
        x: Math.round(box.x),
        y: Math.round(box.y),
        width: Math.round(box.width),
        height: Math.round(box.height),
      };
    }
    function parseRgba(value) {
      const match = String(value || '').match(/rgba?\(\s*([\d.]+)[,\s]+([\d.]+)[,\s]+([\d.]+)(?:[,/\s]+([\d.]+))?\s*\)/i);
      if (!match) return null;
      return [Number(match[1]), Number(match[2]), Number(match[3]), match[4] === undefined ? 1 : Number(match[4])];
    }
    function blend(fg, bg) {
      const a = fg[3] + bg[3] * (1 - fg[3]);
      if (a <= 0) return [255, 255, 255, 0];
      return [
        (fg[0] * fg[3] + bg[0] * bg[3] * (1 - fg[3])) / a,
        (fg[1] * fg[3] + bg[1] * bg[3] * (1 - fg[3])) / a,
        (fg[2] * fg[3] + bg[2] * bg[3] * (1 - fg[3])) / a,
        a,
      ];
    }
    function resolveBackground(element) {
      const layers = [];
      let cur = element;
      while (cur && cur.nodeType === 1) {
        const parsed = parseRgba(getComputedStyle(cur).backgroundColor);
        if (parsed && parsed[3] > 0) {
          layers.push(parsed);
          if (parsed[3] >= 1) break;
        }
        cur = cur.parentElement;
      }
      const dark = matchMedia('(prefers-color-scheme: dark)').matches;
      let base = dark ? [0, 0, 0, 1] : [255, 255, 255, 1];
      for (let i = layers.length - 1; i >= 0; i--) {
        base = blend(layers[i], base);
      }
      return base;
    }
    function luminance([r, g, b]) {
      const channel = v => {
        const s = v / 255;
        return s <= 0.03928 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4);
      };
      return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b);
    }
    function contrastRatio(c1, c2) {
      const l1 = luminance(c1), l2 = luminance(c2);
      return (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05);
    }
    function accessibleName(element) {
      const ariaLabel = (element.getAttribute('aria-label') || '').trim();
      if (ariaLabel) return ariaLabel;
      const labelledBy = (element.getAttribute('aria-labelledby') || '').trim();
      if (labelledBy) {
        const text = labelledBy.split(/\s+/).map(id => document.getElementById(id)?.textContent?.trim() || '').filter(Boolean).join(' ');
        if (text) return text;
      }
      if (element.labels && element.labels.length) {
        const text = [...element.labels].map(node => node.textContent.trim()).filter(Boolean).join(' ');
        if (text) return text;
      }
      const tag = element.tagName.toLowerCase();
      if (tag === 'input') {
        const type = (element.getAttribute('type') || 'text').toLowerCase();
        if (['button', 'submit', 'reset'].includes(type)) {
          return (element.getAttribute('value') || type).trim();
        }
        if (type === 'image') {
          return (element.getAttribute('alt') || '').trim();
        }
        return (element.getAttribute('title') || element.getAttribute('placeholder') || '').trim();
      }
      const imgAlt = [...element.querySelectorAll('img[alt]')].map(img => (img.getAttribute('alt') || '').trim()).filter(Boolean).join(' ');
      if (imgAlt) return imgAlt;
      return (element.textContent || element.getAttribute('title') || '').trim();
    }

    const findings = [];
    const interactiveSelector = [
      'button', 'a[href]', 'input:not([type="hidden"])', 'select', 'textarea',
      '[role="button"]', '[role="link"]', '[role="checkbox"]', '[role="radio"]',
      '[role="switch"]', '[role="tab"]', '[role="menuitem"]', '[role="combobox"]', '[role="textbox"]',
    ].join(',');

    for (const control of document.querySelectorAll(interactiveSelector)) {
      if (!isVisible(control)) continue;
      if (!accessibleName(control)) {
        findings.push({
          rule: 'NUA301',
          severity: 'review',
          criterion: '4.1.2',
          selector: selectorFor(control),
          evidence: `Interactive <${control.tagName.toLowerCase()}> has no accessible name`,
        });
      }
      if ((control.tagName === 'BUTTON' || control.getAttribute('role') === 'button')
          && !(control.textContent || '').trim()
          && (control.querySelector('svg, img') || control.classList.contains('icon-button'))) {
        const box = control.getBoundingClientRect();
        if (box.width > 0 && box.height > 0 && Math.abs(box.width - box.height) > 1) {
          findings.push({
            rule: 'NUM208',
            severity: 'review',
            criterion: '2.5.8',
            selector: selectorFor(control),
            evidence: `Icon-only button ${selectorFor(control)} is ${Math.round(box.width)}x${Math.round(box.height)} px (not symmetric)`,
          });
        }
      }
      const nested = control.querySelector(interactiveSelector);
      if (nested && isVisible(nested)) {
        findings.push({
          rule: 'NUA303',
          severity: 'review',
          criterion: '4.1.2',
          selector: selectorFor(control),
          evidence: `Nested interactive control ${selectorFor(nested)} inside ${selectorFor(control)}`,
        });
      }
    }

    const ariaRefAttrs = ['aria-labelledby', 'aria-describedby', 'aria-controls', 'aria-owns'];
    for (const attr of ariaRefAttrs) {
      for (const element of document.querySelectorAll(`[${attr}]`)) {
        const raw = (element.getAttribute(attr) || '').trim();
        if (!raw) continue;
        const missing = raw.split(/\s+/).filter(id => id && !document.getElementById(id));
        if (missing.length) {
          findings.push({
            rule: 'NUA302',
            severity: 'review',
            criterion: '1.3.1',
            selector: selectorFor(element),
            evidence: `${attr} references missing id: ${missing.join(', ')}`,
          });
        }
      }
    }

    const idCounts = new Map();
    for (const element of document.querySelectorAll('[id]')) {
      const id = (element.getAttribute('id') || '').trim();
      if (!id) continue;
      idCounts.set(id, (idCounts.get(id) || 0) + 1);
    }
    for (const [id, count] of idCounts.entries()) {
      if (count > 1) {
        findings.push({
          rule: 'NUA304',
          severity: 'review',
          criterion: '4.1.1',
          selector: `#${CSS.escape(id)}`,
          evidence: `Duplicate id="${id}" (${count} elements)`,
        });
      }
    }

    let previousLevel = 0;
    for (const heading of document.querySelectorAll('h1,h2,h3,h4,h5,h6,[role="heading"][aria-level]')) {
      if (!isVisible(heading)) continue;
      const level = /^H[1-6]$/.test(heading.tagName)
        ? Number(heading.tagName.slice(1))
        : Number(heading.getAttribute('aria-level'));
      if (!Number.isInteger(level) || level < 1) continue;
      if (previousLevel > 0 && level > previousLevel + 1) {
        findings.push({
          rule: 'NUA305',
          severity: 'review',
          criterion: '1.3.1',
          selector: selectorFor(heading),
          evidence: `Heading level skipped from h${previousLevel} to h${level}`,
        });
      }
      previousLevel = level;
    }

    const walker = document.createTreeWalker(document.body || document.documentElement, NodeFilter.SHOW_TEXT);
    const checkedElements = new Set();
    const checkedTimestampElements = new Set();
    const rawUiTimestampRe = /\b\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?|\s+\d{2}:\d{2}(?::\d{2})?(?:\s*(?:UTC|GMT|[+-]\d{2}:?\d{2}))?)\b|\b\d{2}:\d{2}(?::\d{2})?\s*(?:UTC|GMT)\b/;
    let textNode;
    while ((textNode = walker.nextNode())) {
      if (!textNode.nodeValue || !textNode.nodeValue.trim()) continue;
      const parent = textNode.parentElement;
      if (!parent || ['SCRIPT', 'STYLE', 'NOSCRIPT'].includes(parent.tagName)) continue;
      if (!isVisible(parent)) continue;
      if (!checkedTimestampElements.has(parent) && !parent.closest('code, pre, textarea')) {
        const tsMatch = rawUiTimestampRe.exec(textNode.nodeValue);
        if (tsMatch) {
          checkedTimestampElements.add(parent);
          findings.push({
            rule: 'NUA310',
            severity: 'review',
            criterion: 'relative-timestamps',
            selector: selectorFor(parent),
            evidence: `Visible raw or unadjusted timestamp in UI text: ${tsMatch[0]}`,
          });
        }
      }
      if (checkedElements.has(parent)) continue;
      checkedElements.add(parent);
      const style = getComputedStyle(parent);
      const fgRaw = parseRgba(style.color);
      if (!fgRaw || fgRaw[3] <= 0) continue;
      const bg = resolveBackground(parent);
      const fg = blend(fgRaw, bg);
      const ratio = contrastRatio(fg, bg);
      const size = parseFloat(style.fontSize) || 16;
      const weight = Number(style.fontWeight) || (style.fontWeight === 'bold' ? 700 : 400);
      const large = size >= 24 || (size >= 18.66 && weight >= 700);
      const threshold = large ? 3.0 : 4.5;
      if (ratio + 1e-4 < threshold) {
        findings.push({
          rule: 'NUA306',
          severity: 'review',
          criterion: '1.4.3',
          selector: selectorFor(parent),
          evidence: `Contrast ratio ${ratio.toFixed(2)}:1 is below ${threshold}:1`,
        });
      }
    }

    let hasAuthorFocusOutline = false;
    for (const sheet of Array.from(document.styleSheets || [])) {
      let rules;
      try {
        rules = sheet.cssRules;
      } catch {
        continue;
      }
      for (const rule of Array.from(rules || [])) {
        const text = String(rule.cssText || '');
        if (/:focus(?:-visible)?\b/.test(text) && /\boutline\b/.test(text)) {
          hasAuthorFocusOutline = true;
          break;
        }
      }
      if (hasAuthorFocusOutline) break;
    }
    if (hasAuthorFocusOutline) {
      const prevActive = document.activeElement;
      const prevKeyboardNav = document.documentElement.dataset.keyboardNav;
      delete document.documentElement.dataset.keyboardNav;
      for (const control of document.querySelectorAll(interactiveSelector)) {
        if (!isVisible(control) || control.disabled) continue;
        try {
          control.dispatchEvent(new PointerEvent('pointerdown', {bubbles: true}));
          control.focus({preventScroll: true});
          const fStyle = getComputedStyle(control);
          const outlineW = parseFloat(fStyle.outlineWidth) || 0;
          if (outlineW > 0 && fStyle.outlineStyle !== 'none') {
            findings.push({
              rule: 'NUA307',
              severity: 'review',
              criterion: '2.4.7',
              selector: selectorFor(control),
              bounds: boundsOf(control),
              evidence: `Interactive control ${selectorFor(control)} shows focus outline (${fStyle.outlineWidth} ${fStyle.outlineStyle}) on pointer focus before keyboard navigation`,
            });
          }
        } catch {}
      }
      if (prevKeyboardNav !== undefined) document.documentElement.dataset.keyboardNav = prevKeyboardNav;
      else delete document.documentElement.dataset.keyboardNav;
      try {
        if (prevActive && typeof prevActive.focus === 'function') prevActive.focus({preventScroll: true});
        else if (document.activeElement && typeof document.activeElement.blur === 'function') document.activeElement.blur();
      } catch {}
    }

    for (const node of document.querySelectorAll(`${interactiveSelector},[role="tooltip"]`)) {
      if (!isVisible(node)) continue;
      const rect = node.getBoundingClientRect();
      const style = getComputedStyle(node);
      const isTooltip = node.getAttribute('role') === 'tooltip';
      const halo = isTooltip
        ? 0
        : style.outlineStyle === 'none'
          ? 0
          : Math.max(0, (parseFloat(style.outlineWidth) || 0) + (parseFloat(style.outlineOffset) || 0));
      if (!isTooltip && halo <= 0) continue;
      let anc = node.parentElement;
      while (anc && anc !== document.body && anc !== document.documentElement) {
        const aStyle = getComputedStyle(anc);
        const ox = aStyle.overflowX;
        const oy = aStyle.overflowY;
        const isScrollable = ['auto', 'scroll'].includes(ox) || ['auto', 'scroll'].includes(oy);
        if (!isTooltip && isScrollable) {
          const sRect = anc.getBoundingClientRect();
          if (
            rect.bottom <= sRect.top + 0.5 ||
            rect.top >= sRect.bottom - 0.5 ||
            rect.right <= sRect.left + 0.5 ||
            rect.left >= sRect.right - 0.5 ||
            (anc.scrollTop > 0 && rect.top < sRect.top) ||
            (anc.scrollLeft > 0 && rect.left < sRect.left)
          ) {
            break;
          }
        }
        const clips = isTooltip
          ? ['hidden', 'clip', 'auto', 'scroll'].includes(ox) || ['hidden', 'clip', 'auto', 'scroll'].includes(oy)
          : ['hidden', 'clip'].includes(ox) || ['hidden', 'clip'].includes(oy);
        if (clips) {
          const aRect = anc.getBoundingClientRect();
          if (
            rect.left - halo < aRect.left - 0.5 ||
            rect.top - halo < aRect.top - 0.5 ||
            rect.right + halo > aRect.right + 0.5 ||
            rect.bottom + halo > aRect.bottom + 0.5
          ) {
            findings.push({
              rule: 'NUA308',
              severity: 'review',
              criterion: '2.4.11',
              selector: selectorFor(node),
              bounds: boundsOf(node),
              evidence: `${isTooltip ? 'Tooltip' : 'Focus halo'} on ${selectorFor(node)} is clipped by ${selectorFor(anc)}`,
            });
            break;
          }
        }
        anc = anc.parentElement;
      }
    }

    const prevFocus = document.activeElement;
    for (const control of document.querySelectorAll(interactiveSelector)) {
      if (!isVisible(control)) continue;
      const baseRect = control.getBoundingClientRect();
      if (baseRect.width <= 0 || baseRect.height <= 0) continue;
      const hadBusy = control.hasAttribute('aria-busy');
      const prevBusy = control.getAttribute('aria-busy');
      const prevDisabled = 'disabled' in control ? control.disabled : null;
      const checkRect = stateLabel => {
        const nextRect = control.getBoundingClientRect();
        if (Math.abs(nextRect.width - baseRect.width) > 0.5 || Math.abs(nextRect.height - baseRect.height) > 0.5) {
          findings.push({
            rule: 'NUA309',
            severity: 'review',
            criterion: 'clause-9',
            selector: selectorFor(control),
            evidence: `Interactive control ${selectorFor(control)} changes dimensions on ${stateLabel} (${baseRect.width.toFixed(1)}x${baseRect.height.toFixed(1)} -> ${nextRect.width.toFixed(1)}x${nextRect.height.toFixed(1)})`,
          });
          return true;
        }
        return false;
      };
      try {
        if (!control.disabled && typeof control.focus === 'function') {
          control.focus({preventScroll: true});
          checkRect('focus');
        }
        control.setAttribute('aria-busy', 'true');
        checkRect('aria-busy="true"');
        if (hadBusy) control.setAttribute('aria-busy', prevBusy);
        else control.removeAttribute('aria-busy');
        if (prevDisabled !== null && !prevDisabled) {
          control.disabled = true;
          checkRect('disabled');
          control.disabled = false;
        }
      } catch {}
    }
    try {
      if (prevFocus && typeof prevFocus.focus === 'function') prevFocus.focus({preventScroll: true});
      else if (document.activeElement && typeof document.activeElement.blur === 'function') document.activeElement.blur();
    } catch {}

    return {status: 'checked', findings};
  });
}

function validateFlow(steps) {
  if (!Array.isArray(steps)) throw new Error('Flow must be an array');
  const actions = new Set(['goto', 'click', 'fill', 'press', 'hover', 'focus', 'offline',
    'visible', 'hidden', 'focused', 'text', 'value', 'attribute', 'count', 'screenshot']);
  for (const step of steps) {
    if (!step || typeof step !== 'object' || !actions.has(step.action)) {
      throw new Error('Unsupported flow action');
    }
    if (step.action === 'goto') url(step.url);
    if (step.action === 'fill' && typeof step.value !== 'string') throw new Error('Fill requires a string value');
    if (step.action === 'press' && typeof step.key !== 'string') throw new Error('Press requires a key');
    if (step.action === 'offline' && typeof step.value !== 'boolean') throw new Error('Offline requires a boolean');
    if (step.action === 'count' && (!Number.isInteger(step.expected) || step.expected < 0)) {
      throw new Error('Count requires a nonnegative expected integer');
    }
    if (['text', 'value'].includes(step.action) && typeof step.expected !== 'string') {
      throw new Error('Text and value checks require an expected string');
    }
    if (step.action === 'attribute' && (typeof step.name !== 'string' ||
        !(step.expected === null || typeof step.expected === 'string'))) {
      throw new Error('Attribute requires a name and an expected string or null');
    }
    if (!['goto', 'offline', 'screenshot'].includes(step.action) && typeof step.selector !== 'string') {
      throw new Error('Flow action requires a selector');
    }
  }
}

async function flow(page, context, steps, output, timeout, viewportMeta = {}) {
  const results = [];
  let assertions = 0;
  for (const [index, step] of steps.entries()) {
    const row = {
      index,
      action: step.action,
      selector: step.selector,
      ...(viewportMeta.width ? {width: viewportMeta.width} : {}),
      ...(viewportMeta.scheme ? {scheme: viewportMeta.scheme} : {}),
      status: 'not_run',
    };
    results.push(row);
    try {
      const target = step.selector ? page.locator(step.selector) : null;
      if (step.action === 'goto') await page.goto(url(step.url), {timeout});
      if (step.action === 'click') await target.click({timeout});
      if (step.action === 'fill') await target.fill(String(step.value), {timeout});
      if (step.action === 'press') await target.press(step.key, {timeout});
      if (step.action === 'hover') await target.hover({timeout});
      if (step.action === 'focus') await target.focus({timeout});
      if (step.action === 'offline') await context.setOffline(Boolean(step.value));
      if (step.action === 'visible' || step.action === 'hidden') {
        assertions++;
        await target.waitFor({state: step.action, timeout});
      }
      if (step.action === 'focused') {
        assertions++;
        await page.waitForFunction(selector => document.activeElement === document.querySelector(selector),
          step.selector, {timeout});
      }
      if (['text', 'value', 'attribute', 'count'].includes(step.action)) {
        assertions++;
        await page.waitForFunction(step => {
          const nodes = document.querySelectorAll(step.selector);
          if (step.action === 'count') return nodes.length === step.expected;
          const node = nodes[0];
          if (!node) return false;
          const observed = step.action === 'text' ? node.textContent : step.action === 'value'
            ? node.value : node.getAttribute(step.name);
          return observed === step.expected;
        }, step, {timeout});
        let observed;
        if (step.action === 'text') observed = await target.textContent({timeout});
        if (step.action === 'value') observed = await target.inputValue({timeout});
        if (step.action === 'attribute') observed = await target.getAttribute(step.name, {timeout});
        if (step.action === 'count') observed = await target.count();
        row.observed = observed;
        row.expected = step.expected;
        if (observed !== step.expected) throw new Error('Observed value differs from the flow expectation');
      }
      if (step.action === 'screenshot') {
        row.path = path.join(output, `flow-${index}.png`);
        await page.screenshot({path: row.path, fullPage: true, timeout});
      }
      row.status = 'passed';
    } catch (error) {
      row.status = 'failed';
      row.error = String(error.message);
      if (step.selector) {
        try {
          const state = await page.evaluate(sel => {
            const el = document.querySelector(sel);
            if (!el) return {matched: 0, visible: false};
            const cs = getComputedStyle(el);
            const r = el.getBoundingClientRect();
            const vis =
              cs.display !== 'none' &&
              cs.visibility !== 'hidden' &&
              Number(cs.opacity) !== 0 &&
              r.width > 0 &&
              r.height > 0;
            return {
              matched: document.querySelectorAll(sel).length,
              visible: vis,
              hiddenAttr: Boolean(el.hidden),
              display: cs.display,
              visibility: cs.visibility,
            };
          }, step.selector);
          row.visible = state.visible;
          row.selectorState = state;
        } catch {}
      }
      return {
        status: 'failed',
        ...(viewportMeta.width ? {width: viewportMeta.width} : {}),
        ...(viewportMeta.scheme ? {scheme: viewportMeta.scheme} : {}),
        failedStep: row,
        assertions,
        steps: results,
        remaining: steps.length - results.length,
      };
    }
  }
  return {status: assertions ? 'passed' : steps.length ? 'actions_run' : 'not_run',
    assertions, steps: results, remaining: 0};
}

async function renderWireframe(page, outPath, timeout, selector) {
  await page.evaluate(extraSelector => {
    const existing = document.getElementById('nuu-wireframe-overlay');
    if (existing) existing.remove();
    const docEl = document.documentElement;
    const docW = Math.max(docEl.scrollWidth || 0, docEl.clientWidth || 0, window.innerWidth || 0);
    const docH = Math.max(docEl.scrollHeight || 0, docEl.clientHeight || 0, window.innerHeight || 0);
    const root = document.createElement('div');
    root.id = 'nuu-wireframe-overlay';
    root.setAttribute('aria-hidden', 'true');
    root.style.cssText =
      `position:absolute;left:0;top:0;width:${docW}px;height:${docH}px;` +
      'pointer-events:none;z-index:2147483647;overflow:hidden;' +
      'background:rgba(10,12,16,0.22);font:600 10px/1.2 ui-monospace,SFMono-Regular,Menlo,monospace;';
    const baseQuery =
      'header,nav,main,section,article,aside,footer,dialog,form,table,details,summary,' +
      'button,input,select,textarea,a,img,h1,h2,h3,[role],[id],.card,.panel,.row,.toolbar,.tab,.modal,.chip';
    const fullQuery = extraSelector ? `${baseQuery},${extraSelector}` : baseQuery;
    let nodes = [];
    try {
      nodes = Array.from(document.querySelectorAll(fullQuery));
    } catch {
      nodes = Array.from(document.querySelectorAll(baseQuery));
    }
    const seen = new Set();
    for (const el of nodes) {
      if (!el || el === root || root.contains(el) || seen.has(el)) continue;
      seen.add(el);
      const style = window.getComputedStyle(el);
      if (style.display === 'none' || style.visibility === 'hidden' || Number(style.opacity) === 0) continue;
      const rect = el.getBoundingClientRect();
      const w = Math.round(rect.width);
      const h = Math.round(rect.height);
      if (w < 8 || h < 8) continue;
      const x = Math.round(rect.left + window.scrollX);
      const y = Math.round(rect.top + window.scrollY);
      const tag = el.tagName.toLowerCase();
      const isControl = ['button', 'input', 'select', 'textarea', 'a', 'summary'].includes(tag) ||
        ['button', 'link', 'tab', 'checkbox', 'radio', 'switch', 'textbox', 'combobox'].includes(el.getAttribute('role') || '');
      const isLandmark = ['header', 'nav', 'main', 'section', 'article', 'aside', 'footer', 'dialog', 'form'].includes(tag);
      const color = isControl ? '#ffab00' : (isLandmark ? '#00e5ff' : '#ff4081');
      const box = document.createElement('div');
      box.style.cssText =
        `position:absolute;left:${x}px;top:${y}px;width:${w}px;height:${h}px;` +
        `border:1.5px solid ${color};box-sizing:border-box;background:rgba(0,0,0,0.06);`;
      const idPart = el.id ? `#${el.id}` : '';
      const cls = !idPart && typeof el.className === 'string' && el.className.trim()
        ? `.${el.className.trim().split(/\s+/)[0]}`
        : '';
      const label = document.createElement('span');
      label.textContent = `${tag}${idPart || cls} ${w}\u00d7${h}`;
      label.style.cssText =
        `position:absolute;left:0;top:0;max-width:${Math.max(64, w)}px;` +
        'padding:1px 4px;background:rgba(12,14,18,0.88);' +
        `color:${color};white-space:nowrap;overflow:hidden;text-overflow:ellipsis;`;
      box.appendChild(label);
      root.appendChild(box);
    }
    document.body.appendChild(root);
  }, selector || '');
  try {
    await page.screenshot({path: outPath, fullPage: true, timeout});
  } finally {
    await page.evaluate(() => {
      const existing = document.getElementById('nuu-wireframe-overlay');
      if (existing) existing.remove();
    });
  }
}

async function run() {
  if (request.command !== 'doctor') {
  if (request.overlaps !== undefined && typeof request.overlaps !== 'boolean') {
    throw new Error('Overlaps flag must be a boolean');
  }
  if (request.wireframe !== undefined && typeof request.wireframe !== 'boolean') {
    throw new Error('Wireframe flag must be a boolean');
  }
  if (request.ready !== undefined && request.ready !== null &&
      (typeof request.ready !== 'string' || !request.ready.trim())) {
    throw new Error('Ready requires a non-empty selector');
  }
    url(request.url);
    validateFlow(request.flow || []);
    if (!Array.isArray(request.widths) || !request.widths.length ||
        request.widths.some(width => !Number.isInteger(width) || width <= 0) || !(request.timeout > 0)) {
      throw new Error('Widths and timeout must be positive');
    }
    if (request.executable && request.engine === 'auto') {
      throw new Error('An explicit executable requires an explicit engine');
    }
    if (request.edit) {
      const edit = request.edit;
      if (request.command !== 'live' || request.authorized_edit !== true) throw new Error('Live edit authorization missing');
      if (typeof edit.selector !== 'string' || !edit.selector ||
          (edit.text === undefined && edit.styles === undefined) ||
          (edit.text !== undefined && typeof edit.text !== 'string') ||
          (edit.styles !== undefined && (!edit.styles || Array.isArray(edit.styles) ||
            typeof edit.styles !== 'object' || Object.values(edit.styles).some(value => typeof value !== 'string'))) ||
          Object.keys(edit).some(name => !['selector', 'text', 'styles'].includes(name))) {
        throw new Error('Edit requires a selector and text or a style object');
      }
    }
  }
  const pw = dependency();
  if (!pw) return {status: 'not_checked', reason: 'playwright_unavailable', captures: []};
  const engines = Object.entries(pw).filter(([, value]) =>
    typeof value?.launch === 'function' && typeof value?.executablePath === 'function');
  const available = engines.map(([name, engine]) => ({name,
    executable: engine.executablePath(), available: fs.existsSync(engine.executablePath())}));
  if (request.command === 'doctor') {
    return {status: 'checked', engines: available, browser_launch: 'not_run'};
  }
  const target = url(request.url);
  validateFlow(request.flow || []);
  if (request.edit && request.command !== 'live') throw new Error('Edits require live mode');
  const chosen = request.engine === 'auto'
    ? engines.find(([name]) => available.some(row => row.name === name && row.available))
    : engines.find(([name]) => name === request.engine);
  if (!chosen) return {status: 'not_checked', reason: 'browser_unavailable', engines: available, captures: []};
  if (request.executable && request.engine === 'auto') {
    throw new Error('An explicit executable requires an explicit engine');
  }
  let browser;
  try {
    browser = await chosen[1].launch({headless: true,
      ...(request.executable ? {executablePath: request.executable} : {})});
  } catch (error) {
    return {status: 'not_checked', reason: 'browser_launch_failed', detail: String(error.message), captures: []};
  }
  const artifactRoot = path.resolve(request.output);
  fs.mkdirSync(artifactRoot, {recursive: true});
  const output = fs.mkdtempSync(path.join(artifactRoot, 'capture-'));
  const captures = [];
  const timeout = request.timeout * 1000;
  const HOUSE_HEIGHTS = {
    1920: 957,
    1372: 883,
    1080: 2437,
    958: 958,
    411: 799,
    855: 325,
    ...(request.heights || {}),
  };
  const REPORT_WIDTHS = Array.isArray(request.report_widths) && request.report_widths.length
    ? request.report_widths
    : [1920, 411];
  try {
    for (const width of request.widths) {
      const height = Number(request.height || HOUSE_HEIGHTS[width] || 900);
      const touch = width <= 768 || height <= 480;
      for (const scheme of ['dark', 'light']) {
        for (const motion of ['no-preference', 'reduce']) {
          const context = await browser.newContext({viewport: {width, height},
            hasTouch: touch, isMobile: touch,
            colorScheme: scheme, reducedMotion: motion, acceptDownloads: false});
          const page = await context.newPage();
          const errors = [];
          page.on('pageerror', error => errors.push({type: 'pageerror', message: error.message}));
          page.on('console', message => {
            if (message.type() === 'error') errors.push({type: 'console', message: message.text()});
          });
          const row = {width, height, touch, scheme, motion,
            report_shot: REPORT_WIDTHS.includes(width) && scheme === 'dark' && motion === 'no-preference',
            status: 'not_checked', errors,
            flow: {status: 'not_run'}, edit: {status: 'not_run'}};
          captures.push(row);
          try {
            const response = await page.goto(target, {waitUntil: 'load', timeout});
            row.http_status = response?.status();
            if (response && !response.ok()) throw new Error('Page returned a failing HTTP status');
            if (request.ready) {
              await page.locator(request.ready).first().waitFor({state: 'visible', timeout});
            }
            row.measurements = await metrics(page, request.selector);
            if (captures.length === 1) {
              row.flow = await flow(page, context, request.flow || [], output, timeout, {width, scheme, motion});
              if (request.edit) {
                const edit = request.edit;
                const node = page.locator(edit.selector);
                if (await node.count() !== 1) throw new Error('Live edit requires exactly one element');
                row.edit = {status: 'authorized', before: await metrics(page, edit.selector)};
                row.edit.before_screenshot = path.join(output, 'live-before.png');
                await page.screenshot({path: row.edit.before_screenshot, fullPage: true, timeout});
                await node.evaluate((element, edit) => {
                  if (edit.text !== undefined) element.textContent = String(edit.text);
                  for (const [name, value] of Object.entries(edit.styles || {})) {
                    element.style.setProperty(name, String(value));
                  }
                }, edit);
                row.edit.after = await metrics(page, edit.selector);
                row.edit.after_screenshot = path.join(output, 'live-after.png');
                await page.screenshot({path: row.edit.after_screenshot, fullPage: true, timeout});
                row.edit.persistence = 'rendered page only';
              }
            }
            row.measurements = await metrics(page, request.selector);
            if (request.overlaps) row.overlaps = await overlaps(page, request.selector);
            if (request.a11y || request.command === 'a11y' || request.command === 'audit') {
              row.a11y = await a11yChecks(page);
            }
            row.screenshot = path.join(output, `${width}-${scheme}-${motion}.png`);
            await page.screenshot({path: row.screenshot, fullPage: true, timeout});
            if (request.wireframe) {
              row.wireframe_screenshot = path.join(output, `${width}-${scheme}-${motion}-wireframe.png`);
              await renderWireframe(page, row.wireframe_screenshot, timeout, request.selector, request.typography);
            }
            row.status = 'captured';
          } catch (error) {
            row.status = 'failed';
            row.error = String(error.message);
          } finally {
            await context.close();
          }
        }
      }
    }
  } finally {
    await browser.close();
  }
  const findings = [];
  for (const row of captures) {
    const location = {url: target, width: row.width, scheme: row.scheme, motion: row.motion};
    if (row.status === 'failed') findings.push({rule: 'NUB104', severity: 'error', location, evidence: row.error});
    if (row.flow.status === 'failed') findings.push({rule: 'NUB103', severity: 'error', location, evidence: row.flow});
    if (Array.isArray(row.flow?.steps)) {
      for (const stepRow of row.flow.steps) {
        if (stepRow.dimensionShift) {
          findings.push({
            rule: 'NUA309',
            severity: 'review',
            criterion: 'clause-9',
            selector: stepRow.selector,
            location,
            evidence: `Interactive control ${stepRow.selector} changes dimensions on ${stepRow.action} (${stepRow.dimensionShift.before.width.toFixed(1)}x${stepRow.dimensionShift.before.height.toFixed(1)} -> ${stepRow.dimensionShift.after.width.toFixed(1)}x${stepRow.dimensionShift.after.height.toFixed(1)})`,
          });
        }
      }
    }
    if (row.errors.length) findings.push({rule: 'NUB102', severity: 'review', location, evidence: row.errors});
    if (row.measurements?.overflow) findings.push({rule: 'NUB101', severity: 'review', location, evidence: 'Horizontal overflow'});
    if (row.a11y?.findings?.length) {
      for (const item of row.a11y.findings) {
        findings.push({...item, location});
      }
    }
  }
  return {status: findings.length ? 'findings' : 'captured', findings,
    engine: chosen[0], captures,
    report_shots: captures.filter(row => row.report_shot && row.screenshot).map(row => row.screenshot),
    automated_flow: 'first capture only',
    synthesized_input: 'Playwright actions', visual_judgment: 'not_run', participant_research: 'not_run', real_device: 'not_run'};
}

run().then(emit).catch(error => {
  emit({status: 'error', reason: error.message, captures: []});
  process.exitCode = 2;
});
