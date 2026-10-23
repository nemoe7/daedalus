#!/usr/bin/env node
'use strict';

const fs = require('node:fs');
const path = require('node:path');
const VERSION = '0.1.3';
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
      return {selector: selectorFor(element),
        tag: element.tagName.toLowerCase(), role: element.getAttribute('role'),
        label: element.getAttribute('aria-label'),
        labelledBy: element.getAttribute('aria-labelledby'),
        labels: element.labels ? [...element.labels].map(label => label.textContent) : [],
        text: ['INPUT', 'TEXTAREA'].includes(element.tagName) ? null : element.textContent.slice(0, 200),
        bounds: {x: box.x, y: box.y, width: box.width, height: box.height},
        values: Object.fromEntries(properties.map(name => [name, style.getPropertyValue(name)]))};
    });
    return {width: innerWidth, height: innerHeight, elements,
      overflow: document.documentElement.scrollWidth > innerWidth,
      preference: {dark: matchMedia('(prefers-color-scheme: dark)').matches,
        reducedMotion: matchMedia('(prefers-reduced-motion: reduce)').matches},
      active: {tag: document.activeElement?.tagName,
        id: document.activeElement?.id, role: document.activeElement?.getAttribute('role')}};
  }, selector);
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

async function flow(page, context, steps, output, timeout) {
  const results = [];
  let assertions = 0;
  for (const [index, step] of steps.entries()) {
    const row = {index, action: step.action, selector: step.selector, status: 'not_run'};
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
      return {status: 'failed', assertions, steps: results,
        remaining: steps.length - results.length};
    }
  }
  return {status: assertions ? 'passed' : steps.length ? 'actions_run' : 'not_run',
    assertions, steps: results, remaining: 0};
}

async function run() {
  if (request.command !== 'doctor') {
  if (request.overlaps !== undefined && typeof request.overlaps !== 'boolean') {
    throw new Error('Overlaps flag must be a boolean');
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
  try {
    for (const width of request.widths) {
      for (const scheme of ['light', 'dark']) {
        for (const motion of ['no-preference', 'reduce']) {
          const context = await browser.newContext({viewport: {width, height: 900},
            colorScheme: scheme, reducedMotion: motion, acceptDownloads: false});
          const page = await context.newPage();
          const errors = [];
          page.on('pageerror', error => errors.push({type: 'pageerror', message: error.message}));
          page.on('console', message => {
            if (message.type() === 'error') errors.push({type: 'console', message: message.text()});
          });
          const row = {width, scheme, motion, status: 'not_checked', errors,
            flow: {status: 'not_run'}, edit: {status: 'not_run'}};
          captures.push(row);
          try {
            const response = await page.goto(target, {waitUntil: 'load', timeout});
            row.http_status = response?.status();
            if (response && !response.ok()) throw new Error('Page returned a failing HTTP status');
            row.measurements = await metrics(page, request.selector);
            if (captures.length === 1) {
              row.flow = await flow(page, context, request.flow || [], output, timeout);
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
            row.screenshot = path.join(output, `${width}-${scheme}-${motion}.png`);
            await page.screenshot({path: row.screenshot, fullPage: true, timeout});
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
    if (row.status === 'failed') findings.push({rule: 'UIX104', severity: 'error', location, evidence: row.error});
    if (row.flow.status === 'failed') findings.push({rule: 'UIX103', severity: 'error', location, evidence: row.flow});
    if (row.errors.length) findings.push({rule: 'UIX102', severity: 'review', location, evidence: row.errors});
    if (row.measurements?.overflow) findings.push({rule: 'UIX101', severity: 'review', location, evidence: 'Horizontal overflow'});
  }
  return {status: findings.length ? 'findings' : 'captured', findings,
    engine: chosen[0], captures, automated_flow: 'first capture only',
    synthesized_input: 'Playwright actions', visual_judgment: 'not_run', participant_research: 'not_run', real_device: 'not_run'};
}

run().then(emit).catch(error => {
  emit({status: 'error', reason: error.message, captures: []});
  process.exitCode = 2;
});
