// Ordered preview-client checks under node:test. Later checks read the page earlier checks left.
// This is not a browser rendering test.
const assert = require('node:assert/strict');
// Check that height grows with content instead of requiring a fixed pixel value.
const px = (value) => parseInt(value, 10) || 0;
const { test } = require('node:test');
const fs = require('node:fs');

const path = require('node:path');
const vm = require('node:vm');
const { File } = require('node:buffer');
const { Parser } = require('htmlparser2');

test('preview client', async (t) => {
  class Element {
    get content() { return this; }
    // textContent reads through to children, the way a browser does, so a receipt built from two elements still asserts as one string.
    // Setting it clears the children, as in a browser. A bare string child is a text node, the way replaceChildren takes one.
    get textContent() {
      return this.children.length
        ? this.children.map(child => (typeof child === 'string' ? child : child.textContent)).join('')
        : this.text;
    }
    set textContent(value) {
      this.text = value;
      this.children = [];
    }
    constructor() {
      this.value = '';
      this.text = '';
      this.hidden = false;
      this.disabled = false;
      this.children = [];
      this.attributes = {};
      this.dataset = {};
      this.style = {};
      this.events = {};
      this.scrollTop = 0;
      this.scrollHeight = 0;
      this.clientHeight = 0;
    }
    setAttribute(name, value) { this.attributes[name] = value; }
    getAttribute(name) { return this.attributes[name]; }
    addEventListener(name, callback) { this.events[name] = callback; }
    append(...children) {
      for (const child of children) {
        this.children = this.children.filter(item => item !== child);
        this.children.push(child);
      }
    }
    insertBefore(child, reference) {
      this.children = this.children.filter(item => item !== child);
      const index = this.children.indexOf(reference);
      this.children.splice(index < 0 ? this.children.length : index, 0, child);
      return child;
    }
    removeChild(child) { this.children = this.children.filter(item => item !== child); return child; }
    replaceChildren(...children) { this.children = children; this.replacements = (this.replacements || 0) + 1; }
    // Reset report scroll when replacing content, as browser layout does.
    set innerHTML(value) {
      this.inner = value; this.renders = (this.renders || 0) + 1;
      this.text = '';
      new Parser({ ontext: text => { this.text += text; } }).end(value);
      this.children = [];
      const panel = elements.get('#reports-panel');
      if (this.id === 'report' && panel) panel.scrollTop = 0;
    }
    get innerHTML() { return this.inner; }
    querySelector(selector) { return this.children.find(child => child.tagName === selector) || null; }
    querySelectorAll(selector) { return this.fields && selector === '.question[data-field]' ? this.fields : []; }
    contains(node) { return this === node || this.children.some(child => child instanceof Element && child.contains(node)); }
    get lastElementChild() { return this.children.at(-1); }
    focus() { this.focused = true; }
    scrollIntoView() { this.scrolledIntoView = true; }
    scrollTo(options) { this.lastScroll = options; this.scrollTop = options.top; }
    select() { this.selected = true; }
    setSelectionRange(start, end) { this.selectionStart = start; this.selectionEnd = end; }
    remove() { this.removed = true; }
    requestSubmit() { this.submissions = (this.submissions || 0) + 1; }
    click() { this.clicks = (this.clicks || 0) + 1; }
  }
  const elements = new Map();
  const documentEvents = {};
  const documentCaptureEvents = {};
  const intervals = [];
  const windowEvents = {};
  const copied = [];
  let clipboardFails = false;
  let execCommandResult = true;
  const get = id => {
    if (!elements.has(id)) {
      const element = new Element();
      // A stub element knows its own id, so the parts of the browser a stub has to model can key on it:
      // the scroll clamp below fires for `#report` and nothing else.
      if (id.startsWith('#')) element.id = id.slice(1);
      elements.set(id, element);
    }
    return elements.get(id);
  };
  const taskParts = row => row.children.filter(child => child.className !== 'task-drag-handle');
  const taskPart = (row, className) => row.children.find(child => child.className === className);
  // The first shape of a control's icon: a control holds one SVG, and its shapes sit in draw
  // order, so the same index reads the same shape however many the icon carries.
  const shapeOf = element => element.children.at(-1).children[0];
  get('#notes-tab').setAttribute('aria-controls', 'notes-panel');
  get('#reports-tab').setAttribute('aria-controls', 'reports-panel');
  get('#tasks-tab').setAttribute('aria-controls', 'tasks-panel');
  get('#downloads-tab').setAttribute('aria-controls', 'downloads-panel');
  get('#reports-panel').hidden = true;
  get('#tasks-panel').hidden = true;
  get('#downloads-panel').hidden = true;
  get('#upload-file').files = [];
  get('#note').placeholder = 'What should happen next?';
  get('#send').textContent = 'Send';
  get('#send').dataset.busyLabel = 'Sending...';
  const storage = new Map();
  const root = { dataset: {} };
  let counter = 0;
  let sendHandler;
  let state = { notes: [], reports: [], fetch_jobs: [], last_check: null };
  let stateFails = false;
  let stateResponseWait = null;
  let stateGatewayBody = null;
  let reportFields = 0;
  let reportRevision = '2026-09-22T12:00:00.100000+00:00';
  let reportPublished = '2026-09-22T12:00:00.100000+00:00';
  let reportConflict = false;
  let reportHtmlWait = null;
  let reportSubmitWait = null;
  const sent = [];
  const readStamps = [];
  let readStampFailures = 0;
  const ackSeenCalls = [];
  const ackSeenFailures = new Set();
  const viewCalls = [];
  const viewActive = [];
  const skipCalls = [];
  const orderWrites = [];
  let orderFails = false;
  const replySeenCalls = [];
  let replySeenWait = null;
  let replySeenError = false;
  const unpublished = [];
  const uploadCalls = [];
  let loseUploadResponse = false;
  let dropUploadOnce = false;
  class FormDataStub {
    constructor() { this.fields = new Map(); }
    append(name, value) { this.fields.set(name, [...(this.fields.get(name) || []), value]); }
    get(name) { return (this.fields.get(name) || [])[0]; }
    getAll(name) { return this.fields.get(name) || []; }
  }
  const queuedCalls = [];
  const dropped = [];
  const remoteCalls = [];
  const remoteBehaviors = new Map();
  const resultCalls = [];
  const failedCalls = [];
  let enqueueResponseLost = false;
  let pageFetches = 0;
  const writeHeadersSeen = [];
  const response = (value, ok = true) => {
    const body = JSON.stringify(value);
    return { ok, status: ok ? 200 : 503, json: async () => JSON.parse(body), text: async () => body };
  };
  let coarsePointer = false;
  let reducedMotion = false;
  const pointerListeners = [];
  const pendingWrites = new Map();
  const interruptCalls = [];
  const failedWrites = new Set();
  const context = {
    window: {
      addEventListener: (name, callback) => { windowEvents[name] = callback; },
      matchMedia: query => ({
        // A live getter: the client reads the query once and the pointer may change later.
        get matches() { return query === '(pointer: coarse)' ? coarsePointer : query === '(prefers-reduced-motion: reduce)' && reducedMotion; },
        addEventListener: (name, callback) => { if (name === 'change') pointerListeners.push(callback); },
      }),
    },
    document: { querySelector: get, querySelectorAll(selector) {
      if (selector !== '.task-answer time') return [];
      const matches = new Set();
      const visit = (node, inAnswer = false) => {
        if (!(node instanceof Element)) return;
        const answer = inAnswer || node.className === 'task-answer';
        if (answer && node.tagName === 'time') matches.add(node);
        node.children.forEach(child => visit(child, answer));
      };
      elements.forEach(node => visit(node));
      return [...matches];
    }, createElement: tag => Object.assign(new Element(), { tagName: tag }), createElementNS: (namespace, tag) => Object.assign(new Element(), { tagName: tag, namespace }), createTextNode: () => new Element(), documentElement: root, body: { dataset: {}, append() {} }, addEventListener: (name, callback, capture) => { (capture === true ? documentCaptureEvents : documentEvents)[name] = callback; }, execCommand: () => execCommandResult },
    navigator: { clipboard: { writeText: async value => { if (clipboardFails) throw new Error('denied'); copied.push(value); } } },
    localStorage: { getItem: key => storage.get(key) || null, setItem: (key, value) => storage.set(key, value) },
    crypto: { randomUUID: () => `${String(++counter).padStart(6, '0')}-0000-4000-8000-000000000000` },
    AbortController,
    atob,
    Blob,
    File,
    FormData: FormDataStub,
    URL,
    setTimeout,
    clearTimeout,
    setInterval: callback => { intervals.push(callback); return 1; },
    clearInterval: () => {},
    fetch: t.mock.fn(async (url, options) => {
      if (pendingWrites.has(url)) await pendingWrites.get(url);
      if (failedWrites.has(url)) return response({ error: 'Write failed' }, false);
      if (url === '/api/state') {
        // A gateway in front of a dead server answers with its own page, not the app's JSON.
        if (stateGatewayBody !== null) return { ok: false, status: 502, statusText: 'Bad Gateway', json: async () => ({}), text: async () => stateGatewayBody };
        const snapshot = stateFails ? response({ error: 'server gone' }, false) : response(state);
        if (stateResponseWait) await stateResponseWait;
        return snapshot;
      }
      if (url === '/api/submissions') return response((state.submissions || []).filter(record => (state.reports || []).some(report => report.id === record.report_id)));
      if (url === '/api/report-sources') return response(state.reportSources || []);
      if (url === '/api/copy-state') {
        // The route serves the save-file text and its counts. The harness projects the same keys.
        if (state.copyStateFails) return response({ error: 'no route' }, false);
        const noteKeys = ['id', 'text', 'at', 'acknowledged_at', 'ack_kind', 'ack_text', 'ack_edited_at', 'replies', 'ack_edited_seen_count', 'seen_at', 'task_id'];
        const taskKeys = ['id', 'title', 'details', 'status', 'order', 'blocked', 'type'];
        const project = (record, keys) => {
          const line = {};
          for (const key of keys) line[key] = record[key] ?? null;
          return line;
        };
        const tasks = state.tasks || {};
        const taskLines = [...(tasks.upcoming || []), ...(tasks.finished || [])];
        const lines = [
          ...(state.notes || []).map(record => project(record, noteKeys)),
          ...taskLines.map(record => project(record, taskKeys)),
          ...(state.submissions || []),
          ...(state.reportSources || []),
        ];
        return response({
          text: lines.map(line => JSON.stringify(line)).join('\n') + '\n',
          counts: { notes: (state.notes || []).length, tasks: taskLines.length,
            answers: (state.submissions || []).length, reports: (state.reportSources || []).length },
          stamp: '2026-10-06T09:00:00',
          repo: 'clankers',
          branch: 'main',
        });
      }
      if (url === '/api/skip-poll') {
        // The press toggles one flag and writes no note, so the harness records the call alone.
        const posted = JSON.parse(options.body || '{}');
        skipCalls.push(posted);
        state.skip_poll = posted.skip === false ? null : '2026-10-06T09:00:00';
        return response({ skip_poll: state.skip_poll });
      }
      if (url === '/api/agent-interrupt') {
        writeHeadersSeen.push(options.headers || {});
        const posted = JSON.parse(options.body);
        interruptCalls.push(posted);
        state.agent_interrupt_pending = posted.armed !== false;
        return response({ agent_interrupt_pending: state.agent_interrupt_pending });
      }
      if (url === '/api/tasks/order') {
        writeHeadersSeen.push(options.headers || {});
        if (orderFails) return { ok: false, status: 503, text: async () => JSON.stringify({ error: 'Write failed' }) };
        const ids = JSON.parse(options.body).ids;
        orderWrites.push(ids);
        // The reply is the stored payload, the same shape the poll returns.
        const drawn = ids.map(id => state.tasks.upcoming.find(task => task.id === id));
        const slots = state.tasks.upcoming
          .map((task, index) => ids.includes(task.id) ? index : -1)
          .filter(index => index >= 0);
        const next = [...state.tasks.upcoming];
        slots.forEach((slot, position) => { next[slot] = drawn[position]; });
        next.forEach((task, index) => { task.order = index + 1; });
        state.tasks = { ...state.tasks, upcoming: next };
        return response(state.tasks);
      }
      if (url === '/api/markdown') return { ok: true, text: async () => '<strong>draft</strong>' };
      if (url === '/api/notes') {
        // Retry tests send notes. There is no save route.
        writeHeadersSeen.push(options.headers || {});
        const note = JSON.parse(options.body);
        sent.push(note);
        return sendHandler(note);
      }
      if (url.startsWith('/api/reports/') && url.endsWith('/submit')) {
        const submission = JSON.parse(options.body);
        sent.push(submission);
        if (reportSubmitWait) await reportSubmitWait;
        if (reportConflict) return {ok:false, status:409, text:async () => JSON.stringify({error:'Report changed. Copy entries before refreshing.'})};
        const at = new Date().toISOString();
        const report = state.reports.find(item => item.id === decodeURIComponent(url.split('/')[3]));
        if (report) Object.assign(report, {
          latest_answer_id: submission.id, latest_answer_at: at, latest_answer_acknowledged_at: null
        });
        return response({ ...submission, at });
      }
      if (url === '/api/notes/with-file') {
        const id = options.body.get('id');
        const text = options.body.get('text');
        const files = options.body.getAll('file');
        uploadCalls.push({ id, text, files, headers: options.headers || {},
          contentType: (options.headers || {})['Content-Type'] });
        let saved = state.notes.find(note => note.id === id);
        if (!saved) {
          const records = files.map((file, index) => {
            const ext = file.name.includes('.') ? file.name.slice(file.name.lastIndexOf('.')) : '';
            const stored = `${id}${files.length > 1 ? `-${index + 1}` : ''}${ext}`;
            return { id: index ? `${id}-file-${index}` : id, note_id: id, name: file.name,
              type: file.type, size: file.size, sha256: 'c'.repeat(64),
              file: stored, path: `uploads/${stored}`, present: true,
              at: '2026-09-21T00:00:00+00:00' };
          });
          saved = { id, text, attachment_name: files[0].name, attachment_path: records[0].path,
            attachments: records, at: '2026-09-21T00:00:00+00:00', acknowledged_at: null };
          state.notes = [...state.notes, saved];
          state.uploads = [...(state.uploads || []), ...records];
        }
        if (loseUploadResponse) { loseUploadResponse = false; throw new Error('response lost after commit'); }
        if (dropUploadOnce) {
          dropUploadOnce = false;
          const drop = new Error('Failed to fetch');
          drop.name = 'TypeError';
          throw drop;
        }
        return response(saved);
      }
      if (url === '/api/fetch-jobs') {
        const payload = JSON.parse(options.body);
        const record = {
          id: `fetch-${queuedCalls.length + 1}`, url: payload.url, allow_proxy: payload.allow_proxy,
          status: 'queued', approval: 'approved', origin: 'owner', source: null, error: null, name: null, size: null, sha256: null,
          file: null, present: false, at: '2026-09-22T00:00:00+00:00'
        };
        queuedCalls.push({ ...record });
        state.fetch_jobs = [record, ...(state.fetch_jobs || [])];
        if (enqueueResponseLost) { enqueueResponseLost = false; throw new Error('response lost after commit'); }
        return response(record);
      }
      if (url === '/api/fetch-jobs/claim') {
        const record = [...state.fetch_jobs].reverse().find(item => item.status === 'queued' && item.approval === 'approved');
        if (!record) return response({ job: null });
        record.status = 'fetching';
        return response({ job: { ...record, claim: `claim-${record.id}` } });
      }
      if (url.startsWith('/api/fetch-jobs/')) {
        const [, , , id, actionWithQuery] = url.split('/');
        const action = actionWithQuery.split('?')[0];
        const record = state.fetch_jobs.find(item => item.id === id);
        if (!record) return response({ error: 'No queued download' }, false);
        if (action === 'approve' || action === 'deny') {
          if (record.approval !== 'pending' || record.status !== 'queued') {
            return response({ error: 'Request already decided' }, false);
          }
          record.approval = action === 'approve' ? 'approved' : 'denied';
          if (action === 'deny') { record.status = 'failed'; record.error = 'Denied in the preview'; }
          return response(record);
        }
        if (action === 'drop') {
          dropped.push({ url, id });
          state.fetch_jobs = state.fetch_jobs.filter(item => item.id !== id);
          return response({ dropped: id });
        }
        if (action === 'result') {
          resultCalls.push({ url, body: options.body, source: options.headers['X-Fetch-Source'] });
          record.status = 'saved';
          record.source = options.headers['X-Fetch-Source'];
          record.name = decodeURIComponent(url.split('?name=')[1]);
          record.type = options.headers['Content-Type'];
          record.size = options.body.size;
          record.sha256 = 'd'.repeat(64);
          record.file = id + (record.name.includes('.') ? record.name.slice(record.name.lastIndexOf('.')) : '');
          record.present = true;
          return response(record);
        }
        if (action === 'fail') {
          record.status = 'failed';
          record.error = JSON.parse(options.body).error;
          failedCalls.push({ id, error: record.error });
        } else if (action === 'retry') {
          if (record.approval !== 'approved') return response({ error: 'Only approved downloads can be retried' }, false);
          record.status = 'queued';
          record.error = null;
        }
        return response(record);
      }
      if (url.startsWith('https://')) {
        remoteCalls.push({ url, options });
        const behavior = remoteBehaviors.get(url) || {};
        if (behavior.error) throw new Error(behavior.error);
        const chunks = behavior.chunks || [Uint8Array.from([1, 2, 3])];
        let position = 0;
        return {
          ok: behavior.ok !== false, status: behavior.status || 200, url: behavior.finalURL || url,
          headers: { get: name => name === 'content-length'
            ? behavior.declared == null ? null : String(behavior.declared)
            : behavior.type || 'application/octet-stream' },
          body: { getReader: () => ({
            read: async () => position < chunks.length ? { done: false, value: chunks[position++] } : { done: true },
            cancel: async () => { behavior.cancelled = true; }
          }) }
        };
      }
      if (url === '/') {
        pageFetches += 1;
        return { ok: true, status: 200, text: async () => '<body></body>' };
      }
      if (url.startsWith('/api/messages/') && url.endsWith('/replies/seen')) {
        if (replySeenWait) await replySeenWait;
        if (replySeenError) return response({ error: 'Write failed' }, false);
        const id = decodeURIComponent(url.split('/')[3]);
        const count = JSON.parse(options.body).count;
        const item = [...(state.notes || []), ...(state.submissions || [])].find(record => record.id === id);
        if (!item) return response({ error: 'Message not found' }, false);
        item.ack_edited_seen_count = Math.max(Number(item.ack_edited_seen_count || 0), count);
        replySeenCalls.push({ url, count });
        return response({ id, ack_edited_seen_count: item.ack_edited_seen_count });
      }
      if (url.startsWith('/api/reports/') && url.endsWith('/seen')) {
        readStamps.push(url);
        if (readStampFailures) { readStampFailures -= 1; return response({ error: 'Read stamp failed' }, false); }
        const id = decodeURIComponent(url.split('/')[3]);
        const seen_at = new Date().toISOString();
        const report = state.reports.find(item => item.id === id);
        if (report) report.seen_at = seen_at;
        return response({ id, seen_at });
      }
      if (url.startsWith('/api/reports/') && url.endsWith('/view')) {
        // An active view stamp preserves an unpublished report while the panel shows it.
        viewCalls.push(url);
        viewActive.push(options && options.body ? JSON.parse(options.body).active : true);
        return response({ id: decodeURIComponent(url.split('/')[3]), viewed_at: new Date().toISOString() });
      }
      if (url.startsWith('/api/reports/') && url.endsWith('/ack-seen')) {
        const id = decodeURIComponent(url.split('/')[3]);
        const stamp = new Date().toISOString();
        ackSeenCalls.push(url);
        if (ackSeenFailures.has(id)) return response({ error: 'Write failed' }, false);
        const report = state.reports.find(item => item.id === id);
        if (report) report.ack_seen_at = stamp;
        return response({ id, ack_seen_at: stamp });
      }
      if (url.startsWith('/api/reports/') && url.endsWith('/unpublish')) {
        const target = decodeURIComponent(url.split('/')[3]);
        unpublished.push(target);
        writeHeadersSeen.push(options.headers || {});
        state.reports = state.reports.filter(report => report.id !== target);
        return response({ unpublished: target });
      }
      if (url.endsWith('/source')) return { ok: true, text: async () => '# Report source\n' };
      const renderedRevision = reportRevision;
      if (reportHtmlWait) await reportHtmlWait;
      return response({
        html: '<h1>Report</h1>', fields: reportFields, revision: renderedRevision,
        published: reportPublished, edited: renderedRevision
      });
    })
  };
  const tick = () => new Promise(resolve => setImmediate(resolve));
  // Use the newest page poll timer after a client reload.
  const refresh = () => intervals.filter(callback => callback.name === 'refreshState').at(-1)();
  const part = (node, cls) => node.children.find(child => (child.className || '') === cls);
  const event = properties => ({ preventDefault() { this.prevented = true; }, ...properties });
  let copyIcon;
  let normalTimeout;
  let finishCopy;
  let body;
  let log;
  let upcomingBody;
  let finishedBody;
  let detailList;
  let currentBody;
  let taskControl;
  let taskDraft;
  let taskCopied;
  let opened;
  let cacheKeyHere;
  let copiedText;
  let lines;
  let cachedHere;
  let projectHere;
  let noteKeysHere;
  let taskKeysHere;
  let taskLines;
  let release;
  let sending;
  let agentReceipt;
  let beforeGrow;
  let stamp;
  let answer;
  let copyButton;
  let fallback;
  let denied;
  let idCode;
  let textInput;
  let text;
  let uiBox;
  let apiBox;
  let boxes;
  let picks;
  let shipBox;
  let otherBox;
  let otherOption;
  let otherText;
  let choices;
  let verdict;
  let beforeDelayedUpdate;
  let releaseHtml;
  let beforeDirtyRefresh;
  let oldRevision;
  let releaseSubmission;
  let beforeSendingUpdate;
  let sendingReport;
  let pip;
  let agentAck;
  let panel;
  let logStamp;
  let messageRow;
  let visibleRows;
  let logView;
  let jump;
  let priorState;
  let stateCopyButton;
  let cacheKey;
  let sendNote;
  let sendsBefore;
  let fetchesBefore;
  let filteredNotes;
  let longReceipt;
  let longId;
  let noteBox;
  let copiedBefore;
  let firstId;
  let selectBefore;
  let picker;
  let chips;
  let tooMany;
  let first;
  let second;
  let thirdFile;
  let dragging;
  let dropping;
  let badDrop;
  let notesBeforeFile;
  let uploadsBeforeFile;
  let sendsBeforeFile;
  let attached;
  let receipt;
  let waitFor;
  let approvalButton;
  let requested;
  let deniedURL;
  let fetchedBeforeDeny;
  let direct;
  let blocked;
  let retryRow;
  let origin;
  let allOrigins;
  let third;
  let allOriginsThird;
  let codeTabs;
  let tooLarge;
  let callsBeforeLimit;
  let resultsBeforeLimit;
  let streamed;
  let streamBehavior;
  let lost;
  let beforeLost;
  let editedNode;
  let renders;
  let replacements;
  let copiesBeforeNotesOnly;

  await t.test("load the client", async () => {
    const probe = new Element();
    const literal = '&lt;script&gt;';
    probe.innerHTML = `<code>${literal.replaceAll('&', '&amp;')}</code>`;
    assert.equal(probe.content.textContent, literal, 'the fixture decodes HTML once without treating text as tags');
    vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../assets/markdown-it.min.js'), 'utf8'), context);
    vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../assets/message-markdown.js'), 'utf8'), context);
    vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../assets/app.js'), 'utf8'), context);
    await tick();
  });

  await t.test("A copy swaps the shape for its check and back, and the theme swaps its own", async () => {
    // The page's icons come from the vendored Lucide table, so each swap redraws from one source.
    copyIcon = get('#copy-state');
    normalTimeout = context.setTimeout;
    finishCopy = undefined;
    context.setTimeout = callback => { finishCopy = callback; };
    await context.copyFrom(copyIcon, 'icon probe', 'state');
    assert.equal(shapeOf(copyIcon).getAttribute('d'), 'M20 6 9 17l-5-5');
    finishCopy();
    const restored = shapeOf(copyIcon);
    assert.equal(restored.tagName, 'rect', 'restore the copy shape when its timer ends');
    assert.equal(restored.getAttribute('width'), '14');
    context.setTimeout = normalTimeout;
    copied.length = 0;

    assert.equal(root.dataset.theme, 'dark');
    assert.equal(shapeOf(get('#theme')).getAttribute('r'), '4', 'the sun lights the dark theme');
    assert.equal(get('#theme').getAttribute('aria-label'), 'Switch to light mode');
    get('#theme').events.click();
    assert.equal(root.dataset.theme, 'light');
    assert.match(shapeOf(get('#theme')).getAttribute('d'), /^M20\.985 12\.486/, 'the moon lights the light theme');
    assert.equal(get('#theme').getAttribute('aria-label'), 'Switch to dark mode');
    assert.equal(storage.get('arena-preview-v1:theme'), 'light');
    body = context.document.body;
    log = get('#history');
    assert.equal(body.dataset.chrome, undefined, 'the closed state is gone');
    assert.equal(get('#chrome').events.click, undefined, 'no collapse handler');
    assert.equal(body.dataset.composer, undefined, 'the composer has no closed state');
    assert.equal(get('#composer-toggle').events.click, undefined, 'no composer handler');
    assert.equal(storage.get('arena-preview-v1:composer'), undefined, 'nothing is saved for it');
  });

  await t.test("An empty field keeps the stylesheet's min-height; a placeholder no longer holds the box op", async () => {
    // An empty field keeps the stylesheet's min-height, independent of its placeholder.
    assert.equal(get('#note').style.height, '');
    get('#note').value = 'a draft';
    // The stub carries the height the browser would measure for three lines.
    get('#note').scrollHeight = 96;
    context.grow();
    const grown = get('#note').style.height;
    assert.ok(px(grown) > 0, 'the box takes its measured height');
    get('#note').value = '**draft**';
    await get('#preview-note').events.click();
    assert.equal(get('#note').hidden, true);
    assert.equal(get('#preview-note').getAttribute('aria-pressed'), 'true');
    assert.equal(get('#preview-note').children[0].getAttribute('class'), 'markdown-icon', 'Markdown preview keeps its brand exception');
    assert.equal(get('#preview-note').children[0].children[0].getAttribute('fill'), 'currentColor', 'the Markdown mark is a filled path');
    assert.equal(get('#preview-note').children.length, 1, 'the markdown mark stands alone');
    assert.equal(get('#draft-preview').innerHTML, '<strong>draft</strong>');
    assert.equal(sent.length, 0);
    await get('#preview-note').events.click();
    assert.equal(get('#note').hidden, false);
    assert.equal(get('#note').value, '**draft**');
    assert.equal(get('#note').style.height, grown, 'the preview toggle keeps the measured height');
    get('#note').scrollHeight = 140;
    get('#note').value = 'keep draft';
    get('#note').events.input();
    assert.ok(px(get('#note').style.height) > px(grown), 'more lines grow the box');
    get('#reports-tab').events.click();
    assert.equal(get('#notes-panel').hidden, true);
    assert.equal(get('#note').value, 'keep draft');
    get('#reports-tab').events.keydown(event({ key: 'ArrowLeft' }));
    assert.equal(get('#notes-panel').hidden, false);
    assert.ok(get('#tasks-status').textContent.trim().length > 0, 'an empty task list says so');
    assert.equal(get('#tasks-finished').hidden, true);
    get('#notes-tab').events.keydown(event({ key: 'ArrowRight' }));
    assert.equal(get('#reports-panel').hidden, false);
    get('#reports-tab').events.keydown(event({ key: 'ArrowRight' }));
    assert.equal(get('#tasks-panel').hidden, false);
    assert.equal(get('#reports-panel').hidden, true);
    get('#tasks-tab').events.keydown(event({ key: 'ArrowRight' }));
    assert.equal(get('#downloads-panel').hidden, false, 'the Downloads panel holds the browser queue');
    assert.equal(get('#tasks-panel').hidden, true);
    get('#downloads-tab').events.keydown(event({ key: 'ArrowRight' }));
    assert.equal(get('#notes-panel').hidden, false);
    get('#notes-tab').events.keydown(event({ key: 'End' }));
    assert.equal(get('#downloads-panel').hidden, false);
    get('#tasks-tab').events.keydown(event({ key: 'Home' }));
    assert.equal(get('#notes-panel').hidden, false);
  });

  await t.test("Switching tabs fires an unawaited refreshState, so flush the queue before a click that", async () => {
    // Switching tabs fires an unawaited refreshState, so flush the queue before a click that
    // has to land: stateBusy swallows a refresh while another is still in flight.
    await new Promise(resolve => setTimeout(resolve, 0));
    state.tasks = {
      finished: [{ type: 'unspecified', id: 'shipped', title: 'Dots in the receipt', details: [], status: 'finished',
        order: 1, updated_at: new Date().toISOString() }],
      upcoming: [
        { type: 'unspecified', id: 'docs-archive', title: '<img onerror=alert(1)> dir',
          details: ['move BUDGET-EXCEPTIONS.md', 'write arena-quirks.md'], status: 'upcoming',
          order: 1, updated_at: new Date().toISOString() },
        { type: 'unspecified', id: 'second', title: 'Second in line', details: ['its only detail'], status: 'upcoming',
          order: 2, updated_at: new Date().toISOString() }
      ],
      updated_at: new Date().toISOString()
    };
    await refresh();
    upcomingBody = get('#tasks-upcoming-body');
    finishedBody = get('#tasks-finished-body');
  });

  await t.test("The head renders in its own div and stays out of Upcoming; the row shape is asserted on", async () => {
    // The second task shows the Upcoming row shape.
    assert.equal(upcomingBody.children.length, 1, 'the head stays out of Upcoming');
    assert.equal(upcomingBody.children[0].tagName, 'li');
    const rowParts = taskParts(upcomingBody.children[0]);
    assert.equal(rowParts[0].textContent, 'Second in line');
    assert.equal(rowParts[0].className, 'task-title');
    assert.equal(rowParts[1].tagName, 'details');
    assert.equal(rowParts[1].children.length, 2, 'a summary and one list');
    assert.equal(rowParts[1].children[0].tagName, 'summary');
    assert.match(rowParts[1].children[0].textContent, /\d+ detail/, 'the summary counts the details');
  });

  await t.test("Canonical task types carry badges and filter legacy missing types as Misc", async () => {
    const prior = state.tasks;
    const filter = get('#task-type-filter');
    const types = ['report', 'code', 'docs', 'ops', 'misc'];
    const stamp = new Date().toISOString();
    try {
      state.tasks = {
        finished: [],
        upcoming: [
          ...types.map((type, index) => ({ id: `new-${type}`, title: `New ${type}`,
            details: [], status: 'upcoming', order: index + 1, type, updated_at: stamp })),
          { id: 'missing-type', title: 'Old task', details: [], status: 'upcoming',
            order: types.length + 1, updated_at: stamp },
        ],
        updated_at: stamp,
      };
      filter.value = 'all';
      filter.events.change();
      await refresh();
      const rows = [...get('#tasks-current-body').children, ...get('#tasks-upcoming-body').children];
      assert.equal(rows.length, state.tasks.upcoming.length);
      for (const row of rows) {
        const task = state.tasks.upcoming.find(item => item.id === row.title);
        const badge = row.children.find(child => child.className === 'task-type');
        assert.ok(badge, 'each task has a visible type');
        assert.equal(badge.dataset.taskType, task.type || 'misc');
      }
      for (const type of types) {
        filter.value = type;
        filter.events.change();
        const visible = [...get('#tasks-current-body').children, ...get('#tasks-upcoming-body').children];
        const expected = state.tasks.upcoming.filter(task => (task.type || 'misc') === type);
        assert.deepEqual(visible.map(row => row.title), expected.map(task => task.id));
      }
    } finally {
      filter.value = 'all';
      filter.events.change();
      state.tasks = prior;
      await refresh();
    }
  });

  await t.test("A typed task carries a type badge and the type filter hides other types", async () => {
    // One primary type per task shows as a badge, and the filter narrows every div.
    const prior = state.tasks;
    state.tasks = {
      finished: [{ id: 'shipped-docs', title: 'Shipped docs', details: [], status: 'finished',
        order: 1, type: 'docs', updated_at: new Date().toISOString() }],
      upcoming: [
        { id: 'code-work', title: 'Code work', details: ['step'], status: 'upcoming',
          order: 1, type: 'code', updated_at: new Date().toISOString() },
        { id: 'docs-work', title: 'Docs work', details: ['step'], status: 'upcoming',
          order: 2, type: 'docs', updated_at: new Date().toISOString() },
        { type: 'unspecified', id: 'legacy-task', title: 'Legacy task', details: [], status: 'upcoming',
          order: 3, updated_at: new Date().toISOString() }
      ],
      updated_at: new Date().toISOString()
    };
    await refresh();
    const head = get('#tasks-current-body').children[0];
    assert.equal(head.title, 'code-work', 'the first open task keeps the seat');
    const headParts = taskParts(head);
    const badge = headParts[1];
    assert.equal(badge.className, 'task-type', 'the badge rides beside the title');
    assert.ok(badge.textContent.trim().length > 0, 'the code row carries a type badge label');
    assert.equal(badge.dataset.taskType, 'code');
    assert.equal(headParts[2].className, 'task-details',
      'the details collapse follows the badge');
    const docsRow = get('#tasks-upcoming-body').children[0];
    assert.equal(taskParts(docsRow)[1].className, 'task-type',
      'a typed row carries its badge beside the title');
    const unspecifiedRow = get('#tasks-upcoming-body').children[1];
    assert.equal(taskParts(unspecifiedRow).length, 1,
      'an Unspecified task keeps only its title outside the handle');
    assert.equal(taskPart(unspecifiedRow, 'task-title').textContent, 'Legacy task');
    assert.equal(unspecifiedRow.children[0].className, 'task-drag-handle');
    const filter = get('#task-type-filter');
    filter.value = 'docs';
    filter.events.change();
    assert.equal(get('#tasks-current-body').children[0].title, 'docs-work',
      'the head follows the filter');
    assert.equal(get('#tasks-upcoming-body').children.length, 0,
      'the filtered list shows the head only in its own div');
    assert.equal(get('#tasks-finished-body').children.length, 1,
      'finished rows filter too');
    filter.value = 'code';
    filter.events.change();
    assert.equal(get('#tasks-finished-body').children.length, 0,
      'a finished row of another type leaves the div');
    filter.value = 'misc';
    filter.events.change();
    assert.equal(get('#tasks-current-body').children[0].title, 'legacy-task',
      'the Unspecified filter finds old rows');
    filter.value = 'all';
    filter.events.change();
    state.tasks = prior;
    await refresh();
  });

  await t.test("A queue drag uses the bullet handle, keeps title text selectable, and restores refused writes", async () => {
    const prior = state.tasks;
    const stamp = new Date().toISOString();
    const settle = () => new Promise(resolve => setTimeout(resolve, 0));
    state.tasks = {
      finished: [],
      upcoming: ['first', 'second', 'third'].map((id, index) => ({
        id, title: `${id} task`, details: [], status: 'upcoming', order: index + 1,
        type: 'code', updated_at: stamp,
      })),
      updated_at: stamp,
    };
    try {
      await refresh();
      const rows = () => [...get('#tasks-current-body').children, ...get('#tasks-upcoming-body').children];
      const dragHandle = row => row.children.find(child => child.className === 'task-drag-handle');
      const taskTitle = row => row.children.find(child => child.className === 'task-title');
      const grab = row => {
        const handle = dragHandle(row);
        handle.events.dragstart(event({
          target: handle, dataTransfer: { setData() {} },
        }));
      };
      assert.deepEqual(rows().map(row => row.title), ['first', 'second', 'third']);
      assert.notEqual(rows()[0].draggable, true, 'the task row leaves text selection to the browser');
      assert.notEqual(taskTitle(rows()[0]).draggable, true, 'the title is not a drag target');
      assert.equal(dragHandle(rows()[0]).draggable, true, 'the bullet handle starts the drag');
      assert.equal(dragHandle(rows()[0]).children[0].getAttribute('class'), 'lucide lucide-dot', 'the Lucide dot is the list bullet');
      assert.equal(rows()[0].dataset.dragHandle, 'true', 'the draggable row hides its native marker');
      assert.equal(rows()[0].events.dragstart, undefined, 'dragstart is not attached to the task row');
      assert.equal(taskTitle(rows()[0]).events.dragstart, undefined, 'dragstart is not attached to the title');
      assert.match(dragHandle(rows()[0]).getAttribute('aria-label'), /reorder/i,
        'the drag handle has an accessible name');
      // A drag previews the seat: the queue redraws in the drawn order, and the seat keeps a mark.
      grab(rows()[2]);
      assert.equal(rows()[2].dataset.dragging, 'true', 'the dragged row reads as dragged');
      const hover = event({ target: rows()[0] });
      rows()[0].events.dragover(hover);
      assert.equal(hover.prevented, true, 'the seat under the pointer takes the drag');
      assert.deepEqual(rows().map(row => row.title), ['third', 'first', 'second'], 'the drag previews the drawn order');
      assert.equal(orderWrites.length, 0, 'a preview writes nothing');
      assert.equal(rows()[0].dataset.dragging, 'true', 'the dragged cue follows the row to its seat');
      assert.equal(rows()[1].dataset.drop, 'true', 'the row below the seat carries the mark');
      // The drop writes the previewed order, and the reply re-renders the rows.
      const drop = event({ target: rows()[0] });
      rows()[0].events.drop(drop);
      assert.equal(drop.prevented, true, 'the drop takes the dragged row');
      dragHandle(rows()[1]).events.dragend(event({ target: dragHandle(rows()[1]) }));
      assert.ok(rows().every(row => row.dataset.dragging === undefined), 'the dragged cue clears');
      assert.ok(rows().every(row => row.dataset.drop === undefined), 'the seat mark clears');
      await settle();
      assert.deepEqual(orderWrites.at(-1), ['third', 'first', 'second'], 'the write carries the drawn order');
      assert.equal(Object.hasOwn(writeHeadersSeen.at(-1), 'X-Preview-Token'), false);
      assert.deepEqual(rows().map(row => row.title), ['third', 'first', 'second'], 'the reply re-renders the rows');
      assert.equal(get('#tasks-current-body').children.length, 1, 'one task keeps the seat');
      // A drag that ends without a drop puts the stored rows back and writes nothing.
      grab(rows()[2]);
      rows()[0].events.dragover(event({ target: rows()[0] }));
      assert.deepEqual(rows().map(row => row.title), ['second', 'third', 'first'], 'a preview moves the row under the hand');
      dragHandle(rows()[0]).events.dragend(event({ target: dragHandle(rows()[0]) }));
      assert.deepEqual(rows().map(row => row.title), ['third', 'first', 'second'], 'a cancelled drag restores the stored rows');
      assert.equal(orderWrites.length, 1, 'a cancelled drag writes nothing');
      // A drop on the list background moves the row to the end of that list.
      const last = rows()[1];
      grab(last);
      const background = event({ target: get('#tasks-upcoming-body') });
      get('#tasks-upcoming-body').events.drop(background);
      dragHandle(last).events.dragend(event({ target: dragHandle(last) }));
      await settle();
      assert.deepEqual(orderWrites.at(-1), ['third', 'second', 'first'], 'the background drop appends the row');
      // A refused write drops the drawn rows and names the failure.
      orderFails = true;
      const refused = rows()[2];
      grab(refused);
      rows()[0].events.drop(event({ target: rows()[0] }));
      dragHandle(refused).events.dragend(event({ target: dragHandle(refused) }));
      await settle();
      assert.deepEqual(rows().map(row => row.title), ['third', 'second', 'first'], 'the refused write keeps the stored rows');
      assert.match(get('#tasks-status').textContent, /not saved/i);
      orderFails = false;
      const end = get('#tasks-upcoming-body');
      assert.doesNotMatch(fs.readFileSync(path.join(__dirname, '../assets/index.html'), 'utf8'),
        /tasks-drop-end/, 'the dedicated end area is removed');
      const beforeEnd = rows().map(row => row.title);
      const moved = rows()[0];
      const expectedEnd = [...beforeEnd.slice(1), beforeEnd[0]];
      grab(moved);
      assert.equal(end.dataset.dragging, 'true', 'a drag keeps the list background reachable');
      rows().at(-1).events.dragover(event({ target: rows().at(-1) }));
      const endHover = event({ target: end });
      end.events.dragover(endHover);
      assert.equal(endHover.prevented, true, 'the end target accepts a task drag');
      assert.deepEqual(rows().map(row => row.title), expectedEnd, 'the end target replaces the earlier row preview');
      assert.equal(end.dataset.drop, 'true', 'the end target marks its seat');
      const rowAgain = rows().at(-2);
      rowAgain.events.dragover(event({ target: rowAgain }));
      assert.equal(end.dataset.drop, undefined, 'a row preview clears the end-target mark');
      end.events.dragover(event({ target: end }));
      end.events.drop(event({ target: end }));
      dragHandle(moved).events.dragend(event({ target: dragHandle(moved) }));
      await settle();
      assert.deepEqual(orderWrites.at(-1), expectedEnd, 'the end target saves the appended order');
      assert.equal(end.dataset.drop, undefined, 'the end-target mark clears after a drop');
      assert.equal(end.dataset.dragging, undefined, 'a completed drag clears the list cue');
      orderFails = true;
      const refusedEnd = rows()[0];
      grab(refusedEnd);
      end.events.dragover(event({ target: end }));
      end.events.drop(event({ target: end }));
      dragHandle(refusedEnd).events.dragend(event({ target: dragHandle(refusedEnd) }));
      await settle();
      assert.deepEqual(rows().map(row => row.title), expectedEnd, 'a refused end drop restores the saved order');
      assert.match(get('#tasks-status').textContent, /not saved/i);
    } finally {
      orderFails = false;
      state.tasks = prior;
      await refresh();
    }
  });

  await t.test("The operations badge reads Ops and a task click lifts a hiding filter", async () => {
    const prior = state.tasks;
    const stamp = new Date().toISOString();
    state.tasks = {
      finished: [],
      upcoming: [
        { id: 'ops-task', title: 'Ops work', details: ['watch the queue'],
          status: 'upcoming', order: 1, type: 'ops', updated_at: stamp },
      ],
      updated_at: stamp,
    };
    await refresh();
    const head = get('#tasks-current-body').children[0];
    assert.ok(taskPart(head, 'task-type').textContent.trim().length > 0,
      'the operations row carries a type badge label');
    const filter = get('#task-type-filter');
    filter.value = 'docs';
    filter.events.change();
    assert.equal(get('#tasks-current-body').children.length, 0,
      'the docs filter hides the operations row');
    const link = new Element();
    link.className = 'ack-reference';
    link.dataset = { referenceType: 'task', referenceId: 'ops-task' };
    documentEvents.click({ target: link, preventDefault: () => {} });
    assert.equal(filter.value, 'ops', 'the click sets the filter to the target type');
    assert.equal(get('#tasks-current-body').children[0].title, 'ops-task',
      'the clicked task row comes into view');
    const search = get('#task-search');
    search.value = 'queue';
    search.events.input();
    assert.equal(get('#tasks-current-body').children.length, 1, 'the detail word keeps the row');
    search.value = 'zzz';
    search.events.input();
    assert.equal(get('#tasks-current-body').children.length, 0, 'a wrong query hides the row');
    documentEvents.click({ target: link, preventDefault: () => {} });
    assert.equal(search.value, '', 'the click clears a query that hides the target');
    assert.equal(get('#tasks-current-body').children[0].title, 'ops-task',
      'the row returns after the query clears');
    filter.value = 'all';
    filter.events.change();
    state.tasks = prior;
    await refresh();
  });

  await t.test("A search input narrows the task rows beside the type filter", async () => {
    const prior = state.tasks;
    const stamp = new Date().toISOString();
    state.tasks = {
      finished: [],
      upcoming: [
        { type: 'unspecified', id: 'alpha-task', title: 'Alpha wiring', details: ['trace the proxy'],
          status: 'upcoming', order: 1, updated_at: stamp },
        { type: 'unspecified', id: 'beta-task', title: 'Beta docs', details: ['rewrite the changelog'],
          status: 'upcoming', order: 2, updated_at: stamp },
      ],
      updated_at: stamp,
    };
    await refresh();
    const search = get('#task-search');
    search.value = 'proxy';
    search.events.input();
    assert.equal(get('#tasks-current-body').children[0].title, 'alpha-task',
      'a detail word matches its task into the head seat');
    assert.equal(get('#tasks-upcoming-body').children.length, 0,
      'the filtered list shows the head only in its own div');
    search.value = 'BETA';
    search.events.input();
    assert.equal(get('#tasks-current-body').children[0].title, 'beta-task',
      'the query reads case-insensitive');
    search.value = 'zzz';
    search.events.input();
    assert.equal(get('#tasks-current-body').children.length, 0, 'no match leaves the head empty');
    assert.equal(get('#tasks-upcoming-body').children.length, 0, 'no match leaves the list empty');
    search.value = '';
    search.events.input();
    assert.equal(get('#tasks-current-body').children[0].title, 'alpha-task',
      'clearing the query restores the head');
    assert.equal(get('#tasks-upcoming-body').children.length, 1,
      'clearing the query restores the rest of the list');
    state.tasks = prior;
    await refresh();
  });

  await t.test('Task search marks literal matches and opens matching detail bodies', async () => {
    const prior = state.tasks;
    const at = new Date().toISOString();
    const search = get('#task-search');
    const hits = node => (node.children || []).flatMap(child => typeof child === 'string' ? []
      : [...(child.className === 'search-hit' ? [child.textContent] : []), ...hits(child)]);
    const head = () => get('#tasks-current-body').children[0];
    const details = () => head().children.find(child => child.className === 'task-details');
    const query = value => { search.value = value; search.events.input(); };
    state.tasks = { updated_at: at, finished: [], upcoming: [
      { id: 'alpha-search', title: 'Alpha wiring', details: ['Trace PROXY; <img onerror=alert(1)> stays text.'],
        type: 'code', status: 'upcoming', order: 1, updated_at: at },
      { id: 'beta-search', title: 'Beta docs', details: ['Read the changelog'],
        type: 'docs', status: 'upcoming', order: 2, updated_at: at,
        answers: [{ id: 'receipt-search', at, text: 'Confirmed support' }] },
    ] };
    const original = JSON.stringify(state.tasks);
    await refresh();
    try {
      query('proxy');
      assert.equal(head().title, 'alpha-search');
      assert.deepEqual(hits(head()), ['PROXY'], 'the visible match preserves its case');
      assert.equal(details().open, true, 'a detail match opens its body');
      const row = head();
      await refresh();
      assert.equal(head(), row, 'an unchanged poll retains the marked row');
      assert.equal(details().open, true, 'an unchanged poll retains the open body');
      query('BETA');
      assert.deepEqual(hits(head()), ['Beta'], 'title matches are marked case-insensitively');
      assert.ok(!details().open, 'a title-only match does not open unrelated details');
      const draft = get('#note').value;
      const title = taskPart(head(), 'task-title');
      const marked = title.children.find(child => child.className === 'search-hit');
      marked.closest = selector => selector.includes('.task-title') ? title : null;
      documentEvents.click({ target: marked, preventDefault() {} });
      assert.match(get('#note').value, /^RE: beta-search/, 'a click on marked title text still quotes its task');
      get('#note').value = draft;
      get('#tasks-tab').events.click();
      query('confirmed');
      assert.equal(head().title, 'beta-search', 'answer text is searchable inside the details');
      assert.deepEqual(hits(head()), ['Confirmed']);
      assert.equal(details().open, true, 'an answer match opens its body');
      query('<img');
      assert.deepEqual(hits(head()), ['<img'], 'markup-shaped input stays literal');
      assert.match(details().textContent, /<img onerror=alert\(1\)>/);
      query('');
      assert.deepEqual(hits(get('#tasks-current-body')), [], 'clearing removes the marks');
      assert.ok(!details().open, 'clearing restores the normal collapsed body');
      assert.equal(JSON.stringify(state.tasks), original, 'search leaves stored task data unchanged');
    } finally {
      query('');
      state.tasks = prior;
      await refresh();
    }
  });

  await t.test("A blocked task never takes the Current seat while another task is open", async () => {
    // A report-linked task sorts first in its group, so the head alone would hand the Current div
    // to work the agent cannot start. The first unblocked task takes the seat instead.
    const saved = state.tasks.upcoming;
    state.tasks.upcoming = [
      { type: 'unspecified', id: 'waits-on-report', title: 'Blocked work', details: ['waits'], status: 'upcoming',
        order: 1, blocked: true, updated_at: new Date().toISOString() },
      { type: 'unspecified', id: 'second', title: 'Second in line', details: ['its only detail'], status: 'upcoming',
        order: 2, updated_at: new Date().toISOString() }
    ];
    await refresh();
    assert.equal(get('#tasks-current-body').children.length, 1);
    assert.equal(get('#tasks-current-body').children[0].title, 'second', 'the open task holds the seat');
    assert.equal(get('#tasks-current').hidden, false);
    // The blocked task still reads in Upcoming, with its muted mark.
    upcomingBody = get('#tasks-upcoming-body');
    assert.equal(upcomingBody.children.length, 1);
    assert.equal(upcomingBody.children[0].title, 'waits-on-report');
    assert.equal(taskPart(upcomingBody.children[0], 'task-title').dataset.blocked, 'true');
    // With every upcoming task blocked, no task reads as current work.
    state.tasks.upcoming[1].blocked = true;
    await refresh();
    assert.equal(get('#tasks-current-body').children.length, 0);
    assert.equal(get('#tasks-current').hidden, true);
    assert.equal(get('#tasks-upcoming-body').children.length, 2);
    // Put the fixture back: the checks below this one read the tasks they left.
    state.tasks.upcoming = saved;
    await refresh();
  });

  await t.test("A blocked task never takes the Current seat while another task is open", async () => {
    // A report-linked task sorts first in its group, so the head alone would hand the Current div
    // to work the agent cannot start. The first unblocked task takes the seat instead.
    const saved = state.tasks.upcoming;
    state.tasks.upcoming = [
      { type: 'unspecified', id: 'waits-on-report', title: 'Blocked work', details: ['waits'], status: 'upcoming',
        order: 1, blocked: true, updated_at: new Date().toISOString() },
      { type: 'unspecified', id: 'second', title: 'Second in line', details: ['its only detail'], status: 'upcoming',
        order: 2, updated_at: new Date().toISOString() }
    ];
    await refresh();
    assert.equal(get('#tasks-current-body').children.length, 1);
    assert.equal(get('#tasks-current-body').children[0].title, 'second', 'the open task holds the seat');
    assert.equal(get('#tasks-current').hidden, false);
    // The blocked task still reads in Upcoming, with its muted mark.
    upcomingBody = get('#tasks-upcoming-body');
    assert.equal(upcomingBody.children.length, 1);
    assert.equal(upcomingBody.children[0].title, 'waits-on-report');
    assert.equal(taskPart(upcomingBody.children[0], 'task-title').dataset.blocked, 'true');
    // With every upcoming task blocked, no task reads as current work.
    state.tasks.upcoming[1].blocked = true;
    await refresh();
    assert.equal(get('#tasks-current-body').children.length, 0);
    assert.equal(get('#tasks-current').hidden, true);
    assert.equal(get('#tasks-upcoming-body').children.length, 2);
    // Put the fixture back: the checks below this one read the tasks they left.
    state.tasks.upcoming = saved;
    await refresh();
  });

  await t.test("The details are a real list, so each carries its own marker rather than being a line in a ", async () => {
    // The details are a real list, so each carries its own marker rather than being a line in a span.
    detailList = taskPart(upcomingBody.children[0], 'task-details').children[1];
    assert.equal(detailList.tagName, 'ul');
    assert.equal(detailList.className, 'task-detail-list');
    assert.equal(detailList.children.length, 1);
    assert.equal(detailList.children[0].tagName, 'li');
    assert.equal(detailList.children[0].className, 'task-detail');
    assert.equal(detailList.children[0].textContent, 'its only detail');
    assert.equal(upcomingBody.children[0].title, 'second', 'hover shows the task ID, not its detail');
    assert.equal(finishedBody.children.length, 1);
    assert.equal(finishedBody.children[0].title, 'shipped');
    assert.equal(finishedBody.children[0].children.length, 1);
    assert.equal(finishedBody.children[0].children[0].textContent, 'Dots in the receipt');
    assert.equal(get('#tasks-finished').hidden, false);
    assert.equal(get('#tasks-upcoming').hidden, false);
  });

  await t.test("Finished tasks keep the backend completion order", async () => {
    const original = state.tasks.finished.slice();
    state.tasks.finished = [
      { type: 'unspecified', id: 'latest', title: 'Latest finished', details: [], status: 'finished', order: 2 },
      ...original
    ];
    await refresh();
    assert.deepEqual(get('#tasks-finished-body').children.map(row => row.title), ['latest', 'shipped']);
    assert.deepEqual(state.tasks.finished.map(task => task.id), ['latest', 'shipped']);
    state.tasks.finished = original;
    await refresh();
  });

  await t.test("A task carries its linked answers in a read-only block below the details", async () => {
    const prior = state.tasks;
    const priorReports = state.reports;
    state.reports = [{ id: 'answer-source', title: 'Source report', updated_at: new Date().toISOString() }];
    state.tasks = {
      finished: [],
      upcoming: [
        { type: 'unspecified', id: 'filler-task', title: 'Filler', details: [], status: 'upcoming',
          order: 1, updated_at: new Date().toISOString() },
        { type: 'unspecified', id: 'answered-task', title: 'Answered work', details: ['read it'], status: 'upcoming',
          order: 2, updated_at: new Date().toISOString(),
          answers: [
            { id: 'answer-abc123', report_id: 'answer-source', at: '2026-10-15T05:44:00.000Z', text: 'Choice? {#pick}\n- (x) yes' },
            { id: 'answer-def456', report_id: 'unpublished-source', at: '2026-10-15T06:00:00.000Z', text: 'Approved: ship it' },
            { id: 'answer-long01', at: '2026-10-15T06:04:00.000Z',
              text: 'Decision recorded for the record: the receipt line keeps every word of the answer text, past the step cap that manual details obey.' },
          ] },
      ],
      updated_at: new Date().toISOString(),
    };
    await refresh();
    const row = get('#tasks-upcoming-body').children[0];
    assert.equal(row.title, 'answered-task', 'the filler holds the head seat');
    const rowParts = taskParts(row);
    assert.equal(rowParts.length, 2, 'the title and details stay beside the drag handle');
    assert.equal(row.children[0].className, 'task-drag-handle');
    assert.equal(row.dataset.dragHandle, 'true');
    const details = taskPart(row, 'task-details');
    assert.equal(details.className, 'task-details');
    assert.ok(!details.open, 'the receipt stays hidden until expanded');
    assert.match(details.children[0].textContent, /\d+ details/,
      'the summary counts the receipt entries beside the manual steps');
    const answers = details.children[2];
    assert.equal(answers.className, 'task-answers');
    assert.equal(answers.children.length, 3, 'one entry per submission ID');
    const long = answers.children[2].children[2];
    assert.ok(long.textContent.length > 120, 'a receipt entry keeps text past the step cap');
    const first = answers.children[0];
    assert.equal(first.className, 'task-answer');
    assert.equal(first.children[0].tagName, 'a', 'a published source makes the submission ID clickable');
    assert.equal(first.children[0].href, '#reports-panel');
    assert.equal(first.children[0].dataset.referenceType, 'report');
    assert.equal(first.children[0].dataset.referenceId, 'answer-source');
    assert.equal(first.children[0].children[0].tagName, 'code', 'the linked ID stays monospace');
    assert.equal(first.children[0].textContent, 'answer-abc123', 'the entry still names the submission');
    assert.equal(answers.children[1].children[0].tagName, 'code', 'an unpublished source leaves a plain ID');
    assert.equal(answers.children[2].children[0].tagName, 'code', 'legacy answers without a source stay plain');
    assert.equal(first.children[1].tagName, 'time');
    assert.equal(first.children[1].textContent, context.time('2026-10-15T05:44:00.000Z'), 'the answer time uses the shared relative clock');
    assert.equal(first.children[1].dateTime, '2026-10-15T05:44:00.000Z', 'the stored UTC stamp stays intact');
    assert.equal(first.children[1].title, context.stampDate('2026-10-15T05:44:00.000Z').toLocaleString(),
      'the tooltip uses the full date in the browser timezone');
    assert.match(first.children[2].textContent, /\(x\) yes/, 'the field and value text shows');
    details.open = true;
    state.reports.push({ id: 'unrelated-source', title: 'Unrelated report', updated_at: new Date().toISOString() });
    await refresh();
    assert.equal(get('#tasks-upcoming-body').children[0], row, 'an unrelated publication does not rebuild task rows');
    assert.equal(details.open, true, 'an unrelated publication keeps open details');
    first.children[1].textContent = 'stale age';
    await refresh();
    assert.equal(get('#tasks-upcoming-body').children[0], row, 'age updates do not rebuild task rows');
    assert.equal(first.children[1].textContent, context.time(first.children[1].dateTime),
      'an unchanged poll refreshes the relative age');
    state.reports = [];
    await refresh();
    const plain = taskPart(get('#tasks-upcoming-body').children[0], 'task-details')
      .children[2].children[0].children[0];
    assert.equal(plain.tagName, 'code', 'unpublishing removes a source link without a task-data change');
    assert.equal(plain.textContent, 'answer-abc123');
    state.reports = [{ id: 'answer-source', title: 'Source report', updated_at: new Date().toISOString() }];
    await refresh();
    const restored = taskPart(get('#tasks-upcoming-body').children[0], 'task-details')
      .children[2].children[0].children[0];
    assert.equal(restored.tagName, 'a', 'republishing restores the source link without a task-data change');
    // The row builds this block from the record, so hostile answer text stays text.
    state.tasks = {
      finished: [],
      upcoming: [
        { type: 'unspecified', id: 'filler-task', title: 'Filler', details: [], status: 'upcoming',
          order: 1, updated_at: new Date().toISOString() },
        { type: 'unspecified', id: 'hostile-task', title: 'Hostile', details: [], status: 'upcoming',
          order: 2, updated_at: new Date().toISOString(),
          answers: [{ id: 'answer-z', at: '2026-10-15T06:10:00.000Z', text: '<img onerror=alert(1)>' }] },
      ],
      updated_at: new Date().toISOString(),
    };
    await refresh();
    const host = get('#tasks-upcoming-body').children[0];
    assert.equal(host.title, 'hostile-task', 'the filler still holds the head seat');
    assert.equal(taskParts(host).length, 2, 'the receipt stays in details beside the bullet handle');
    assert.equal(host.children[0].className, 'task-drag-handle');
    const hostileText = taskPart(host, 'task-details').children[1].children[0].children[2];
    assert.equal(hostileText.tagName, 'p', 'the answer text lands in a paragraph, not markup');
    assert.equal(hostileText.textContent, '<img onerror=alert(1)>', 'answer text stays text');
    state.tasks = prior;
    state.reports = priorReports;
    await refresh();
  });

  await t.test("A hostile title is text in the current row, because a row is built with textContent", async () => {
    currentBody = get('#tasks-current-body');
    assert.equal(get('#tasks-current').hidden, false);
    assert.equal(currentBody.children.length, 1);
    const currentTitle = taskPart(currentBody.children[0], 'task-title');
    assert.equal(currentTitle.textContent, '<img onerror=alert(1)> dir');
    assert.equal(currentTitle.className, 'task-title');
    assert.equal(currentBody.children[0].title, 'docs-archive');
    taskControl = currentTitle;
    assert.equal(taskControl.dataset.taskId, 'docs-archive');
    assert.equal(taskControl.attributes.role, 'button');
    assert.equal(taskControl.tabIndex, 0, 'keyboard users can reach the task ID');
    assert.match(taskControl.getAttribute('aria-label'), /Press Enter to quote it$/,
      'the keyboard label names the quote, not a copy');
    taskDraft = get('#note').value;
    taskCopied = copied.length;
    get('#note').value = 'a draft already written';
    documentEvents.click(event({ target: taskControl }));
    await tick();
    assert.equal(copied.length, taskCopied, 'a click quotes rather than copies');
    assert.equal(get('#note').value, 'RE: docs-archive\n\na draft already written');
    assert.equal(get('#notes-panel').hidden, false, 'quoting a task opens the Notes tab');
    assert.equal(get('#note').focused, true);
    get('#note').value = 'a draft already written';
    documentEvents.keydown(event({ target: taskControl, key: 'Enter' }));
    await tick();
    assert.equal(get('#note').value, 'RE: docs-archive\n\na draft already written',
      'the Enter key quotes like the click does');
    get('#note').value = taskDraft;
    documentEvents.click(event({ target: taskControl, ctrlKey: true }));
    assert.equal(copied.length, taskCopied, 'Ctrl-click quotes without copying');
    assert.equal(get('#note').value, `RE: docs-archive\n\n${taskDraft}`);
    assert.equal(get('#notes-panel').hidden, false, 'quoting a task opens the Notes tab');
    assert.equal(get('#note').focused, true);
    get('#note').value = taskDraft;
    get('#tasks-tab').events.click();
    assert.equal(upcomingBody.children.length, 1, 'the head is removed from Upcoming');
    assert.match(get('#tasks-status').textContent, /^Updated /);
    assert.doesNotMatch(get('#tasks-status').textContent, / · |written by|takes no answers/);
  });

  await t.test("An unchanged poll must not rebuild the rows, or an opened details snaps shut three seconds", async () => {
    // An unchanged poll must not rebuild the rows, or an opened details snaps shut three seconds later.
    opened = taskPart(upcomingBody.children[0], 'task-details');
    opened.open = true;
    const taskData = JSON.stringify(state.tasks);
    get('#tasks-status').textContent = 'stale age';
    await refresh();
    assert.ok(get('#tasks-status').textContent.endsWith(context.time(state.tasks.updated_at)),
      'an unchanged poll refreshes the Updated age');
    assert.equal(JSON.stringify(state.tasks), taskData, 'age refresh does not change task data');
    assert.equal(taskPart(upcomingBody.children[0], 'task-details'), opened,
      'the row survives an unchanged poll');
    assert.equal(opened.open, true);
  });

  await t.test("The log and the tasks lost their own copy buttons", async () => {
    // One button copies notes, tasks, and live answers as restorable NDJSON. Reports keep a separate copy button.
    // Copy only state data. Exclude rendered HTML, tokens, sequences, uploads, and last checks. Poll stamps already use seconds.
    cacheKeyHere = [...storage.keys()].find(key => key.endsWith(':state-cache'));
    assert.ok(cacheKeyHere, 'every poll caches the state the copy button copies');
    state.reports = [{ id: 'r1', title: 'Fielded', updated_at: new Date().toISOString() }];
    state.reportSources = [{ id: 'r1', title: 'Fielded', markdown: '# Fielded\n\nA question.\n', published_at: '2026-09-22T10:00:00' }];
    state.submissions = [{ id: 'ans-1', report_id: 'r1', text: 'REPORT r1: one', at: '2026-09-22T10:00:00',
      acknowledged_at: null, ack_kind: null, ack_text: null, ack_edited_at: null, replies: null, ack_edited_seen_count: 0, seen_at: null, task_id: null }];
    copied.length = 0;
    const statusBeforeCopy = get('#send-status').textContent;
    get('#copy-state').events.click();
    await tick();
    copiedText = copied.at(-1);
    assert.ok(copiedText.endsWith('\n') && !copiedText.endsWith('\n\n'),
      'the state copies as NDJSON with one trailing newline');
    lines = copiedText.slice(0, -1).split('\n').map(line => JSON.parse(line));
    cachedHere = JSON.parse(storage.get(cacheKeyHere));
    projectHere = (record, keys) => {
      const line = {};
      for (const key of keys) line[key] = record[key] ?? null;
      return line;
    };
    noteKeysHere = ['id', 'text', 'at', 'acknowledged_at', 'ack_kind', 'ack_text', 'ack_edited_at', 'replies', 'ack_edited_seen_count', 'seen_at', 'task_id'];
    taskKeysHere = ['id', 'title', 'details', 'status', 'order', 'blocked', 'type'];
    assert.deepEqual(lines, [
      ...cachedHere.notes.map(note => projectHere(note, noteKeysHere)),
      ...(cachedHere.tasks.upcoming || []).map(task => projectHere(task, taskKeysHere)),
      ...(cachedHere.tasks.finished || []).map(task => projectHere(task, taskKeysHere)),
      ...state.submissions,
      ...state.reportSources,
    ], 'the copy carries the note, task, answer and report lines a restore reads, and nothing derived');
    reportLines = lines.filter(line => 'markdown' in line);
    assert.equal(reportLines.length, 1, 'the copy carries the report sources, as the save file does');
    assert.equal(reportLines[0].id, 'r1', 'the report line is the save-file shape from the server');
    // A report line carries a title too, so the task filter excludes it.
    taskLines = lines.filter(line => 'title' in line && !('markdown' in line));
    answerLines = lines.filter(line => 'report_id' in line);
    assert.equal(answerLines.length, 1, 'the copy carries the live report answers');
    assert.equal(answerLines[0].id, 'ans-1', 'the answer line is the save-file shape from the server');
    assert.equal(taskLines.length,
      (cachedHere.tasks.upcoming || []).length + (cachedHere.tasks.finished || []).length,
      'every cached task rides the copy');
    assert.equal(get('#copy-state').dataset.state, 'good', 'the click reports through the button');
    assert.equal(get('#send-status').textContent, statusBeforeCopy,
      'the copy reports through its own control, not the composer');
    state.tasks.upcoming = [];
    await refresh();
    assert.equal(get('#tasks-current').hidden, true, 'an empty queue has no current task');
    assert.equal(currentBody.children.length, 0);
    assert.equal(get('#tasks-upcoming').hidden, false, 'the section still renders, empty');
    delete state.tasks;
    await refresh();
    assert.equal(get('#tasks-finished').hidden, true);
    assert.equal(get('#tasks-current').hidden, true, 'no task list, no current div');
    // The server route answers whatever the poll cached, so the copy needs no client cache.
    storage.delete(cacheKeyHere);
    copied.length = 0;
    get('#copy-state').events.click();
    await tick();
    assert.equal(get('#copy-state').dataset.state, 'good', 'the server export needs no client cache');
    assert.ok(copied.at(-1).endsWith('\n'), 'the copy stays NDJSON with one trailing newline');
    // The userscript owns the save path. The toolbar has a copy control and no save control.
    const pageHtml = fs.readFileSync(path.join(__dirname, '../assets/index.html'), 'utf8');
    assert.ok(!pageHtml.includes('save-state'), 'the page carries no save control');
    assert.ok(
      !fs.readFileSync(path.join(__dirname, '../assets/app.js'), 'utf8').includes('saveState'),
      'the page script wires no save control',
    );

    assert.ok(get('#tasks-status').textContent.trim().length > 0, 'an empty task list says so');
    assert.equal(get('#notes-tab').focused, true);
    get('#note').events.keydown(event({ key: 'Enter', shiftKey: false, isComposing: false }));
    assert.equal(get('#form').submissions, 1);
    get('#note').events.keydown(event({ key: 'Enter', shiftKey: true, isComposing: false }));
    get('#note').events.keydown(event({ key: 'Enter', shiftKey: false, isComposing: true }));
    assert.equal(get('#form').submissions, 1);
    // A touch keyboard has no Shift key, so there the Return key writes the newline and the Send
    // button sends. A mouse-and-keyboard device keeps Enter-to-send.
    coarsePointer = true;
    pointerListeners.forEach(callback => callback({ matches: true }));
    const newline = event({ key: 'Enter', shiftKey: false, isComposing: false });
    get('#note').events.keydown(newline);
    assert.equal(newline.prevented, undefined, 'the browser writes the newline');
    assert.equal(get('#form').submissions, 1, 'no send from the Return key');
    assert.match(get('#send-hint').textContent, /Return: new line/);
    coarsePointer = false;
    pointerListeners.forEach(callback => callback({ matches: false }));
    get('#note').events.keydown(event({ key: 'Enter', shiftKey: false, isComposing: false }));
    assert.equal(get('#form').submissions, 2, 'a desk Enter sends again');
    assert.match(get('#send-hint').textContent, /Enter sends/);
    sendHandler = async () => { throw new Error('response lost'); };
    await get('#form').events.submit(event({}));
    assert.equal(get('#note').value, 'keep draft');
    assert.match(get('#send-status').textContent, /Save not confirmed/);
    sendHandler = async note => response({ ...note, at: new Date().toISOString() });
    get('#note').scrollHeight = 72;
    await get('#form').events.submit(event({}));
    assert.equal(sent[0].id, sent[1].id);
    assert.match(sent[0].id, /^[0-9a-f]{6}$/);
    assert.equal(get('#note').value, '');
  });

  await t.test("A cleared field collapses back to the stylesheet's min-height instead of keeping its grown", async () => {
    // A cleared field collapses back to the stylesheet's min-height instead of keeping its grown size.
    assert.equal(get('#note').style.height, '');
    assert.equal(get('#send-status').textContent, '', 'successful sends do not leave routine footer text');
    assert.equal(get('#note').placeholder, 'keep draft');
    await tick();
    get('#note').value = 'sent while typing';
    release = undefined;
    sendHandler = note => new Promise(resolve => { release = () => resolve(response({ ...note, at: new Date().toISOString() })); });
    sending = get('#form').events.submit(event({}));
    get('#note').value = 'new unsent draft';
    release();
    await sending;
    assert.equal(get('#note').value, 'new unsent draft');
    await tick();
    state = { notes: [{ id: 'one', text: '<img onerror=alert(1)>', at: new Date().toISOString(), acknowledged_at: null }], reports: [], last_check: null };
    await refresh();
    assert.equal(get('#history').children[0].children[0].textContent, '<img onerror=alert(1)>');
    assert.match(get('#history').children[0].children[2].textContent, /^one ·  · (just now|\d+ (mins?|hours?) ago)$/);
  });

  await t.test('Receipt metadata uses a middle dot separator and a state dot', async () => {
    assert.equal(part(get('#history').children[0].children[2], 'receipt-sep').textContent, ' · ');
    assert.equal(part(get('#history').children[0].children[2], 'state-dot').dataset.state, 'sent');
  });

  await t.test("A message that became a task says so, so a line that was read is never mistaken for one th", async () => {
    state.notes[0].task_id = 'dots-in-receipt';
    await refresh();
    assert.match(get('#history').children[0].children[2].textContent, / · Task dots-in-receipt added$/);
    assert.equal(part(get('#history').children[0].children[2], 'receipt-task').title, 'Task dots-in-receipt');
    delete state.notes[0].task_id;
    await refresh();
    assert.equal(part(get('#history').children[0].children[2], 'receipt-task'), undefined,
      'a message with no task carries no marker');
  });

  await t.test("A task receipt links its ID the way an ack reference does, so one click opens the Tasks tab", async () => {
    const priorTasks = state.tasks;
    state.notes[0].task_id = 'linked-receipt';
    state.tasks = {
      upcoming: [{ type: 'unspecified', id: 'linked-receipt', title: 'Linked receipt', details: ['A linked detail'], status: 'upcoming',
        order: 1, updated_at: new Date().toISOString() }],
      finished: [],
      updated_at: new Date().toISOString()
    };
    await refresh();
    try {
      const marker = part(get('#history').children[0].children[2], 'receipt-task');
      const link = part(marker, 'ack-reference');
      assert.ok(link, 'the marker links the task ID');
      assert.equal(link.dataset.referenceType, 'task', 'the link follows the task reference path');
      assert.equal(link.dataset.referenceId, 'linked-receipt');
      assert.equal(link.children[0].textContent, 'linked-receipt', 'the ID rides in a code element');
      assert.match(marker.textContent, / · Task linked-receipt added$/);
      let prevented = false;
      documentEvents.click({ target: link, preventDefault: () => { prevented = true; } });
      assert.equal(prevented, true, 'the receipt link takes the reference path');
      assert.equal(get('#tasks-panel').hidden, false, 'the jump opens the Tasks tab');
      const target = get('#tasks-current-body').children[0];
      assert.equal(target.children.find(child => child.className === 'task-details').open, true,
        'a task-reference link opens its target details');
    } finally {
      delete state.notes[0].task_id;
      state.tasks = priorTasks;
      await refresh();
    }
  });

  await t.test('Message receipts omit the origin tag and retain rendered or raw text', async () => {
    assert.equal(get('#history').children[0].children[0].className, 'raw-message',
      'a message the server sent without rendered HTML keeps the raw class');
    state.notes.push({
      id: 'from-agent',
      text: 'written by the agent',
      html: '<p>written by the agent</p>',
      at: new Date().toISOString(),
      acknowledged_at: null
    });
    await refresh();
    agentReceipt = get('#history').children.at(-1).children[2];
    assert.deepEqual(agentReceipt.children.map(child => child.className).filter(Boolean),
      ['note-id', 'receipt-sep', 'state-dot'],
      'ID, separator, state — no writer tag remains on the receipt');
    assert.equal(get('#history').children.at(-1).children[0].className, 'message-text',
      'rendered message text is named for what it is, not for a report');
  });

  await t.test("The fixture note is this block's own; the tests after it read the log that was there befor", async () => {
    // The fixture note is this block's own. The tests after it read the log that was there before.
    state.notes.pop();
    await refresh();
    assert.equal(part(get('#history').children[0].children[2], 'state-dot').title, 'Sent');
    assert.equal(part(get('#history').children[0].children[2], 'state-dot').attributes['aria-label'], 'Sent');
    assert.equal(part(get('#history').children[0].children[2], 'state-dot').attributes.role, 'img');
    assert.equal(context.clipPlaceholder('one\ntwo\nthree'), 'one\ntwo\nthree');
    assert.equal(context.clipPlaceholder('one\ntwo\nthree\nfour'), 'one\ntwo\n\u2026');
    assert.equal(context.clipPlaceholder('one\ntwo\nthree\nfour\nfive'), 'one\ntwo\n\u2026');
    beforeGrow = get('#note').value;
    get('#note').value = ''; get('#note').scrollHeight = 300; context.grow();
    assert.equal(get('#note').style.height, '');
    get('#note').value = beforeGrow; get('#note').scrollHeight = 96; context.grow();
    assert.doesNotMatch(get('#history').children[0].children[2].textContent,
      /Awaiting|ACK-ed|Saved|Delivered|Sent|Seen|Said| id /);
    assert.equal(get('#last-check').textContent, 'No read or ack yet.');
    stamp = get('#history').children[0].children[2].textContent;
    assert.match(stamp, /(just now|\d+ (mins?|hours?) ago)$/);
    assert.doesNotMatch(stamp, /\d{1,2}:\d{2}/);
    assert.doesNotMatch(stamp, /\b\d{4}\b/);
    assert.doesNotMatch(stamp, /[AP]M/);
  });

  await t.test("A seconds-only stamp is UTC on the wire; the formatter pins it to UTC so a browser", async () => {
    // A seconds-only stamp is UTC on the wire. The formatter pins it to UTC so a browser
    // outside UTC shows the converted local wall clock, not the server's digits.
    {
      const secondsOnly = context.time('2026-06-15T12:00:00');
      const withZone = context.time('2026-06-15T12:00:00Z');
      assert.equal(secondsOnly, withZone,
        'a seconds-only stamp formats as UTC, matching the same instant with a Z');
      const offsetKept = context.time('2026-06-15T12:00:00.000+00:00');
      assert.equal(offsetKept, withZone, 'a stamped offset still parses as UTC');
    }
    assert.equal(get('#history').children[0].children[1].hidden, true);
    assert.match(get('#history').children[0].children[1].className, /^answer/);
    assert.equal(get('#history').children[0].children[2].className, 'receipt');
    state.notes[0].seen_at = new Date().toISOString();
    await refresh();
    assert.equal(part(get('#history').children[0].children[2], 'state-dot').dataset.state, 'seen');
    assert.equal(part(get('#history').children[0].children[2], 'state-dot').title, 'Seen');
    state.notes[0].html = '<p>&lt;img onerror=alert(1)&gt;</p>';
    state.notes[0].acknowledged_at = new Date().toISOString();
    state.last_check = new Date().toISOString();
    await refresh();
    assert.equal(part(get('#history').children[0].children[2], 'state-dot').dataset.state, 'said');
    assert.equal(part(get('#history').children[0].children[2], 'state-dot').title, 'Said');
    assert.match(get('#last-check').textContent, /^Last read\/ack: (just now|\d+ (mins?|hours?) ago)$/);
    assert.equal(get('#history').children[0].children[0].innerHTML, '<p>&lt;img onerror=alert(1)&gt;</p>');
    state.notes[0].ack_kind = 'reply';
    state.notes[0].ack_text = '**done**';
    state.notes[0].ack_html = '<p><strong>done</strong></p>';
    await refresh();
    answer = get('#history').children[0].children[1];
    assert.equal(part(get('#history').children[0].children[2], 'state-dot').dataset.state, 'said');
    assert.equal(part(get('#history').children[0].children[2], 'state-dot').title, 'Said');
    assert.equal(typeof documentEvents.click, 'function');
    copyButton = new Element();
    copyButton.className = 'copy-code';
    copyButton.dataset = { code: 'print(1)' };
    documentEvents.click({ target: copyButton });
    await tick();
    assert.equal(copied.at(-1), 'print(1)');
    assert.equal(shapeOf(copyButton).getAttribute('d'), 'M20 6 9 17l-5-5');
    assert.equal(copyButton.title, 'Copied through the clipboard API');
    assert.equal(copyButton.getAttribute('aria-label'), 'Copied through the clipboard API');
    assert.equal(copyButton.dataset.state, 'good');
    documentEvents.click({ target: new Element() });
    documentEvents.click({});
    clipboardFails = true;
    fallback = new Element();
    fallback.className = 'copy-code';
    fallback.dataset = { code: 'second' };
    documentEvents.click({ target: fallback });
    await tick();
    assert.equal(shapeOf(fallback).getAttribute('d'), 'M20 6 9 17l-5-5');
    assert.equal(fallback.title, 'Copied through the selection fallback');
    execCommandResult = false;
    denied = new Element();
    denied.className = 'copy-code';
    denied.dataset = { code: 'third' };
    documentEvents.click({ target: denied });
    await tick();
    assert.equal(shapeOf(denied).getAttribute('d'), 'M18 6 6 18');
    assert.equal(denied.title, 'Clipboard blocked; select the code and copy it');
    assert.equal(denied.dataset.state, 'bad');
    clipboardFails = false;
  });

  await t.test("The header clock is the log's own face with seconds, so the two cannot disagree", async () => {
    // The header clock is the log's own face with seconds, so the two cannot disagree.
    assert.match(get('#clock').textContent, /^[A-Z][a-z]{2} \d{2}( \d{2})?, \d{2}:\d{2}:\d{2}$/);
    assert.ok(get('#clock').title.length > 3);
  });

  await t.test("A receipt ID quotes on a plain click, with no copy path left on the ID", async () => {
    // A receipt ID quotes on a plain click, with no copy path left on the ID.
    idCode = get('#history').children[0].children[2].children[0];
    assert.equal(idCode.className, 'note-id');
    const draftBeforeQuote = get('#note').value;
    copiedBefore = copied.length;
    get('#note').value = '';
    documentEvents.click({ target: idCode });
    await tick();
    assert.equal(copied.length, copiedBefore, 'the click leaves the clipboard alone');
    assert.equal(get('#note').value, `RE: ${idCode.textContent}\n`);
    assert.equal(get('#note').focused, true);
    assert.equal(idCode.title, `Quoted in the composer: RE: ${idCode.textContent}`);
    get('#note').value = draftBeforeQuote;
    assert.equal(answer.innerHTML, '<p><strong>done</strong></p>');
    assert.match(answer.className, /answer reply message-text/);
    assert.equal(answer.hidden, false);
    state.notes[0].ack_kind = 'note';
    state.notes[0].ack_text = 'rechecked';
    delete state.notes[0].ack_html;
    await refresh();
    assert.equal(answer.textContent, 'rechecked');
    assert.equal(answer.children.length, 1);
    assert.equal(answer.children[0].tagName, 'p');
    assert.match(answer.className, /answer reply/);
    assert.doesNotMatch(answer.className, /note/);
    assert.equal(part(get('#history').children[0].children[2], 'state-dot').dataset.state, 'said');
    assert.equal(part(get('#history').children[0].children[2], 'state-dot').title, 'Said');
    assert.equal(get('#note').value, 'new unsent draft');
    state.notes.push({ id: 'two', text: 'second', at: new Date().toISOString(), acknowledged_at: null });
    log.scrollHeight = 400; log.clientHeight = 200; log.scrollTop = 200;
    await refresh();
    assert.deepEqual(log.children.map(item => item.children[2].children[0].textContent), ['one', 'two']);
    assert.deepEqual(log.children.map(item => item.children[2].children[0].tagName), ['code', 'code']);
    assert.deepEqual(log.children.map(item => part(item.children[2], 'receipt-sep').tagName), ['span', 'span']);
    assert.equal(log.scrollTop, 400);
    log.scrollTop = 0;
    state.notes.push({ id: 'three', text: 'third', at: new Date().toISOString(), acknowledged_at: null });
    await refresh();
    assert.equal(log.scrollTop, 0);
    assert.equal(log.children.at(-1).children[2].children[0].textContent, 'three');
    assert.equal(get('#connection-dot').dataset.state, 'ok');
    assert.equal(get('#connection-dot').getAttribute('aria-label'), 'Connected');
    assert.equal(get('#connection-text').textContent, '3 messages saved');
    stateFails = true;
    await refresh();
    assert.equal(get('#connection-dot').dataset.state, 'down');
    assert.equal(get('#connection-dot').getAttribute('aria-label'), 'Disconnected');
    assert.match(get('#connection-text').textContent, /Connection failed: server gone/);
    // A Cloudflare page for the dead sandbox must not land in the header. The status stands in.
    stateGatewayBody = '<!DOCTYPE html><html class="no-js ie6 oldie" lang="en-US"><head><title>arena-site.site | 502: Bad gateway</title></head><body><h2>Bad gateway</h2><p>Error code 502</p></body></html>';
    await refresh();
    assert.equal(get('#connection-text').textContent, 'Connection failed: HTTP 502 Bad Gateway. Draft kept; history may be stale.');
    // A proxy that answers plain text keeps one short line of it, folded to 120 characters.
    stateGatewayBody = `proxy refused\n${'x'.repeat(200)}`;
    await refresh();
    assert.equal(get('#connection-text').textContent, `Connection failed: proxy refused ${'x'.repeat(106)}. Draft kept; history may be stale.`);
    stateGatewayBody = null;
    stateFails = false;
    await refresh();
    assert.equal(get('#connection-dot').dataset.state, 'ok');
    // A running agent poll turns the dot blue and leaves the text alone.
    state.polling = true;
    await refresh();
    assert.equal(get('#connection-dot').dataset.state, 'polling');
    assert.equal(get('#connection-dot').getAttribute('aria-label'), 'Connected');
    assert.equal(get('#connection-text').textContent, '3 messages saved');
    // A poll start times the wait in the header. The clock ticks between state refreshes.
    state.polling_since = new Date(Date.now() - 65000).toISOString().slice(0, 19);
    await refresh();
    assert.match(get('#connection-text').textContent, /^Polling… 1m 0[5-9]s$/);
    await tick();
    assert.match(get('#connection-text').textContent, /^Polling… 1m 0[5-9]s$/);
    state.waiting_for_gates = true;
    await refresh();
    assert.match(get('#connection-text').textContent, /^Waiting for gates\.\.\. /);
    state.waiting_for_gates = false;
    await refresh();
    assert.match(get('#connection-text').textContent, /^Polling… /);
    // A wait outlives the idle window: the live heartbeat keeps the wait text, not the gone mark.
    state.agent_seen_at = new Date(Date.now() - 600_000).toISOString().slice(0, 19);
    await refresh();
    assert.equal(get('#connection-dot').dataset.state, 'polling');
    assert.match(get('#connection-text').textContent, /^Polling… /);
    assert.doesNotMatch(get('#connection-text').textContent, /Agent 404/);
    state.agent_seen_at = new Date().toISOString().slice(0, 19);
    await refresh();
    state.polling = false;
    state.polling_since = null;
    await refresh();
    assert.equal(get('#connection-text').textContent, '3 messages saved');
    state.agent_seen_at = new Date(Date.now() - 600_000).toISOString().slice(0, 19);
    await refresh();
    assert.equal(get('#connection-dot').dataset.state, 'ok');
    assert.equal(get('#connection-dot').getAttribute('aria-label'), 'Connected');
    assert.equal(get('#connection-text').textContent, '3 messages saved');
    assert.doesNotMatch(get('#connection-text').textContent, /long call/);
    // A poll that returned ends the turn: the mark holds the agent's last act, and three quiet
    // minutes name the agent absent without waiting out the call cap a running bash call needs.
    state.turn_ended_at = new Date(Date.now() - 600_000).toISOString().slice(0, 19);
    await refresh();
    assert.equal(get('#connection-dot').dataset.state, 'idle');
    assert.equal(get('#connection-dot').getAttribute('aria-label'), 'No agent');
    assert.equal(get('#connection-text').textContent, 'Agent 404');
    // A fresh turn end keeps the plain line until the quiet window passes.
    state.turn_ended_at = new Date(Date.now() - 5_000).toISOString().slice(0, 19);
    await refresh();
    assert.equal(get('#connection-dot').dataset.state, 'ok');
    assert.equal(get('#connection-text').textContent, '3 messages saved');
    // The agent's next call clears the mark, and the line names the last call's stamp.
    state.turn_ended_at = null;
    state.agent_call_ended_at = new Date().toISOString().slice(0, 19);
    await refresh();
    assert.match(get('#connection-text').textContent, /^Bash (just now|\d+ (mins?|hours?) ago)$/);
    // The hook stamps every bash call's end, so a call that finished is not a running one,
    // whatever the agent does between calls.
    state.agent_call_ended_at = new Date(Date.now() - 700_000).toISOString().slice(0, 19);
    await refresh();
    await tick();
    assert.match(get('#connection-text').textContent, /^Bash (just now|\d+ (mins?|hours?) ago)$/);
    assert.doesNotMatch(get('#connection-text').textContent, /long call/);
    // A stamp and an absent agent share the line: the call's end, then the gone mark.
    state.agent_seen_at = new Date(Date.now() - 2_000_000).toISOString().slice(0, 19);
    await refresh();
    assert.equal(get('#connection-dot').dataset.state, 'idle');
    assert.equal(get('#connection-dot').getAttribute('aria-label'), 'No agent');
    assert.match(get('#connection-text').textContent,
      /^Bash (just now|\d+ (mins?|hours?) ago) · Agent 404$/);
    state.agent_call_ended_at = null;
    state.agent_seen_at = new Date().toISOString().slice(0, 19);
    await refresh();
    assert.equal(get('#connection-dot').dataset.state, 'ok');
    assert.equal(get('#connection-dot').getAttribute('aria-label'), 'Connected');
    // The header names the calls, singular and plural, and says Blocked once the count reaches
    // ten.
    state.calls_since_message = 9;
    await refresh(); await tick();
    assert.equal(get('#connection-text').textContent, '3 messages saved · 9 calls');
    state.calls_since_message = 10;
    await refresh(); await tick();
    assert.equal(get('#connection-text').textContent, '3 messages saved · Blocked');
    state.calls_since_message = 1;
    await refresh(); await tick();
    assert.equal(get('#connection-text').textContent, '3 messages saved · 1 call');
    // Poll and completed-call status retain the call count.
    state.polling = true;
    state.polling_since = new Date(Date.now() - 65000).toISOString().slice(0, 19);
    await refresh(); await tick();
    assert.match(get('#connection-text').textContent, /^Polling… 1m 0[5-9]s · 1 call$/);
    state.polling = false;
    state.polling_since = null;
    state.agent_call_ended_at = new Date().toISOString().slice(0, 19);
    await refresh(); await tick();
    assert.match(get('#connection-text').textContent, /^Bash (just now|\d+ (mins?|hours?) ago) · 1 call$/);
    state.agent_call_ended_at = null;
    state.agent_seen_at = new Date(Date.now() - 2_000_000).toISOString().slice(0, 19);
    await refresh(); await tick();
    assert.equal(get('#connection-text').textContent, 'Agent 404 · 1 call');
    state.agent_seen_at = new Date().toISOString().slice(0, 19);
    state.calls_since_message = 0;
    await refresh(); await tick();
    assert.equal(get('#connection-dot').dataset.state, 'ok');
    assert.equal(get('#connection-dot').getAttribute('aria-label'), 'Connected');
    state.reports = [{ id: 'r1', title: 'Fielded', updated_at: new Date().toISOString() }];
    reportFields = 3;
    storage.set('arena-preview-v1:answers:r1', JSON.stringify({ answers: { name: 'ada', areas: ['ui'], verdict: 'Other: make it blue' }, at: '2026-09-20T12:00:00.000Z' }));
    textInput = { value: '', className: 'answer-text', scrollHeight: 40, style: {} };
    text = new Element();
    text.dataset = { field: 'name', type: 'text' };
    text.querySelector = selector => selector === 'textarea.answer-text' ? textInput : null;
    text.querySelectorAll = () => [];
    uiBox = { value: 'ui', checked: false, dataset: {} };
    apiBox = { value: 'api', checked: true, dataset: {} };
    boxes = [uiBox, apiBox];
    picks = new Element();
    picks.dataset = { field: 'areas', type: 'checkbox' };
    picks.querySelector = () => null;
    picks.querySelectorAll = selector => (selector === 'input:checked' ? boxes.filter(box => box.checked) : boxes);
    shipBox = { value: 'Ship it', checked: false, dataset: {} };
    otherBox = { value: 'Other: ___', checked: false, dataset: { label: 'Other' } };
    otherOption = {
      querySelector: selector => (selector === '[data-label="Other"]' ? otherBox : null),
      querySelectorAll: selector => (selector === '[data-label]' ? [otherBox] : []),
    };
    otherText = { value: '', dataset: { custom: 'Other' }, className: 'custom-text', scrollHeight: 40, style: {}, parentElement: otherOption };
    choices = [shipBox, otherBox, otherText];
    verdict = new Element();
    verdict.dataset = { field: 'verdict', type: 'choice' };
    verdict.querySelector = selector => (selector === '[data-custom="Other"]' ? otherText : null);
    verdict.querySelectorAll = selector => (selector === 'input:checked' ? choices.filter(box => box.checked) : choices);
    get('#report').fields = [text, picks, verdict];
    get('#report-select').value = 'r1';
    get('#reports-tab').events.click();
    await tick();
    await get('#refresh-report').events.click();
    await tick();
    assert.equal(get('#report').innerHTML, '<h1>Report</h1>');
    assert.equal(get('#report-select').children[0].textContent, '1. Fielded',
      'opening the report clears its unread asterisk');
    assert.equal(get('#report-submit').hidden, false);
    assert.equal(Number(get('#report-form').dataset.fields), reportFields);
    assert.equal(textInput.value, 'ada');
    const restored = textInput.style.height;
    assert.ok(px(restored) > 0, 'restored answers expand the textarea');
    textInput.value = 'first line\nsecond line';
    textInput.scrollHeight = 88;
    documentEvents.input({ target: textInput });
    const grownAnswer = textInput.style.height;
    assert.ok(px(grownAnswer) > px(restored), 'typed lines grow the textarea');
    assert.equal(context.collect(get('#report')).name, 'first line\nsecond line',
      'line breaks survive answer collection');
    textInput.value = 'ada';
    assert.equal(uiBox.checked, true);
    assert.equal(apiBox.checked, false);
    assert.equal(shipBox.checked, false);
    assert.equal(otherBox.checked, true);
    assert.equal(otherText.value, 'make it blue');
    assert.equal(otherText.style.height, restored, 'a one-line slot matches the restored one-line height');
    // A reload must keep the draft: an edit writes the answers before any submit.
    apiBox.checked = true;
    get('#report-form').events.input(event({}));
    assert.deepEqual(JSON.parse(storage.get('arena-preview-v1:answers:r1')).answers,
      { name: 'ada', areas: ['ui', 'api'], verdict: 'Other: make it blue' },
      'a checkbox edit reaches the saved answers');
    apiBox.checked = false;
    get('#report-form').events.input(event({}));
    otherText.value = 'typed in the slot';
    otherText.scrollHeight = 88;
    documentEvents.input({ target: otherText });
    assert.equal(otherBox.checked, true);
    assert.equal(otherText.style.height, grownAnswer, 'the same line count measures the same height');
    documentEvents.input({ target: get('#note') });
    documentEvents.input({});
    otherText.value = '   ';
    documentEvents.input({ target: otherText });
    assert.equal(otherBox.checked, false);
    otherText.value = 'make it blue';
    documentEvents.input({ target: otherText });
    assert.equal(otherBox.checked, true);
    textInput.value = 'ada lovelace';
    apiBox.checked = true;
    otherText.value = '  make it red  ';
    await get('#report-form').events.submit(event({}));
    assert.deepEqual(sent.at(-1).answers, { name: 'ada lovelace', areas: ['ui', 'api'], verdict: 'Other: make it red' });
    assert.equal(sent.at(-1).revision, reportRevision, 'send the revision that supplied the form');
    assert.equal(get('#report-status').children[0].dataset.full, sent.at(-1).id);
    assert.doesNotMatch(get('#report-status').textContent, /Sent |Read |Awaiting ack|Acked /,
      'the stamps live on the receipt, not the status line');
    const sentReceipt = get('#report-agent-ack');
    assert.equal(part(sentReceipt, 'state-dot').dataset.state, 'sent');
    assert.equal(part(sentReceipt, 'state-dot').title, 'Sent');
    assert.ok(sentReceipt.children.at(-1).textContent.length > 0, 'the latest stamp ends the line');
    assert.match(sentReceipt.children[0].title, /^Sent /,
      'the full stamp breakdown waits in the title');
    assert.ok(get('#report-agent-ack').textContent.includes(sent.at(-1).id.slice(0, 6)),
      'show the six-character ID immediately after sending');
    assert.deepEqual(JSON.parse(storage.get('arena-preview-v1:answers:r1')).answers, { name: 'ada lovelace', areas: ['ui', 'api'], verdict: 'Other: make it red' });
    await get('#refresh-report').events.click();
    await tick();
    assert.match(get('#report-status').textContent, /^Published /,
      'a report never republished shows the publish stamp');
    assert.ok(get('#report-status').textContent.includes(sent.at(-1).id.slice(0, 6)));
    assert.doesNotMatch(get('#report-status').textContent, /Sent /);
    // The ID is on show, a click quotes it like a note ID, and a ctrl-click is the same gesture.
    // The quote opens in Messages with a regrown box. A hidden-panel measurement collapsed the textarea and clipped it.
    const reportCode = get('#report-status').children.find(child => child.dataset?.full === 'r1');
    assert.equal(reportCode.textContent, 'r1', 'the loaded report shows its ID');
    assert.equal(reportCode.dataset.full, 'r1');
    const copiedBeforeReport = copied.length;
    get('#note').value = '';
    documentEvents.click({ target: reportCode });
    assert.equal(copied.length, copiedBeforeReport, 'a click on a report ID quotes it, no copy');
    assert.equal(get('#note').value, 'RE: r1\n', 'the report ID lands whole in the composer');
    assert.equal(get('#notes-panel').hidden, false, 'the quote brings the Messages tab up');
    get('#reports-tab').events.click();
    assert.equal(get('#reports-panel').hidden, false, 'the panel comes back for the rest of the flow');
    const reportNote = get('#note');
    reportNote.value = 'a draft already written';
    reportNote.hidden = true;
    get('#draft-preview').hidden = false;
    reportNote.scrollHeight = 96;
    documentEvents.click({ target: reportCode, ctrlKey: true });
    assert.equal(copied.length, copiedBeforeReport, 'a ctrl-click leaves the clipboard alone');
    assert.equal(get('#notes-tab').getAttribute('aria-selected'), 'true',
      'a quoted report ID opens the Messages tab');
    assert.equal(get('#notes-panel').hidden, false);
    assert.equal(get('#reports-panel').hidden, true);
    assert.equal(reportNote.hidden, false, 'the quote leaves the preview for the box');
    assert.equal(get('#draft-preview').hidden, true);
    assert.ok(px(reportNote.style.height) > 0, 'the quote regrows the composer');
    assert.equal(reportNote.value, 'RE: r1\n\na draft already written');
    assert.equal(otherBox.checked, true);
    assert.equal(otherText.value, 'make it red');
    otherText.value = '   ';
    shipBox.checked = true;
    await get('#report-form').events.submit(event({}));
    assert.deepEqual(sent.at(-1).answers, { name: 'ada lovelace', areas: ['ui', 'api'], verdict: 'Ship it' });
    // The status line keeps the report ID visible before and after submission.
    assert.equal(get('#report-status').children.find(child => child.dataset?.quote === 'full').textContent,
      get('#report').dataset.reportId,
      'a sent answer keeps the report ID on show');
    await get('#refresh-report').events.click();
    await tick();
    assert.equal(shipBox.checked, true);
    assert.equal(otherBox.checked, false);
    assert.equal(otherText.value, '');
    shipBox.checked = false;
    await get('#report-form').events.submit(event({}));
    assert.equal('verdict' in sent.at(-1).answers, false);
    beforeDelayedUpdate = get('#report').renders;
    releaseHtml = undefined;
    reportHtmlWait = new Promise(resolve => { releaseHtml = resolve; });
    state.reports[0].updated_at = '2026-09-22T12:00:00';
    await refresh(); await tick();
    textInput.value = 'keep this draft';
    get('#report-form').events.input(event({target:textInput}));
    releaseHtml(); await tick();
    reportHtmlWait = null;
    assert.equal(get('#report').renders, beforeDelayedUpdate, 'keep edits made while an automatic refresh was fetching');
    beforeDirtyRefresh = get('#report').renders;
    oldRevision = reportRevision;
    reportRevision = '2026-09-22T12:00:01.200000+00:00';
    state.reports[0].updated_at = '2026-09-22T12:00:01';
    await refresh(); await tick();
    assert.equal(get('#report').renders, beforeDirtyRefresh, 'automatic updates must keep unsent answers');
    reportConflict = true;
    await get('#report-form').events.submit(event({}));
    assert.equal(sent.at(-1).revision, oldRevision, 'never relabel an old form with the latest poll revision');
    assert.equal(textInput.value, 'keep this draft');
    assert.match(get('#report-status').textContent, /Submission not confirmed.*Report changed/);
    assert.equal(get('#report-submit').disabled, false);
    reportConflict = false;
    await get('#refresh-report').events.click(); await tick();
    assert.equal(get('#report').dataset.revision, reportRevision, 'explicit refresh loads a new revision');
    reportPublished = '2026-09-22T11:59:59.000000+00:00';
    await get('#refresh-report').events.click(); await tick();
    assert.match(get('#report-status').textContent, /Edited /, 'a republished report shows the edit stamp');
    assert.doesNotMatch(get('#report-status').textContent, /Published /,
      'the edit stamp replaces the publish stamp instead of joining it');
    releaseSubmission = undefined;
    reportSubmitWait = new Promise(resolve => { releaseSubmission = resolve; });
    beforeSendingUpdate = get('#report').renders;
    sendingReport = get('#report-form').events.submit(event({}));
    reportRevision = '2026-09-22T12:00:02.300000+00:00';
    state.reports[0].updated_at = '2026-09-22T12:00:02';
    await refresh(); await tick();
    assert.equal(get('#report').renders, beforeSendingUpdate, 'keep a submitted form until its request completes');
    reportConflict = true;
    releaseSubmission(); await sendingReport;
    assert.equal(get('#report').renders, beforeSendingUpdate, 'keep the form when an in-flight request is rejected');
    assert.match(get('#report-status').textContent, /Submission not confirmed.*Report changed/);
    reportSubmitWait = null;
    reportConflict = false;
    reportFields = 0;
    await get('#refresh-report').events.click();
    await tick();
    assert.equal(get('#report-submit').hidden, true);
    get('#notes-tab').events.keydown(event({ key: 'End' }));
    assert.equal(get('#downloads-panel').hidden, false);
    assert.equal(get('#downloads-tab').focused, true);
    assert.equal(get('#downloads-tab').attributes['aria-selected'], 'true');
    get('#tasks-tab').events.keydown(event({ key: 'ArrowLeft' }));
    assert.equal(get('#reports-panel').hidden, false);
    assert.equal(get('#reports-tab').focused, true);
    assert.equal(get('#reports-tab').attributes['aria-selected'], 'true');
  });

  await t.test("The tab carries a pip rather than a count: visible while a report carries no read stamp of", async () => {
    // The tab carries a pip rather than a count: visible while a report carries no read stamp of its
    // own. The stamp lives on the report, so a cleared browser cannot make a read report unread.
    pip = get('#report-pip');
    state.reports = [{ id: 'r1', title: 'Fielded', updated_at: new Date().toISOString(),
      seen_at: '2026-09-20T22:00:00' }];
    await tick();
    await refresh();
    assert.equal(pip.hidden, true, 'a report carrying a read stamp is not unread');
    assert.equal(pip.title, 'No unread reports');
    assert.equal(get('#report-select').children.find(item => item.value === 'r1').textContent,
      '1. Fielded', 'a report already read is not starred in the select');
  });

  await t.test("Unanswered forms add a caret without changing unread markers", async () => {
    // The answer marker is separate from the unread star and pip.
    state.reports[0].needs_answer = true;
    await refresh();
    assert.equal(pip.hidden, true, 'an unanswered form leaves the pip alone');
    assert.equal(get('#report-select').children.find(item => item.value === 'r1').textContent,
      '1. ^ Fielded', 'an unanswered form adds a caret without an unread star');
    state.reports = [{ id: 'r1', title: 'Fielded', updated_at: new Date().toISOString(), seen_at: null }];
    await refresh();
    assert.equal(pip.hidden, false, 'a report with no read stamp is unread');
    assert.equal(pip.title, 'A report is unread');
    assert.equal(pip.attributes['aria-label'], 'Unread report');
    assert.equal(get('#report-select').children.find(item => item.value === 'r1').textContent,
      '1. * Fielded', 'an unseen report is starred in the select');
    assert.equal([...storage.keys()].filter(item => item.endsWith(':reports-read-at')).length, 0,
      'the tab writes no browser marker now that the stamp lives on the report');
    agentAck = get('#report-agent-ack');
    state.reports[0].latest_answer_id = 'a123456-aaaaaaaaaaaaaaaaaaaaaaaaa';
    state.reports[0].latest_answer_at = '2026-09-24T10:00:00';
    state.reports[0].latest_answer_acknowledged_at = null;
    state.reports[0].agent_seen_at = '2026-09-24T10:00:30';
    await refresh();
    assert.equal(agentAck.hidden, false, 'show the latest answer while it awaits a receipt');
    assert.match(agentAck.textContent, /Submission a12345/);
    assert.ok(!agentAck.textContent.includes('a123456-'), 'the UI only shows six ID characters');
    assert.match(agentAck.children[0].title, / · Read \d+ hours ago/,
      'the read stamp waits in the title');
    assert.match(agentAck.textContent, / · \d+ hours ago$/,
      'the line prints the latest stamp after the dot');
    assert.equal(part(agentAck, 'state-dot').dataset.state, 'seen');
    assert.equal(part(agentAck, 'state-dot').title, 'Awaiting ack',
      'the state is a dot and its word lives in the title, as in the log');
    assert.equal(part(agentAck, 'state-dot').attributes['aria-label'], 'Awaiting ack');
    assert.equal(get('#report-agent-ack-footer').textContent, agentAck.textContent);
    assert.equal(get('#report-agent-ack-footer').hidden, agentAck.hidden);
    state.reports[0].latest_answer_acknowledged_at = '2026-09-24T10:01:00';
    await refresh();
    assert.equal(part(agentAck, 'state-dot').dataset.state, 'said');
    assert.match(part(agentAck, 'state-dot').title, /^Acked \d+ hours ago/);
    assert.match(agentAck.textContent, / · \d+ hours ago$/,
      'the ack time is the latest stamp and ends the line');
    assert.equal(get('#report-agent-ack-footer').textContent, agentAck.textContent);
    state.reports[0].acknowledgements = [{ id: 'answer-one', ack_text: 'First', ack_html: '<p>First</p>', acknowledged_at: '2026-09-24T10:01:00', replies: [{text: 'Second', at: '2026-09-24T10:02:00'}] }];
    await refresh();
    assert.equal(get('#report-ack-history').hidden, false);
    assert.equal(get('#report-ack-history').children.length, 2);
    assert.equal(get('#report-ack-history').children[0].innerHTML, '<p>First</p>');
    assert.equal(get('#report-ack-history').children[1].children[0].textContent, 'Second');
    assert.equal(pip.hidden, true, 'the open report body is read independently of its answer receipt');
    state.reports[0].latest_answer_id = 'b765432-bbbbbbbbbbbbbbbbbbbbbbbbb';
    state.reports[0].latest_answer_at = '2026-09-24T10:02:00';
    state.reports[0].latest_answer_acknowledged_at = null;
    await refresh();
    assert.match(agentAck.textContent, /Submission b76543/);
    assert.ok(!agentAck.textContent.includes('a123456'), 'the old submission ID disappears');
    assert.equal(part(agentAck, 'state-dot').title, 'Sent',
      'a previous receipt and an earlier read stamp do not cover a later answer');
    assert.match(agentAck.textContent, / · \d+ hours ago$/,
      'a later answer brings its own stamp');
    state.reports = [];
    await refresh();
    assert.equal(agentAck.hidden, true, 'hide receipts when no report is selected');
    assert.equal(get('#report-agent-ack-footer').hidden, true);
    assert.equal(pip.hidden, true, 'with no reports there is nothing unread');
  });

  await t.test("An edited report keeps its star but drops the pip", async () => {
    // Check edits while Reports is hidden. Opening now stamps the loaded body immediately.
    get('#notes-tab').events.click();
    await tick();
    // An edited report keeps its star but drops the pip: ever_seen remembers the open.
    state.reports = [{ id: 'r1', title: 'Fielded', updated_at: new Date().toISOString(),
      seen_at: null, ever_seen: 1 }];
    await refresh();
    assert.equal(pip.hidden, true, 'an opened report edited later shows no dot');
    assert.equal(get('#report-select').children.find(item => item.value === 'r1').textContent,
      '1. * Fielded', 'the star still marks the changed text');
    state.reports = [{ id: 'r1', title: 'Fielded', updated_at: new Date().toISOString(),
      seen_at: null, ever_seen: 0 }];
    await refresh();
    assert.equal(pip.hidden, false, 'a report nobody opened shows the dot');
  });

  await t.test("An acked answer stars the report and lights the pip until the owner opens it", async () => {
    // An agent ack is unread until the owner opens the report that carries it: the star and the
    // tab dot mark it, and that open writes the stamp which clears both.
    pip = get('#report-pip');
    ackSeenCalls.length = 0;
    state.reports = [{ id: 'r1', title: 'Fielded', updated_at: new Date().toISOString(),
      seen_at: '2026-09-20T22:00:00', ever_seen: 1,
      latest_answer_id: 'a123456-aaaaaaaaaaaaaaaaaaaaaaaaa',
      latest_answer_at: '2026-09-24T10:00:00',
      latest_answer_acknowledged_at: '2026-09-24T10:01:00', ack_seen_at: null }];
    await refresh();
    assert.equal(pip.hidden, false, 'an acked answer nobody opened shows the dot');
    assert.equal(pip.title, 'A report is unread');
    assert.equal(get('#report-select').children.find(item => item.value === 'r1').textContent,
      '1. * Fielded', 'an acked answer stars a report already read once');
    // An earlier subtest already stamped the look, so the count is the throttle's base.
    const viewsBefore = viewCalls.length;
    assert.ok(viewsBefore >= 1, 'an earlier look stamped the view');
    await get('#reports-tab').events.click();
    await tick(); await tick();
    assert.deepEqual(ackSeenCalls, ['/api/reports/r1/ack-seen'],
      'opening the report stamps the ack open once');
    assert.equal(viewCalls.length, viewsBefore + 1,
      'returning to Reports starts a new active look');
    assert.equal(viewActive[viewActive.length - 1], true, 'that stamp counts as an active look');
    assert.equal(pip.hidden, true, 'the dot goes with the open');
    assert.equal(get('#report-select').children.find(item => item.value === 'r1').textContent,
      '1. Fielded', 'the ack star goes with the open');
    await get('#refresh-report').events.click();
    await tick(); await tick();
    assert.deepEqual(ackSeenCalls, ['/api/reports/r1/ack-seen'],
      'a later open stamps nothing, because the ack is already seen');
    assert.equal(viewCalls.length, viewsBefore + 1, 'a refresh on the same report stamps no look');
    // Leaving the report clears the stamp, so a page sitting in the background holds nothing.
    await get('#notes-tab').events.click();
    await tick(); await tick();
    assert.equal(viewCalls.length, viewsBefore + 2, 'leaving the report sends the clear');
    assert.equal(viewActive[viewActive.length - 1], false, 'the clear ends the look');
  });

  await t.test("An ack the owner already opened leaves neither marker", async () => {
    // The open stamp is what clears the mark: an ack whose stamp is at or after its own time is
    // already seen, so the report keeps only the marker its own text carries.
    pip = get('#report-pip');
    state.reports = [{ id: 'r1', title: 'Fielded', updated_at: new Date().toISOString(),
      seen_at: '2026-09-20T22:00:00', ever_seen: 1,
      latest_answer_id: 'a123456-aaaaaaaaaaaaaaaaaaaaaaaaa',
      latest_answer_at: '2026-09-24T10:00:00',
      latest_answer_acknowledged_at: '2026-09-24T10:01:00',
      ack_seen_at: '2026-09-24T10:02:00' }];
    await refresh();
    assert.equal(get('#report-select').children.find(item => item.value === 'r1').textContent,
      '1. Fielded', 'an opened ack leaves no star');
    assert.equal(pip.hidden, true, 'an opened ack leaves no dot');
  });

  await t.test("An ack that lands while the panel shows the report clears at once", async () => {
    // The open is the gesture, and a panel already showing the report is that open: an ack that
    // lands in view must stamp without waiting for a tab switch the owner has no reason to make.
    pip = get('#report-pip');
    ackSeenCalls.length = 0;
    state.reports = [{ id: 'r1', title: 'Fielded', updated_at: '2026-09-24T09:59:00',
      seen_at: '2026-09-20T22:00:00', ever_seen: 1,
      latest_answer_id: 'a123456-aaaaaaaaaaaaaaaaaaaaaaaaa',
      latest_answer_at: '2026-09-24T10:00:00',
      latest_answer_acknowledged_at: null, ack_seen_at: null }];
    await refresh();
    await get('#reports-tab').events.click();
    await tick(); await tick();
    assert.deepEqual(ackSeenCalls, [], 'nothing to stamp before the ack lands');
    state.reports = [{ id: 'r1', title: 'Fielded', updated_at: '2026-09-24T09:59:00',
      seen_at: '2026-09-20T22:00:00', ever_seen: 1,
      latest_answer_id: 'a123456-aaaaaaaaaaaaaaaaaaaaaaaaa',
      latest_answer_at: '2026-09-24T10:00:00',
      latest_answer_acknowledged_at: '2026-09-24T10:01:00', ack_seen_at: null }];
    await refresh();
    await tick(); await tick();
    assert.deepEqual(ackSeenCalls, ['/api/reports/r1/ack-seen'],
      'an ack in view is stamped on the refresh');
    assert.equal(pip.hidden, true, 'the dot goes with the in-view ack');
    assert.equal(get('#report-select').children.find(item => item.value === 'r1').textContent,
      '1. Fielded', 'the star goes with the in-view ack');
  });

  await t.test('Opening a short report stamps it without a dwell', async () => {
    panel = get('#reports-panel');
    state.reports = [{ id: 'r1', title: 'Fits', updated_at: new Date().toISOString(), seen_at: null }];
    readStamps.length = 0;
    panel.scrollHeight = 400; panel.clientHeight = 400; panel.scrollTop = 0;
    get('#reports-tab').events.click();
    await tick(); await tick();
    assert.deepEqual(readStamps, ['/api/reports/r1/seen']);
  });

  await t.test('Opening a long report stamps it before scrolling', async () => {
    state.reports = [{ id: 'r1', title: 'Long', updated_at: new Date().toISOString(), seen_at: null }];
    readStamps.length = 0;
    panel.scrollHeight = 900; panel.clientHeight = 300; panel.scrollTop = 0;
    get('#reports-tab').events.click();
    await tick(); await tick();
    assert.deepEqual(readStamps, ['/api/reports/r1/seen']);
    panel.events.scroll();
    await tick();
    assert.equal(readStamps.length, 1, 'scrolling does not repeat the stamp');
  });

  await t.test('A poll keeps the open report read without another stamp', async () => {
    readStamps.length = 0;
    await refresh();
    await tick(); await tick();
    assert.deepEqual(readStamps, []);
  });

  await t.test('Sending answers preserves the open report read stamp', async () => {
    readStamps.length = 0;
    await get('#report-form').events.submit(event({}));
    await tick();
    assert.deepEqual(readStamps, [], 'a read report needs no duplicate stamp');
  });

  await t.test('A report loaded after leaving the tab stays unread', async () => {
    get('#notes-tab').events.click();
    state.reports = [{ id: 'r1', title: 'Hidden', updated_at: new Date().toISOString(), seen_at: null }];
    readStamps.length = 0;
    await refresh();
    let release;
    reportHtmlWait = new Promise(resolve => { release = resolve; });
    get('#reports-tab').events.click();
    await tick(); await tick();
    assert.deepEqual(readStamps, [], 'pending report HTML is not read');
    get('#notes-tab').events.click();
    reportHtmlWait = null;
    release();
    await tick(); await tick();
    assert.deepEqual(readStamps, [], 'a response after leaving the tab is not read');
    get('#reports-tab').events.click();
    await tick(); await tick();
    assert.deepEqual(readStamps, ['/api/reports/r1/seen']);
    get('#notes-tab').events.click();
    await tick();
    assert.ok(state.reports[0].seen_at, 'leaving retains the completed read');
    get('#reports-tab').events.click();
    await tick(); await tick();
  });

  await t.test('A hidden page does not read automatically, but a submission keeps its fallback', async () => {
    context.document.hidden = true;
    try {
      state.reports = [{ id: 'r1', title: 'Hidden page', updated_at: new Date().toISOString(), seen_at: null }];
      readStamps.length = 0;
      await refresh(); await tick(); await tick();
      assert.deepEqual(readStamps, [], 'a hidden page has no automatic read stamp');
      await get('#report-form').events.submit(event({}));
      await tick();
      assert.deepEqual(readStamps, ['/api/reports/r1/seen'], 'sending answers retains the read fallback');
    } finally { context.document.hidden = false; }
  });

  await t.test('A failed read stamp waits for a later check before retrying', async () => {
    state.reports = [{ id: 'r1', title: 'Retry', updated_at: new Date().toISOString(), seen_at: null }];
    readStamps.length = 0;
    readStampFailures = 1;
    await refresh(); await tick(); await tick();
    assert.equal(readStamps.length, 1, 'the failed write must not loop through its own rollback');
    assert.equal(state.reports[0].seen_at, null);
    await refresh(); await tick(); await tick();
    assert.equal(readStamps.length, 2, 'a later poll retries the failed stamp once');
    assert.ok(state.reports[0].seen_at);
  });

  await t.test('Reading a refreshed report preserves its scroll and clears its star', async () => {
    panel.scrollHeight = 900; panel.clientHeight = 300; panel.scrollTop = 450;
    state.reports = [{ id: 'r1', title: 'Long', updated_at: new Date(Date.now() + 1000).toISOString(), seen_at: null }];
    readStamps.length = 0;
    await refresh();
    await tick(); await tick();
    assert.deepEqual(readStamps, ['/api/reports/r1/seen']);
    assert.equal(panel.scrollTop, 450, 'the stamp does not move the panel');
    assert.equal(get('#report-select').children.find(item => item.value === 'r1').textContent,
      '1. Long', 'the read stamp clears the star');
    assert.equal(pip.hidden, true, 'the pip clears in place');
  });

  await t.test("An update to the report being read keeps the panel where the owner left it, and a report t", async () => {
    panel.scrollHeight = 900; panel.clientHeight = 300; panel.scrollTop = 450;
    state.reports = [
      { id: 'r1', title: 'Long', updated_at: new Date(Date.now() + 1000).toISOString(), seen_at: new Date().toISOString() },
      { id: 'r2', title: 'Other', updated_at: new Date().toISOString(), seen_at: new Date().toISOString() }
    ];
    await refresh();
    await tick(); await tick();
    assert.equal(panel.scrollTop, 450, 'a report update keeps the panel where the owner left it');
    get('#report-select').value = 'r2';
    get('#report-select').events.change();
    await tick(); await tick();
    assert.equal(panel.scrollTop, 0, 'a report the owner switched to starts at its top');
  });

  await t.test("A report the agent unpublished under the owner's eyes stays until they click away", async () => {
    // The agent unpublish lands at once, so the page holds the open report and its row until the
    // owner clicks away rather than emptying the panel under a reader.
    state.reports = [
      { id: 'keep', title: 'Keep', updated_at: new Date().toISOString(), seen_at: new Date().toISOString() },
      { id: 'gone', title: 'Gone', updated_at: new Date().toISOString(), seen_at: new Date().toISOString() }
    ];
    await refresh();
    get('#reports-tab').events.click();
    await tick(); await tick();
    get('#report-select').value = 'gone';
    get('#report-select').events.change();
    await tick(); await tick();
    assert.equal(get('#report').dataset.reportId, 'gone');
    const body = get('#report').innerHTML;
    state.reports = [state.reports[0]];
    await refresh();
    await tick(); await tick();
    assert.equal(get('#report').dataset.reportId, 'gone', 'the body stays under the reader');
    assert.equal(get('#report').innerHTML, body, 'the body is untouched');
    assert.equal(get('#report-select').value, 'gone', 'the row stays');
    assert.match(get('#report-select').children.find(item => item.value === 'gone').textContent, /unpublished/);
    get('#report-select').value = 'keep';
    const options = get('#report-select').children;
    get('#report-select').children = { ...options, length: options.length, [Symbol.iterator]: () => options.values() };
    get('#report-select').events.change();
    await tick(); await tick();
    assert.equal(get('#report').dataset.reportId, 'keep', 'clicking away loads the other report');
    assert.equal(get('#report-select').children.some(item => item.value === 'gone'), false,
      'the held row leaves with the click');
  });

  await t.test('A report deleted outside Reports leaves no held row on return', async () => {
    const previous = state.reports;
    const selected = get('#report-select').value;
    const stamp = new Date().toISOString();
    const remaining = [
      { id: 'answered', title: 'Answered', updated_at: stamp, seen_at: stamp },
      { id: 'first-unanswered', title: 'First unanswered', updated_at: stamp, seen_at: stamp, needs_answer: true },
      { id: 'later-unanswered', title: 'Later unanswered', updated_at: stamp, seen_at: stamp, needs_answer: true }
    ];
    try {
      for (const reports of [remaining, []]) {
        state.reports = [...reports, { id: 'removed-hidden', title: 'Removed', updated_at: stamp, seen_at: stamp }];
        await refresh();
        get('#report-select').value = 'removed-hidden';
        get('#reports-tab').events.click();
        await tick(); await tick();
        get('#notes-tab').events.click();
        state.reports = reports;
        get('#reports-tab').events.click();
        await tick(); await tick();
        const expected = reports.find(report => report.needs_answer)?.id || '';
        assert.equal(get('#report-select').value, expected);
        assert.equal(get('#report-select').children.some(item => item.value === 'removed-hidden'), false);
        if (expected) assert.equal(get('#report').dataset.reportId, expected);
        else assert.equal(get('#report-submit').hidden, true);
      }
    } finally {
      state.reports = previous;
      get('#report-select').value = selected;
      await refresh();
      get('#reports-tab').events.click();
      await tick(); await tick();
    }
  });

  await t.test("The log search narrows the rows by substring and leaves the copy whole", async () => {
    // The log search narrows the rows by substring over each row as it stands, and leaves the copy
    // alone: that is the restore path, and a narrowed copy would restore a partial log.
    logStamp = new Date().toISOString();
    state.notes = [
      { id: 'n1', text: 'alpha', at: logStamp, acknowledged_at: null },
      { id: 'n2', text: 'beta', at: logStamp, seen_at: logStamp, acknowledged_at: null },
      { id: 'n3', text: 'gamma', at: logStamp, seen_at: logStamp, acknowledged_at: logStamp,
        ack_kind: 'note', ack_text: 'delta' }
    ];
    await tick();
    await refresh();
    await tick();
    assert.equal(get('#log-search').value, '', 'the log opens with an empty search');
    messageRow = id => get('#history').children.find(node =>
      node.children[2].children[0].textContent === id);
    visibleRows = () => ['n1', 'n2', 'n3'].filter(id => !messageRow(id).hidden);
    assert.deepEqual(visibleRows(), ['n1', 'n2', 'n3'], 'every message shows with no query');
    assert.equal(get('#log-empty').hidden, true, 'nothing to explain while the search matches');
    assert.equal(messageRow('n1').dataset.state, 'sent');
    assert.equal(messageRow('n2').dataset.state, 'seen');
    assert.equal(messageRow('n3').dataset.state, 'said');
    for (const [query, expected] of [['alp', ['n1']], ['BET', ['n2']], ['delta', ['n3']]]) {
      get('#log-search').value = query;
      get('#log-search').events.input();
      assert.deepEqual(visibleRows(), expected, `the search for ${query} shows its own rows`);
      assert.equal(get('#log-empty').hidden, true);
    }
    get('#log-search').value = 'n2';
    get('#log-search').events.input();
    assert.deepEqual(visibleRows(), ['n2'], 'the ID on the row finds the row');
    get('#log-search').value = '';
    get('#log-search').events.input();
    assert.deepEqual(visibleRows(), ['n1', 'n2', 'n3'], 'clearing the box brings every row back');
    assert.equal([...storage.keys()].some(key => key.endsWith(':log-filter')), false,
      'a search is a look, not a setting, so nothing is remembered');
  });

  await t.test("The search lights the matched text in each row it keeps", async () => {
    // The search lights the matched text in each row it keeps.
    state.notes = [
      { id: 'n1', text: 'Alpha beta alpha', at: logStamp, acknowledged_at: null },
      { id: 'n2', text: 'gamma', at: logStamp, acknowledged_at: null }
    ];
    await refresh();
    await tick();
    get('#log-search').value = 'ALPHA';
    get('#log-search').events.input();
    const text = messageRow('n1').children[0];
    const hits = node => node.children.filter(child =>
      typeof child !== 'string' && child.className === 'search-hit');
    assert.equal(hits(text).length, 2, 'every match wears a mark, whatever the case');
    assert.equal(hits(text)[0].textContent, 'Alpha', 'the mark keeps the row\'s own casing');
    assert.equal(text.textContent, 'Alpha beta alpha', 'the row text stays whole');
    assert.equal(hits(messageRow('n2').children[0]).length, 0,
      'a row without the word wears no mark');
    get('#log-search').value = '';
    get('#log-search').events.input();
    assert.equal(hits(text).length, 0, 'clearing the box takes the marks back');
    assert.equal(text.textContent, 'Alpha beta alpha', 'the text stands as it stood');
  });

  await t.test("The search lights matched text inside a rendered answer too", async () => {
    // A rendered answer keeps its markup, so its words sit in nested elements.
    state.notes = [{ id: 'n1', text: 'plain', at: logStamp, acknowledged_at: null }];
    await refresh();
    await tick();
    const body = messageRow('n1').children[0];
    const inner = new Element();
    inner.children = ['beta alpha'];
    body.children = ['Alpha ', inner];
    get('#log-search').value = 'alpha';
    get('#log-search').events.input();
    const hits = node => node.children.filter(child =>
      typeof child !== 'string' && child.className === 'search-hit');
    assert.equal(hits(inner).length, 1, 'a match inside nested markup wears a mark');
    assert.equal(hits(body).length, 1, 'a match in the top-level text wears a mark too');
    assert.equal(inner.textContent, 'beta alpha', 'the nested text stays whole');
    get('#log-search').value = '';
    get('#log-search').events.input();
    assert.equal(hits(inner).length, 0, 'clearing the box takes the nested marks back');
    assert.equal(inner.textContent, 'beta alpha', 'the nested text stands as it stood');
  });

  await t.test("The search lights a match inside the agent reply block", async () => {
    // The reply block rides on the row, so a query that matches only its words marks them there.
    state.notes = [
      { id: 'n1', text: 'plain', at: logStamp, acknowledged_at: logStamp,
        ack_kind: 'note', ack_text: 'landed the delta fix' }
    ];
    await refresh();
    await tick();
    const answer = messageRow('n1').children[1];
    const hits = node => node.children.filter(child =>
      typeof child !== 'string' && child.className === 'search-hit');
    get('#log-search').value = 'delta';
    get('#log-search').events.input();
    assert.equal(messageRow('n1').hidden, false, 'the row stays while its reply matches');
    assert.equal(hits(answer.children[0]).length, 1, 'the match in the reply wears a mark');
    assert.equal(hits(answer.children[0])[0].textContent, 'delta', 'the mark covers the match');
    assert.equal(answer.children[0].textContent, 'landed the delta fix', 'the reply text stays whole');
    get('#log-search').value = '';
    get('#log-search').events.input();
    assert.equal(hits(answer.children[0]).length, 0, 'clearing the box takes the reply mark back');
    assert.equal(answer.children[0].textContent, 'landed the delta fix', 'the reply stands as it stood');
  });

  await t.test("A longer search re-marks text that a shorter search marked", async () => {
    // The shorter search leaves a mark on "a". The longer search must still mark "alpha" whole.
    state.notes = [{ id: 'n1', text: 'Alpha beta', at: logStamp, acknowledged_at: null }];
    await refresh();
    await tick();
    const body = messageRow('n1').children[0];
    const hits = () => body.children.filter(child =>
      typeof child !== 'string' && child.className === 'search-hit');
    get('#log-search').value = 'a';
    get('#log-search').events.input();
    get('#log-search').value = 'alpha';
    get('#log-search').events.input();
    assert.equal(hits().length, 1, 'the longer query marks its whole match');
    assert.equal(hits()[0].textContent, 'Alpha', 'the mark covers the full match');
    assert.equal(body.textContent, 'Alpha beta', 'the text stands as it stood');
    get('#log-search').value = '';
    get('#log-search').events.input();
  });

  await t.test("A search that matches nothing says so and counts what it is holding back", async () => {
    // A search that matches nothing says so and counts what it is holding back.
    state.notes = [{ id: 'n1', text: 'unread', at: logStamp, acknowledged_at: null }];
    await refresh();
    await tick();
    get('#log-search').value = 'nothing here';
    get('#log-search').events.input();
    assert.equal(messageRow('n1').hidden, true);
    assert.equal(get('#log-empty').hidden, false, 'a search with no match explains itself');
    assert.match(get('#log-empty').textContent, /Nothing here matches "nothing here"/);
  });

  await t.test("The search keeps the log's place: at its end, the log returns there when rows come back,", async () => {
    // The search keeps the log's place: at its end, the log returns there when rows come back,
    // because the browser clamps the scroll while the view is short and never puts it back.
    logView = get('#history');
    state.notes = [
      { id: 'n1', text: 'alpha', at: logStamp, acknowledged_at: null },
      { id: 'n2', text: 'beta', at: logStamp, seen_at: logStamp, acknowledged_at: null }
    ];
    await refresh();
    await tick();
    logView.scrollHeight = 900; logView.clientHeight = 300; logView.scrollTop = 600;
    logView.events.scroll();
    get('#log-search').value = 'alpha';
    get('#log-search').events.input();
    assert.equal(logView.scrollTop, 900, 'a narrowed view with one row sits at its end');
    get('#log-search').value = '';
    get('#log-search').events.input();
    assert.equal(logView.scrollTop, 900, 'the log returns to its end when the rows come back');
    logView.scrollTop = 0;
    logView.events.scroll();
    get('#log-search').value = 'beta';
    get('#log-search').events.input();
    assert.equal(logView.scrollTop, 0, 'a log the owner scrolled away from stays where they left it');
  });

  await t.test("Only the newest twenty messages render, and scrolling up loads the older ones", async () => {
    // The log renders its newest page only, so a long session does not pay for every message
    // at once. Scrolling to the top renders the next older page.
    state = {
      notes: Array.from({ length: 25 }, (_, index) => ({
        id: `page-${index}`,
        text: `message ${index}`,
        at: logStamp,
        acknowledged_at: null,
      })),
      reports: [],
      last_check: null,
    };
    await refresh();
    assert.equal(get('#history').children.length, 20, 'the newest page renders');
    assert.equal(get('#history').children[0].children[0].textContent, 'message 5');
    assert.equal(get('#history').children.at(-1).children[0].textContent, 'message 24');
    logView = get('#history');
    const priorHeight = logView.scrollHeight;
    const priorClient = logView.clientHeight;
    logView.scrollHeight = 500; logView.clientHeight = 100; logView.scrollTop = 0;
    logView.events.scroll();
    assert.equal(get('#history').children.length, 25, 'scrolling up loads the older messages');
    assert.equal(get('#history').children[0].children[0].textContent, 'message 0');
    logView.events.scroll();
    assert.equal(get('#history').children.length, 25, 'a second scroll past the oldest page adds nothing');
    logView.scrollHeight = priorHeight; logView.clientHeight = priorClient;
  });

  await t.test("The jump bar is the remedy for a long log: hidden at the end, shown once the log is scroll", async () => {
    jump = get('#log-newest');
    logView.scrollTop = 900;
    logView.events.scroll();
    assert.equal(jump.hidden, true, 'no bar while the log sits at its end');
    logView.scrollTop = 120;
    logView.events.scroll();
    assert.equal(jump.hidden, false, 'the bar appears once the log is scrolled away from its end');
    jump.events.click();
    assert.equal(logView.scrollTop, 900, 'the bar returns the log to its newest message');
    assert.equal(jump.hidden, true, 'and goes when there is nothing below');
  });

  await t.test("The save button posts the page's cached copy, so a wiped server is refilled from the brows", async () => {
    priorState = state;
    state = {
      notes: [{ id: 'saved', text: 'cached here', at: new Date().toISOString(), acknowledged_at: null }],
      reports: [],
      tasks: { finished: [], upcoming: [{ type: 'unspecified', id: 'saved-task', title: 'Cached here', details: [],
        status: 'upcoming', order: 1, updated_at: new Date().toISOString() }] },
      last_check: null
    };
    await refresh();
    stateCopyButton = get('#copy-state');
    const composerBeforeCopy = get('#send-status').textContent;
    cacheKey = [...storage.keys()].find(item => item.endsWith(':state-cache'));
    assert.ok(cacheKey, 'every successful poll caches the copy the state button copies');
    copied.length = 0;
    stateCopyButton.events.click();
    await tick();
    assert.equal(copied.length, 1, 'the button copies the cache');
    assert.ok(copied[0].split('\n').filter(Boolean).length > 1, 'the copy carries note and task lines');
    assert.equal(get('#send-status').textContent, composerBeforeCopy, 'copy success leaves composer feedback alone');
    assert.equal(stateCopyButton.dataset.state, 'good');
    storage.delete(cacheKey);
    copied.length = 0;
    stateCopyButton.events.click();
    await tick();
    assert.equal(copied.length, 1, 'the server export copies without a client cache');
    assert.equal(get('#send-status').textContent, composerBeforeCopy, 'server export stays in its own control');
    state.copyStateFails = true;
    copied.length = 0;
    stateCopyButton.events.click();
    await new Promise(done => setTimeout(done, 1200));
    await tick();
    assert.equal(copied.length, 0, 'a down route copies nothing');
    assert.equal(get('#send-status').textContent, composerBeforeCopy, 'copy failure leaves composer feedback alone');
    assert.equal(stateCopyButton.dataset.state, 'bad', 'copy failure stays visible on its control');
    delete state.copyStateFails;
  });

  await t.test('Writes after a state refresh carry no page-token header', async () => {
    storage.set(cacheKey, JSON.stringify({ notes: state.notes, tasks: state.tasks }));
    sendHandler = async note => response({ ...note, at: new Date().toISOString() });
    sendNote = async text => {
      get('#note').value = text;
      await get('#form').events.submit(event({}));
    };
    sendsBefore = sent.length;
    fetchesBefore = pageFetches;
    await refresh();
    await sendNote('after the poll');
    assert.equal(pageFetches, fetchesBefore);
    assert.equal(sent.length, sendsBefore + 1);
    assert.equal(Object.hasOwn(writeHeadersSeen.at(-1), 'X-Preview-Token'), false);
  });

  await t.test('HTTP 401 and 403 keep the draft without fetching a replacement page', async () => {
    for (const status of [401, 403]) {
      sendHandler = async () => ({ ok: false, status, text: async () => JSON.stringify({ error: 'Access denied' }) });
      const before = sent.length;
      const pages = pageFetches;
      const draft = `refused ${status}`;
      await sendNote(draft);
      assert.equal(sent.length, before + 1, 'a refused write is not retried');
      assert.equal(pageFetches, pages, 'a refused write does not fetch the page');
      assert.equal(get('#note').value, draft);
    }
    sendHandler = async note => response({ ...note, at: new Date().toISOString() });
  });

  await t.test("The fixture above is this test's own; later tests read the state that was live before it", async () => {
    // The fixture above is this test's own. Later tests read the state that was live before it.
    state = priorState;
    await refresh();
  });

  await t.test("With the search matching nothing, the state copy still carries every message: the cache is", async () => {
    // With the search matching nothing, the state copy still carries every message: the cache is the
    // restore path, and a narrowed copy would restore a partial log as if it were all of it.
    get('#log-search').value = 'nothing here';
    get('#log-search').events.input();
    copied.length = 0;
    get('#copy-state').events.click();
    await tick();
    filteredNotes = copied.at(-1).slice(0, -1).split('\n')
      .map(line => JSON.parse(line)).filter(line => 'title' in line === false);
    assert.ok(filteredNotes.length > 0, 'the fixture holds messages to carry');
    assert.equal(filteredNotes.length, state.notes.length,
      'the state copies whatever the log holds, not the narrowed view of it');
    get('#log-search').value = '';
    get('#log-search').events.input();
    await tick();
    state.notes.push({ id: 'old', text: 'old note', at: '2024-06-15T12:00:00.000Z', acknowledged_at: null });
    await refresh();
    assert.match(get('#history').children.at(-1).children[2].textContent, /(just now|\d+ (mins?|hours?) ago)/);
    state = { notes: [{ id: 'a66700e4-37f0-4182-b782-33c38a83728d', text: 'long id', at: new Date().toISOString(), acknowledged_at: null }], reports: [], last_check: null };
    await refresh();
    longReceipt = get('#history').children.at(-1).children[2];
    assert.equal(longReceipt.children[0].textContent, 'a66700');
    assert.equal(longReceipt.children[0].title, 'a66700e4-37f0-4182-b782-33c38a83728d');
    assert.equal(longReceipt.children[0].className, 'note-id');
    assert.doesNotMatch(longReceipt.textContent, /a66700e4-37f0/);
    assert.match(longReceipt.textContent, /^a66700 ·  · (just now|\d+ (mins?|hours?) ago)$/);
    assert.equal(part(longReceipt, 'state-dot').dataset.state, 'sent');
  });

  await t.test("A plain click quotes the six characters on show; no copy gesture is left on an ID", async () => {
    // A plain click quotes the short ID and leaves the clipboard unchanged.
    longId = longReceipt.children[0];
    assert.notEqual(longId.textContent, longId.dataset.full);
    get('#note').value = '';
    copiedBefore = copied.length;
    documentEvents.click({ target: longId });
    assert.equal(copied.length, copiedBefore, 'the click leaves the clipboard alone');
    assert.equal(get('#note').value, `RE: ${longId.textContent}\n`);
    assert.equal(longId.title, `Quoted in the composer: RE: ${longId.textContent}`);
    get('#note').value = '';
    documentEvents.click({ target: longId, shiftKey: true });
    assert.equal(copied.length, copiedBefore, 'a shift-click copies nothing either');
    assert.equal(get('#note').value, `RE: ${longId.textContent}\n`);
  });

  await t.test("A ctrl-click quotes instead of copying: the composer takes a `RE: <shortid>` line, the car", async () => {
    noteBox = get('#note');
    noteBox.value = 'a draft already written';
    noteBox.hidden = true;
    copiedBefore = copied.length;
    documentEvents.click({ target: longId, ctrlKey: true });
    assert.equal(copied.length, copiedBefore, 'a ctrl-click leaves the clipboard alone');
    assert.equal(noteBox.value, `RE: ${longId.textContent}\n\na draft already written`);
    assert.equal(noteBox.hidden, false, 'quoting opens a hidden composer');
    assert.equal(noteBox.focused, true);
    assert.equal(noteBox.selectionStart, 11, 'the caret sits after the quote line');
    assert.equal(noteBox.selectionEnd, 11);
    assert.equal(longId.title, `Quoted in the composer: RE: ${longId.textContent}`);
    assert.equal(storage.get([...storage.keys()].find(item => item.endsWith(':draft'))), noteBox.value,
      'the quote is kept as the draft');
  });

  await t.test("An empty composer gets exactly the line that was asked for, and quoting a second note", async () => {
    noteBox.value = '';
    firstId = get('#history').children[0].children[2].children[0];
    documentEvents.click({ target: firstId, ctrlKey: true });
    assert.equal(noteBox.value, `RE: ${firstId.textContent}\n`);
    noteBox.value = `RE: ${firstId.textContent}\nsomething typed after it`;
    documentEvents.click({ target: longId, ctrlKey: true });
    assert.equal(noteBox.value, `RE: ${longId.textContent}\n\nsomething typed after it`);
  });

  await t.test("Meta is the same gesture on a Mac, and a plain click is the same quote", async () => {
    // Meta is the same gesture on a Mac, and a plain click is the same quote. Neither touches the
    // clipboard any more.
    noteBox.value = '';
    copiedBefore = copied.length;
    documentEvents.click({ target: longId, metaKey: true });
    assert.equal(noteBox.value, `RE: ${longId.textContent}\n`);
    noteBox.value = '';
    documentEvents.click({ target: longId });
    assert.equal(copied.length, copiedBefore, 'a plain click copies nothing');
    assert.equal(noteBox.value, `RE: ${longId.textContent}\n`, 'a plain click quotes');
  });

  await t.test("A report ID quotes whole: its six-character prefix names nothing", async () => {
    // The log's notes answer to six characters. A report ID is a slug, so the quote carries the
    // whole of it.
    context.reportStatusLine('Report · 3 fields', 'summarize-stops');
    const code = part(get('#report-status'), 'note-id');
    assert.equal(code.dataset.quote, 'full');
    get('#note').value = '';
    documentEvents.click({ target: code, ctrlKey: true });
    assert.equal(get('#note').value, 'RE: summarize-stops\n');
    assert.equal(get('#note').hidden, false, 'the quote opens the composer');
    const answer = 'abcdef012345';
    const report = 'summarize-stops';
    context.reportStatusLine('', report, answer);
    const codes = get('#report-status').children.filter(child => child.className === 'note-id');
    assert.deepEqual(codes.map(code => code.dataset.full), [answer, report]);
    const answerCode = codes.find(code => code.dataset.full === answer);
    assert.equal(answerCode.textContent, answer.slice(0, 6));
    get('#note').value = '';
    documentEvents.click({ target: answerCode });
    assert.equal(get('#note').value, `RE: ${answerCode.textContent}\n`);
  });

  await t.test("The report copies its Markdown source, fetched on the click rather than riding the poll", async () => {
    // The report copies its Markdown source, fetched on the click rather than riding the poll.
    selectBefore = get('#report-select').value;
    get('#report-select').value = 'p1';
    get('#copy-report').events.click();
    await tick();
    assert.equal(copied.at(-1), '# Report source\n');
    assert.equal(get('#copy-report').dataset.state, 'good');
    get('#report-select').value = '';
    get('#copy-report').events.click();
    await tick();
    assert.equal(get('#copy-report').dataset.state, 'bad');
    assert.equal(get('#copy-report').title, 'There is no report to copy');
    get('#report-select').value = selectBefore;
  });

  await t.test('Report labels keep unseen stars and mark unanswered forms independently', async () => {
    const keptReports = state.reports;
    const keptSelect = get('#report-select').value;
    get('#notes-tab').events.click();
    const stamp = '2026-09-25T10:00:00';
    try {
      state.reports = [
        { id: 'unseen-wait', title: 'Waiting', updated_at: stamp, seen_at: null, needs_answer: true },
        { id: 'seen-wait', title: 'Seen waiting', updated_at: stamp, seen_at: stamp, needs_answer: true },
        { id: 'unseen-done', title: 'Answered', updated_at: stamp, seen_at: null, needs_answer: false },
        { id: 'seen-done', title: 'Read', updated_at: stamp, seen_at: stamp, needs_answer: false },
        { id: 'new-ack', title: 'New ack', updated_at: stamp, seen_at: stamp, needs_answer: false,
          latest_answer_acknowledged_at: '2026-09-25T10:01:00', ack_seen_at: null }
      ];
      await refresh();
      assert.deepEqual(get('#report-select').children.map(option => option.textContent), [
        '1. *^ Waiting', '2. ^ Seen waiting', '3. * Answered', '4. Read', '5. * New ack'
      ]);
      state.reports[1].needs_answer = false;
      await refresh();
      assert.equal(get('#report-select').children[1].textContent, '2. Seen waiting');
    } finally {
      state.reports = keptReports;
      await refresh();
      get('#report-select').value = keptSelect;
    }
  });

  await t.test('The bulk eye marks only unread report acknowledgements once', async () => {
    const keptReports = state.reports;
    const notesBefore = JSON.stringify(state.notes);
    get('#notes-tab').events.click();
    state.reports = [
      { id: 'ack-one', title: 'One', updated_at: '2026-09-25T10:00:00', seen_at: null,
        latest_answer_acknowledged_at: '2026-09-25T10:01:00', ack_seen_at: null },
      { id: 'ack-two', title: 'Two', updated_at: '2026-09-25T10:00:00', seen_at: null,
        latest_answer_acknowledged_at: '2026-09-25T10:02:00', ack_seen_at: null },
      { id: 'ack-old', title: 'Old', updated_at: '2026-09-25T10:00:00', seen_at: null,
        latest_answer_acknowledged_at: '2026-09-25T10:01:00', ack_seen_at: '2026-09-25T10:03:00' }
    ];
    await refresh();
    const readCount = readStamps.length;
    const replyCount = replySeenCalls.length;
    ackSeenCalls.length = 0;
    try {
      const button = get('#mark-report-acks-seen');
      assert.equal(typeof button.events.click, 'function');
      const first = button.events.click();
      const repeated = button.events.click();
      await Promise.all([first, repeated]);
      assert.deepEqual(ackSeenCalls.slice().sort(), [
        '/api/reports/ack-one/ack-seen', '/api/reports/ack-two/ack-seen'
      ]);
      await button.events.click();
      assert.equal(ackSeenCalls.length, 2, 'seen acknowledgements need no second write');
      assert.equal(readStamps.length, readCount, 'report bodies stay unread');
      assert.equal(replySeenCalls.length, replyCount, 'message replies stay unchanged');
      assert.equal(JSON.stringify(state.notes), notesBefore);
      assert.ok(state.reports.every(report => report.seen_at === null));
      const html = fs.readFileSync(path.join(__dirname, '../assets/index.html'), 'utf8');
      assert.match(html, /<button[^>]+id="mark-report-acks-seen"/);
      state.reports = [{ id: 'ack-failed', title: 'Retry', updated_at: '2026-09-25T10:00:00',
        seen_at: null, latest_answer_acknowledged_at: '2026-09-25T10:01:00', ack_seen_at: null }];
      ackSeenFailures.add('ack-failed');
      await refresh();
      await button.events.click();
      assert.equal(button.disabled, false, 'a failed acknowledgement remains available to retry');
      ackSeenFailures.clear();
      await button.events.click();
      assert.equal(button.disabled, true, 'success clears the remaining acknowledgement');
      assert.equal(readStamps.length, readCount);
      assert.equal(replySeenCalls.length, replyCount);
    } finally {
      ackSeenFailures.clear();
      state.reports = keptReports;
      ackSeenCalls.length = 0;
      await refresh();
    }
  });

  await t.test('Next report follows list order and stops at the last report', async () => {
    const keptReports = state.reports;
    const keptSelect = get('#report-select').value;
    get('#notes-tab').events.click();
    try {
      state.reports = ['next-one', 'next-two', 'next-three'].map(id => ({
        id, title: id, updated_at: '2026-09-25T10:00:00', seen_at: '2026-09-25T10:01:00'
      }));
      await refresh();
      get('#report-select').value = 'next-one';
      get('#reports-tab').events.click();
      await tick(); await tick();
      const button = get('#next-report');
      assert.equal(typeof button.events.click, 'function');
      assert.equal(button.disabled, false);
      await button.events.click();
      await tick();
      assert.equal(get('#report-select').value, 'next-two');
      assert.equal(get('#report').dataset.reportId, 'next-two');
      await button.events.click();
      await tick();
      assert.equal(get('#report-select').value, 'next-three');
      assert.equal(button.disabled, true);
      await button.events.click();
      assert.equal(get('#report-select').value, 'next-three', 'the last report must not wrap');
      state.reports = [];
      await refresh();
      assert.equal(button.disabled, true);
    } finally {
      get('#notes-tab').events.click();
      state.reports = keptReports;
      await refresh();
      get('#report-select').value = keptSelect;
    }
  });

  await t.test('Explicit jump scrolling follows the current reduced-motion preference', () => {
    const history = get('#history');
    const panel = get('#reports-panel');
    const saved = [history.scrollTop, history.scrollHeight, panel.scrollTop, history.lastScroll, panel.lastScroll];
    try {
      for (const reduce of [false, true]) {
        reducedMotion = reduce;
        history.scrollHeight = Math.max(history.scrollHeight, history.clientHeight + 1);
        get('#log-newest').events.click();
        assert.ok(history.lastScroll, 'the explicit jump must use the native scrolling API');
        assert.equal(history.lastScroll.top, history.scrollHeight);
        assert.equal(history.lastScroll.behavior, reduce ? 'auto' : 'smooth');
        get('#report-top').events.click();
        assert.ok(panel.lastScroll);
        assert.equal(panel.lastScroll.top, 0);
        assert.equal(panel.lastScroll.behavior, reduce ? 'auto' : 'smooth');
      }
    } finally {
      reducedMotion = false;
      [history.scrollTop, history.scrollHeight, panel.scrollTop, history.lastScroll, panel.lastScroll] = saved;
    }
  });

  await t.test('The floating report control returns to top and hides outside Reports', async () => {
    const keptReports = state.reports;
    const keptSelect = get('#report-select').value;
    const panel = get('#reports-panel');
    try {
      get('#notes-tab').events.click();
      state.reports = [{ id: 'scroll-report', title: 'Scroll', updated_at: '2026-09-25T10:00:00',
        seen_at: '2026-09-25T10:01:00' }];
      await refresh();
      get('#report-select').value = 'scroll-report';
      get('#reports-tab').events.click();
      await tick(); await tick();
      const button = get('#report-top');
      assert.equal(typeof button.events.click, 'function');
      panel.scrollHeight = 1000;
      panel.clientHeight = 200;
      panel.scrollTop = 240;
      panel.events.scroll();
      assert.equal(button.hidden, false);
      button.events.click();
      assert.equal(panel.scrollTop, 0);
      assert.equal(button.hidden, true);
      panel.scrollTop = 120;
      panel.events.scroll();
      get('#notes-tab').events.click();
      assert.equal(button.hidden, true);
      assert.equal(get('#reports-scroll-frame').hidden, true);
      get('#reports-tab').events.click();
      await tick(); await tick();
      assert.equal(get('#reports-scroll-frame').hidden, false);
      const html = fs.readFileSync(path.join(__dirname, '../assets/index.html'), 'utf8');
      assert.match(html, /id="report-top"[^>]*class="log-newest"/);
      assert.match(html, /<section id="reports-panel"[\s\S]*?<\/section>\s*<button id="report-top"/);
    } finally {
      get('#notes-tab').events.click();
      state.reports = keptReports;
      panel.scrollTop = 0;
      await refresh();
      get('#report-select').value = keptSelect;
    }
  });

  await t.test("A report delete asks for a second click against the same selection, then leaves the tab", async () => {
    const keptReports = state.reports;
    const keptSelect = get('#report-select').value;
    state.reports = [
      { id: 'keep1', title: 'Keep', updated_at: '2026-09-25T10:00:00', seen_at: '2026-09-25T10:05:00' },
      { id: 'gone1', title: 'Gone', updated_at: '2026-09-25T10:00:00', seen_at: null }
    ];
    get('#reports-tab').events.click();
    await new Promise(resolve => setTimeout(resolve, 0));
    get('#report-select').value = 'gone1';
    get('#report-select').events.change();
    await tick();
    assert.equal(get('#report').dataset.reportId, get('#report-select').value);
    const statusLines = [];
    const statusNode = get('#report-status');
    const nodeProto = Object.getPrototypeOf(statusNode);
    const textAccessor = Object.getOwnPropertyDescriptor(nodeProto, 'textContent');
    Object.defineProperty(statusNode, 'textContent', {
      set(value) { statusLines.push(value); textAccessor.set.call(this, value); },
      get() { return textAccessor.get.call(this); }
    });
    get('#unpublish-report').events.click();
    await tick();
    assert.deepEqual(unpublished, [], 'the first click only arms');
    assert.equal(get('#unpublish-report').dataset.state, 'bad', 'the armed button reads danger');
    assert.match(get('#report-status').textContent, /Click ✕ again to confirm/);
    get('#report-select').value = 'keep1';
    get('#report-select').events.change();
    await tick();
    get('#unpublish-report').events.click();
    await tick();
    assert.deepEqual(unpublished, [], 'a click against another selection re-arms instead of deleting');
    get('#report-select').value = 'gone1';
    get('#report-select').events.change();
    await tick();
    get('#unpublish-report').events.click();
    await tick();
    get('#unpublish-report').events.click();
    await tick();
    assert.deepEqual(unpublished, ['gone1']);
    assert.equal(get('#unpublish-report').dataset.state, '');
    await new Promise(resolve => setTimeout(resolve, 0));
    assert.equal(state.reports.some(report => report.id === 'gone1'), false, 'the server dropped the row');
    assert.ok(statusLines.some(line => (
      /^Report gone1 · Deleted · /.test(line) && !line.includes('hides when seen')
    )), `the delete receipt must not wait for a read; flow wrote: ${statusLines.join(' | ')}`);
    delete statusNode.textContent;
    state.reports = keptReports;
    unpublished.length = 0;
    get('#reports-tab').events.click();
    await new Promise(resolve => setTimeout(resolve, 0));
    get('#report-select').value = keptSelect;
  });

  await t.test("Only consecutive delete clicks confirm, including clicks on the icon", async () => {
    const keptReports = state.reports;
    const keptSelect = get('#report-select').value;
    const button = get('#unpublish-report');
    const children = button.children.slice();
    const icon = new Element();
    const shape = new Element();
    icon.append(shape);
    button.append(icon);
    const press = async (target, onButton) => {
      const click = event({ target });
      documentCaptureEvents.click?.(click);
      if (onButton) await button.events.click(click);
      documentEvents.click(click);
      await tick();
    };
    unpublished.length = 0;
    try {
      state.reports = ['outside', 'nested'].map(id => ({
        id, title: id, updated_at: '2026-09-25T10:00:00', seen_at: '2026-09-25T10:05:00'
      }));
      await refresh();
      get('#report-select').value = 'outside';
      await press(button, true);
      assert.equal(button.dataset.state, 'bad');
      await press(new Element(), false);
      assert.equal(button.dataset.state, '', 'an intervening click clears the arm');
      await press(button, true);
      assert.deepEqual(unpublished, [], 'a click after cancellation must arm again');
      await press(button, true);
      assert.deepEqual(unpublished, ['outside']);
      get('#report-select').value = 'nested';
      await press(shape, true);
      assert.equal(button.dataset.state, 'bad', 'the icon click must not cancel its own arm');
      assert.deepEqual(unpublished, ['outside']);
      await press(shape, true);
      assert.deepEqual(unpublished, ['outside', 'nested']);
    } finally {
      button.replaceChildren(...children);
      state.reports = keptReports;
      unpublished.length = 0;
      await refresh();
      get('#report-select').value = keptSelect;
    }
  });

  await t.test("The delete press stamps its acknowledgement before removal", async () => {
    // The receipt appears atop the panel that the owner removes from. The page stamps its acknowledgement before removal.
    // The ack time sits past every stamp, so the tab's own open cannot clear it.
    const keptReports = state.reports;
    const keptSelect = get('#report-select').value;
    ackSeenCalls.length = 0;
    unpublished.length = 0;
    state.reports = [{ id: 'held1', title: 'Held', updated_at: '2026-09-25T10:00:00',
      seen_at: '2026-09-25T10:05:00',
      latest_answer_id: 'a123456-aaaaaaaaaaaaaaaaaaaaaaaaa',
      latest_answer_at: '2026-09-25T10:06:00',
      latest_answer_acknowledged_at: '2099-01-01T00:00:00', ack_seen_at: null }];
    get('#reports-tab').events.click();
    await new Promise(resolve => setTimeout(resolve, 0));
    get('#report-select').value = 'held1';
    ackSeenCalls.length = 0;
    get('#unpublish-report').events.click();
    await tick();
    assert.deepEqual(unpublished, [], 'the first click still only arms');
    get('#unpublish-report').events.click();
    await tick();
    assert.deepEqual(ackSeenCalls, ['/api/reports/held1/ack-seen'],
      'the confirm stamps the open the hold reads');
    assert.deepEqual(unpublished, ['held1'], 'the removal lands with the stamp');
    state.reports = keptReports;
    unpublished.length = 0;
    get('#reports-tab').events.click();
    await new Promise(resolve => setTimeout(resolve, 0));
    get('#report-select').value = keptSelect;
  });

  await t.test("Multiple files stage as removable filename chips beside \"Your message\"", async () => {
    // Files stage as removable filename chips beside "Your message", with no saved-files list.
    // The note receipt and backend state keep every original name.
    picker = get('#upload-file');
    chips = () => get('#staged-files').children.map(chip => chip.children[0].textContent.replace(/ · .*$/, ''));
    chipText = () => get('#staged-files').children[0].children[0].textContent;
    get('#attach-file').events.click();
    assert.equal(picker.clicks, 1, 'the attach button opens the native multi-file picker');
    picker.files = [{ name: 'empty.bin', size: 0, type: '' }];
    picker.events.change();
    assert.equal(get('#send-status').textContent, 'empty.bin is empty.');
    picker.files = [{ name: 'big.bin', size: 50_000_001, type: 'application/octet-stream' }];
    picker.events.change();
    assert.equal(get('#send-status').textContent, 'Staged 1 file. Send message to save them together.');
    assert.deepEqual(chips(), ['big.bin'], 'a large file has no ceiling');
    get('#staged-files').children[0].children[1].events.click();
    tooMany = Array.from({ length: 6 }, (_, n) => ({ name: `file-${n}.bin`, size: 1, type: '' }));
    picker.files = tooMany;
    picker.events.change();
    assert.equal(get('#send-status').textContent, 'Staged 6 files. Send message to save them together.');
    assert.equal(get('#staged-files').children.length, 6, 'no count cap stages six files');
    assert.equal(uploadCalls.length, 0, 'choosing files does not upload them yet');
    while (get('#staged-files').children.length) {
      get('#staged-files').children[0].children[1].events.click();
    }
    assert.equal(get('#staged-files').hidden, true, 'clearing the six keeps the exact chips below');
    first = { name: 'report card.pdf', size: 12, type: 'application/pdf', lastModified: 7 };
    second = { name: 'photo.png', size: 2, type: 'image/png', lastModified: 8 };
    thirdFile = { name: 'notes.txt', size: 9, type: 'text/plain', lastModified: 9 };
    picker.files = [first, second];
    picker.events.change();
    assert.deepEqual(chips(), ['report card.pdf', 'photo.png']);
    assert.equal(chipText(), 'report card.pdf · 12 B', 'the chip carries the size of the file');
    assert.equal(get('#staged-files').hidden, false);
    assert.equal(get('#staged-files').children[0].children[1].getAttribute('aria-label'), 'Remove report card.pdf');
    assert.equal(uploadCalls.length, 0, 'choosing files does not upload them yet');
    dragging = event({ dataTransfer: { types: ['Files'], files: [thirdFile] } });
    get('#compose').events.dragover(dragging);
    assert.equal(dragging.prevented, true);
    assert.equal(get('#compose').dataset.dragging, 'true');
    dropping = event({ dataTransfer: { types: ['Files'], files: [thirdFile] } });
    get('#compose').events.drop(dropping);
    assert.equal(dropping.prevented, true);
    assert.equal(get('#compose').dataset.dragging, undefined);
    assert.deepEqual(chips(), ['report card.pdf', 'photo.png', 'notes.txt']);
  });

  await t.test("Remove the middle file and reselect it: replacement keeps both chip order and its own remo", async () => {
    // Remove the middle file and reselect it: replacement keeps both chip order and its own remove.
    get('#staged-files').children[1].children[1].events.click();
    assert.deepEqual(chips(), ['report card.pdf', 'notes.txt']);
    picker.files = [second];
    picker.events.change();
    assert.deepEqual(chips(), ['report card.pdf', 'notes.txt', 'photo.png']);
    badDrop = event({ dataTransfer: { types: ['Files'], files: [{ name: 'empty-drop.bin', size: 0, type: '' }] } });
    get('#compose').events.drop(badDrop);
    assert.deepEqual(chips(), ['report card.pdf', 'notes.txt', 'photo.png'], 'an invalid drop keeps staged files');
    assert.equal(get('#send-status').textContent, 'empty-drop.bin is empty.');

  });

  await t.test("One multipart call writes the note and all three files", async () => {
    // One multipart call writes the note and all three files. A lost response keeps them in memory.
    // manual retry reuses the same note ID and the backend returns the one saved message.
    notesBeforeFile = state.notes.length;
    uploadsBeforeFile = (state.uploads || []).length;
    sendsBeforeFile = sent.length;
    get('#note').value = 'Please review all attachments';
    loseUploadResponse = true;
    await get('#form').events.submit(event({}));
    assert.equal(uploadCalls.length, 1);
    assert.deepEqual(uploadCalls[0].files, [first, thirdFile, second]);
    assert.equal(uploadCalls[0].text, 'Please review all attachments');
    assert.equal(uploadCalls[0].contentType, undefined, 'the browser supplies the multipart boundary');
    assert.equal(Object.hasOwn(uploadCalls[0].headers, 'X-Preview-Token'), false);
    assert.equal(sent.length, sendsBeforeFile, 'no separate upload notification note is posted');
    assert.equal(get('#staged-files').hidden, false, 'an unconfirmed response keeps every staged file');
    assert.match(get('#send-status').textContent, /^Save not confirmed:/);
    assert.deepEqual(JSON.parse(storage.get('arena-preview-v1:pending')).attachments.map(item => item.name),
      ['report card.pdf', 'notes.txt', 'photo.png']);
    await get('#form').events.submit(event({}));
    await tick();
    await refresh();
    assert.equal(uploadCalls.length, 2);
    assert.equal(uploadCalls[0].id, uploadCalls[1].id, 'unchanged retries reuse the note ID');
    assert.equal(state.notes.length, notesBeforeFile + 1);
    assert.equal(state.uploads.length, uploadsBeforeFile + 3);
    assert.equal(sent.length, sendsBeforeFile);
    assert.equal(get('#note').value, '');
    assert.equal(get('#staged-files').hidden, true);
    assert.deepEqual(state.uploads.slice(-3).map(item => item.file), [
      `${uploadCalls[0].id}-1.pdf`, `${uploadCalls[0].id}-2.txt`, `${uploadCalls[0].id}-3.png`
    ]);
    attached = get('#history').children.find(item => item.children[2].children[0].dataset.full === uploadCalls[0].id);
    assert.ok(attached);
    receipt = attached.children[2];
    assert.equal(part(receipt, 'receipt-file').textContent, ' · report card.pdf · notes.txt · photo.png');
    assert.ok(receipt.children.indexOf(part(receipt, 'receipt-file')) > receipt.children.indexOf(part(receipt, 'state-dot')),
      'the filenames follow the receipt date and state dot');

  });

  await t.test("An upload the network drops retries once under the same note ID", async () => {
    picker.files = [{ name: 'retry.bin', size: 2048, type: 'application/octet-stream' }];
    picker.events.change();
    const calls = uploadCalls.length;
    const notes = state.notes.length;
    dropUploadOnce = true;
    await get('#form').events.submit(event({}));
    await tick();
    assert.equal(uploadCalls.length, calls + 2, 'exactly one replay follows the drop');
    assert.equal(uploadCalls.at(-1).id, uploadCalls.at(-2).id, 'the replay keeps the note ID');
    assert.deepEqual(uploadCalls.at(-1).files.map(file => file.name), ['retry.bin']);
    assert.equal(get('#send-status').textContent, '', 'a confirmed upload leaves no routine success footer');
    assert.equal(state.notes.length, notes + 1, 'the replay writes no second note');
    assert.equal(get('#staged-files').hidden, true, 'a saved note clears its chips');
  });

  await t.test("One file without text still writes a note and uses the original single-file basename", async () => {
    // One file without text still writes a note and uses the original single-file basename.
    picker.files = [{ name: 'single.bin', size: 1, type: '' }];
    picker.events.change();
    await get('#form').events.submit(event({}));
    assert.equal(uploadCalls.at(-1).text, 'File: single.bin');
    assert.equal(uploadCalls.at(-1).files.length, 1);
    assert.equal(sent.length, sendsBeforeFile);
    assert.equal(get('#staged-files').hidden, true);

  });

  await t.test("Clipboard images stage with epoch filenames, preserve bytes and use the normal send", async () => {
    const paste = get('#compose').events.paste;
    assert.equal(typeof paste, 'function');
    const before = uploadCalls.length;
    const stamp = 1790674492000;
    context.Date = class extends Date { static now() { return stamp; } };
    const image = new File([new Uint8Array([137, 80, 78, 71])], 'image.png', { type: 'image/png' });
    const jpeg = new File(['jpeg bytes'], 'image.jpeg', { type: 'image/jpeg' });
    const pasteEvent = files => event({ clipboardData: { files, getData: () => '' } });
    try {
      get('#note').value = 'Review this image';
      const plain = event({ clipboardData: { files: [], getData: () => 'text' } });
      paste(plain);
      assert.equal(plain.prevented, undefined, 'native text paste is untouched');
      paste(event({ clipboardData: null }));
      paste(pasteEvent([new File(['x'], 'notes.txt', { type: 'text/plain' })]));
      assert.equal(get('#staged-files').hidden, true, 'non-images do not become attachments');
      const images = pasteEvent([image, image, jpeg]);
      paste(images);
      assert.equal(images.prevented, true);
      assert.deepEqual(chips(), [`${stamp}.png`, `${stamp}-2.png`, `${stamp}.jpg`]);
      const mixed = event({ clipboardData: { files: [image], getData: () => 'caption' } });
      paste(mixed);
      assert.equal(mixed.prevented, undefined, 'mixed text still uses native paste');
      assert.deepEqual(chips(), [`${stamp}.png`, `${stamp}-2.png`, `${stamp}.jpg`, `${stamp}-3.png`]);
      assert.equal(get('#note').value, 'Review this image');
      assert.equal(uploadCalls.length, before, 'pasting only stages files');
      get('#send').disabled = true;
      paste(pasteEvent([image]));
      get('#send').disabled = false;
      assert.equal(chips().length, 4, 'sending does not accept more files');
      paste(pasteEvent([new File([], 'empty.png', { type: 'image/png' })]));
      assert.match(get('#send-status').textContent, /is empty/);
      const oversized = new File([new Uint8Array(50_000_001)], 'large.png', { type: 'image/png' });
      paste(pasteEvent([oversized]));
      assert.equal(chips().length, 5, 'a large paste has no ceiling');
      get('#staged-files').children[4].children[1].events.click();
      assert.equal(chips().length, 4, 'the large paste removes cleanly');
      await get('#form').events.submit(event({}));
      assert.equal(uploadCalls.length, before + 1);
      const files = uploadCalls.at(-1).files;
      assert.deepEqual(files.map(file => file.name), [`${stamp}.png`, `${stamp}-2.png`, `${stamp}.jpg`, `${stamp}-3.png`]);
      assert.equal(files[0].type, 'image/png');
      assert.equal(files[0].lastModified, stamp);
      assert.deepEqual(new Uint8Array(await files[0].arrayBuffer()), new Uint8Array(await image.arrayBuffer()));
      assert.equal(get('#staged-files').hidden, true);
    } finally { delete context.Date; get('#send').disabled = false; }
  });

  await t.test("A long text paste stages one attachment and leaves the composer alone", async () => {
    // A paste above either limit becomes one text attachment named for its line count and the
    // epoch, so a pasted log never fills the composer. A shorter paste stays a native paste.
    const paste = get('#compose').events.paste;
    const stamp = 1790674500000;
    context.Date = class extends Date { static now() { return stamp; } };
    const lines = 15;
    const text = 'x\n'.repeat(lines - 1) + 'x';
    const textEvent = () => event({ clipboardData: { files: [], getData: () => text } });
    try {
      get('#note').value = 'Review this log';
      const shortPaste = event({ clipboardData: { files: [], getData: () => 'short' } });
      paste(shortPaste);
      assert.equal(shortPaste.prevented, undefined, 'a short paste stays native');
      assert.equal(get('#staged-files').hidden, true);
      const boundary = event({ clipboardData: { files: [], getData: () => Array.from({ length: lines - 1 }, () => 'x').join('\n') } });
      paste(boundary);
      assert.equal(boundary.prevented, undefined, 'the line-limit boundary stays native');
      assert.equal(get('#staged-files').hidden, true, 'the boundary stages no attachment');
      const characters = 1500;
      for (const unit of ['y', '😀']) {
        const atLimit = event({ clipboardData: { files: [], getData: () => unit.repeat(characters) } });
        paste(atLimit);
        assert.equal(atLimit.prevented, undefined, 'the character boundary stays native, including Unicode');
        assert.equal(get('#staged-files').hidden, true, 'the character boundary stages no attachment');
        const aboveLimit = event({ clipboardData: { files: [], getData: () => unit.repeat(characters) + 'x' } });
        paste(aboveLimit);
        assert.equal(aboveLimit.prevented, true, 'one long line above the character limit becomes a file');
        assert.deepEqual(chips(), [`1-pasted-lines-${stamp}.txt`]);
        assert.equal(get('#note').value, 'Review this log', 'character conversion keeps the existing draft');
        for (const chip of get('#staged-files').children.slice()) await chip.children[1].events.click();
      }
      const longPaste = textEvent();
      paste(longPaste);
      assert.equal(longPaste.prevented, true, 'a long paste never reaches the composer');
      assert.deepEqual(chips(), [`${lines}-pasted-lines-${stamp}.txt`]);
      assert.equal(get('#note').value, 'Review this log', 'the draft keeps its own text');
      paste(textEvent());
      assert.deepEqual(chips(), [`${lines}-pasted-lines-${stamp}.txt`, `${lines}-pasted-lines-${stamp}-2.txt`]);
      get('#send').disabled = true;
      const blocked = textEvent();
      paste(blocked);
      assert.equal(blocked.prevented, undefined, 'a disabled send leaves the paste alone');
    } finally {
      delete context.Date;
      get('#send').disabled = false;
      for (const chip of get('#staged-files').children.slice()) await chip.children[1].events.click();
    }
  });

  await t.test('Ctrl+Shift+V keeps long clipboard text out of attachments without changing normal paste', async () => {
    const composer = get('#compose');
    const draft = get('#note').value;
    const before = chips();
    const text = 'long clipboard line\n'.repeat(50);
    const pasted = () => event({ clipboardData: { files: [], getData: () => text } });
    const shortcut = () => event({ key: 'V', ctrlKey: true, shiftKey: true });
    const clear = async () => {
      for (const chip of get('#staged-files').children.slice()) await chip.children[1].events.click();
    };
    assert.equal(typeof composer.events.keydown, 'function');
    const key = shortcut();
    composer.events.keydown(key);
    const plain = pasted();
    composer.events.paste(plain);
    assert.equal(key.prevented, undefined, 'the native paste shortcut stays available');
    assert.equal(plain.prevented, undefined, 'the browser inserts plain text');
    assert.deepEqual(chips(), before, 'plain-text paste stages no file');
    assert.equal(get('#note').value, draft, 'the handler does not replace the draft');
    try {
      const normal = pasted();
      composer.events.paste(normal);
      assert.equal(normal.prevented, true, 'the shortcut applies to one paste only');
      assert.ok(chips().length > before.length);
      await clear();
      for (const cancel of [
        () => composer.events.keyup(event({ key: 'Shift' })),
        () => composer.events.focusout(event({})),
        () => composer.events.keydown(event({ key: 'x', ctrlKey: false, shiftKey: false })),
      ]) {
        composer.events.keydown(shortcut());
        cancel();
        const next = pasted();
        composer.events.paste(next);
        assert.equal(next.prevented, true, 'an abandoned shortcut does not affect a later paste');
        await clear();
      }
    } finally { await clear(); }
  });

  await t.test("The queue records per-job opt-in and a browser worker claims one URL at a time", async () => {
    // The queue records per-job opt-in and a browser worker claims one URL at a time. Remote fetches
    // omit credentials and referrers. None of these mocks assert actual browser CORS behavior.
    waitFor = async predicate => {
      for (let attempt = 0; attempt < 80; attempt++) {
        if (predicate()) return;
        await tick();
      }
      throw new Error('Browser queue did not settle');
    };
    approvalButton = (url, label) => {
      const row = get('#fetch-approvals').children.find(item => item.children[0].textContent === url);
      return row?.children.at(-1).children.find(item => item.textContent === label);
    };
    requested = 'https://files.example.org/review.zip';
    state.fetch_jobs = [{ id: 'agent-1', url: requested, allow_proxy: false, status: 'queued',
      approval: 'pending', source: null, error: null, size: null, file: null, present: false }];
    await tick(); // The preceding file-send completion can still be finishing its state poll.
    await refresh();
    assert.equal(get('#downloads-pip').hidden, false, 'a pending request lights the Downloads dot');
    assert.equal(get('#downloads-pip').attributes['aria-label'], '1 download awaiting approval');
    assert.equal(get('#fetch-approvals').children.length, 1);
    assert.ok(approvalButton(requested, 'Approve') && approvalButton(requested, 'Deny'));
    assert.equal(get('#fetch-list').children.length, 0, 'pending requests appear in their own section');
    assert.match(get('#fetch-count').textContent, /1 awaiting approval/);
    assert.equal(remoteCalls.length, 0, 'polling must not claim or fetch an unapproved URL');
    get('#downloads-tab').events.click();
    assert.equal(get('#downloads-pip').hidden, false, 'opening Downloads cannot dismiss a pending decision');
    await approvalButton(requested, 'Approve').events.click();
    await waitFor(() => state.fetch_jobs[0].status === 'saved');
    assert.equal(get('#downloads-pip').hidden, true);
    assert.equal(remoteCalls.at(-1).url, requested, 'an approval queues a normal browser fetch');
    assert.equal(get('#fetch-list').children.length, 1);
    deniedURL = 'https://files.example.org/denied.zip';
    state.fetch_jobs.unshift({ id: 'agent-2', url: deniedURL, allow_proxy: true, status: 'queued',
      approval: 'pending', source: null, error: null, size: null, file: null, present: false });
    fetchedBeforeDeny = remoteCalls.length;
    await refresh();
    assert.equal(get('#downloads-pip').hidden, false);
    await approvalButton(deniedURL, 'Deny').events.click();
    assert.equal(state.fetch_jobs[0].approval, 'denied');
    assert.equal(state.fetch_jobs[0].status, 'failed');
    assert.equal(get('#downloads-pip').hidden, true);
    assert.equal(remoteCalls.length, fetchedBeforeDeny, 'denial never starts the browser worker');
    assert.equal(get('#fetch-approvals').children.length, 0);
    assert.match(get('#fetch-list').children[0].children[1].textContent, /denied/);
    assert.equal(get('#fetch-list').children[0].children.some(item => item.textContent === 'Retry same URL'), false);
    state.fetch_jobs = [];
    await refresh();
    get('#notes-tab').events.click();
    resultCalls.length = 0;
    remoteCalls.length = 0;

    get('#fetch-url').value = 'http://example.org/unsafe.zip';
    await get('#fetch-form').events.submit(event({}));
    assert.match(get('#fetch-status').textContent, /Only HTTPS/);
    assert.equal(queuedCalls.length, 0);
    get('#fetch-url').value = 'https://user:password@example.org/unsafe.zip';
    await get('#fetch-form').events.submit(event({}));
    assert.equal(queuedCalls.length, 0, 'embedded credentials are rejected before queueing');

    direct = 'https://files.example.org/plugin.zip';
    remoteBehaviors.set(direct, { declared: 2, type: 'application/zip', chunks: [Uint8Array.from([80, 75])] });
    get('#fetch-url').value = direct;
    await get('#fetch-form').events.submit(event({}));
    await waitFor(() => state.fetch_jobs[0]?.status === 'saved');
    assert.equal(queuedCalls[0].allow_proxy, true, 'every job allows the proxy fallback');
    assert.equal(remoteCalls.at(-1).url, direct);
    assert.equal(remoteCalls.at(-1).options.credentials, 'omit');
    assert.equal(remoteCalls.at(-1).options.referrerPolicy, 'no-referrer');
    assert.equal(resultCalls[0].source, 'direct');
    assert.equal(resultCalls[0].body.size, 2);
    assert.equal(get('#fetch-list').children[0].children[2].textContent, 'downloads/fetch-1.zip');
    assert.equal(get('#fetch-count').textContent, '1 in history · 0 queued or active.');

    blocked = 'https://files.example.org/cors.zip';
    remoteBehaviors.set(blocked, { error: 'CORS blocked' });
    get('#fetch-url').value = blocked;
    await get('#fetch-form').events.submit(event({}));
    await waitFor(() => state.fetch_jobs[0]?.status === 'saved');
    assert.equal(queuedCalls[1].allow_proxy, true);
    assert.equal(resultCalls.at(-1).source, 'allorigins', 'the fallback chain runs with no opt-in');
    assert.equal(remoteCalls.at(-1).url, `https://api.allorigins.win/raw?url=${encodeURIComponent(blocked)}`);

    // A whole chain that fails names every leg, and the retry reuses the same SQLite job.
    chain = 'https://files.example.org/chain.zip';
    remoteBehaviors.set(chain, { error: 'CORS blocked' });
    remoteBehaviors.set(`https://api.allorigins.win/raw?url=${encodeURIComponent(chain)}`, { error: 'Proxy offline' });
    remoteBehaviors.set(`https://api.codetabs.com/v1/proxy?quest=${encodeURIComponent(chain)}`, { error: 'Proxy down' });
    get('#fetch-url').value = chain;
    await get('#fetch-form').events.submit(event({}));
    await waitFor(() => state.fetch_jobs[0]?.status === 'failed');
    assert.match(state.fetch_jobs[0].error, /Direct: CORS blocked/);
    assert.match(state.fetch_jobs[0].error, /AllOrigins: Proxy offline/);
    assert.match(state.fetch_jobs[0].error, /CodeTabs: Proxy down/);
    remoteBehaviors.set(chain, { chunks: [Uint8Array.from([1])] });
    retryRow = get('#fetch-list').children.find(item => item.children[0].textContent === chain);
    await retryRow.children.find(item => item.textContent === 'Retry same URL').events.click();
    await waitFor(() => state.fetch_jobs.find(item => item.url === chain)?.status === 'saved');
    assert.equal(queuedCalls.length, 3, 'retry reuses the same SQLite job');

    // The owner's X frees one saved row. The first click only arms it.
    dropRow = get('#fetch-list').children.find(item => item.children[0].textContent === chain);
    dropButton = dropRow.children.find(item => item.className === 'icon-button fetch-drop');
    await dropButton.events.click();
    assert.equal(dropped.length, 0, 'the first click only arms the delete');
    await dropButton.events.click();
    assert.equal(dropped.length, 1);
    assert.equal(state.fetch_jobs.some(item => item.url === chain), false, 'the row leaves the list');

    origin = 'https://files.example.org/fallback.zip';
    allOrigins = `https://api.allorigins.win/raw?url=${encodeURIComponent(origin)}`;
    remoteBehaviors.set(origin, { error: 'CORS blocked' });
    remoteBehaviors.set(allOrigins, { chunks: [Uint8Array.from([3, 4])] });
    get('#fetch-url').value = origin;
    await get('#fetch-form').events.submit(event({}));
    await waitFor(() => state.fetch_jobs[0]?.status === 'saved');
    assert.equal(queuedCalls[3].allow_proxy, true);
    assert.equal(resultCalls.at(-1).source, 'allorigins');
    assert.equal(remoteCalls.at(-1).url, allOrigins);

    third = 'https://files.example.org/third.zip';
    allOriginsThird = `https://api.allorigins.win/raw?url=${encodeURIComponent(third)}`;
    codeTabs = `https://api.codetabs.com/v1/proxy?quest=${encodeURIComponent(third)}`;
    remoteBehaviors.set(third, { error: 'CORS blocked' });
    remoteBehaviors.set(allOriginsThird, { error: 'Proxy offline' });
    remoteBehaviors.set(codeTabs, { chunks: [Uint8Array.from([5])] });
    get('#fetch-url').value = third;
    await get('#fetch-form').events.submit(event({}));
    await waitFor(() => state.fetch_jobs[0]?.status === 'saved');
    assert.equal(resultCalls.at(-1).source, 'codetabs');
    assert.equal(remoteCalls.at(-1).url, codeTabs);

    // Owner-origin responses pass the old declared and streamed size thresholds.
    tooLarge = 'https://files.example.org/too-large.bin';
    remoteBehaviors.set(tooLarge, { declared: 102_400_001, chunks: [Uint8Array.from([1])] });
    get('#fetch-url').value = tooLarge;
    await get('#fetch-form').events.submit(event({}));
    await waitFor(() => state.fetch_jobs[0]?.status === 'saved');
    assert.equal(state.fetch_jobs[0].origin, 'owner');

    streamed = 'https://files.example.org/stream.bin';
    streamBehavior = { chunks: [{ byteLength: 102_400_001 }] };
    remoteBehaviors.set(streamed, streamBehavior);
    get('#fetch-url').value = streamed;
    await get('#fetch-form').events.submit(event({}));
    await waitFor(() => state.fetch_jobs[0]?.status === 'saved');
    assert.notEqual(streamBehavior.cancelled, true);

    // Approved agent and legacy jobs also pass the old byte thresholds.
    for (const [suffix, behavior] of [['declared', { declared: 102_400_001 }], ['stream', { chunks: [{ byteLength: 102_400_001 }] }]]) {
      const url = `https://files.example.org/agent-${suffix}.bin`;
      remoteBehaviors.set(url, behavior);
      state.fetch_jobs.unshift({ id: `agent-${suffix}`, url, origin: suffix === 'declared' ? 'agent' : undefined, status: 'queued', approval: 'approved', allow_proxy: true, error: null });
      callsBeforeLimit = remoteCalls.length;
      resultsBeforeLimit = resultCalls.length;
      await refresh();
      await waitFor(() => state.fetch_jobs[0]?.status === 'saved');
      assert.equal(remoteCalls.length, callsBeforeLimit + 1);
      assert.equal(resultCalls.length, resultsBeforeLimit + 1);
      assert.equal(state.fetch_jobs[0].error, null);
      if (suffix === 'stream') assert.notEqual(behavior.cancelled, true);
    }
    lost = 'https://files.example.org/lost.bin';
    remoteBehaviors.set(lost, { chunks: [Uint8Array.from([1])] });
    beforeLost = queuedCalls.length;
    enqueueResponseLost = true;
    get('#fetch-url').value = lost;
    await get('#fetch-form').events.submit(event({}));
    assert.equal(queuedCalls.length, beforeLost + 1, 'a lost enqueue response is not blindly retried');
    assert.match(get('#fetch-status').textContent, /Queue not confirmed.*Check the list/);
    await refresh();
    await waitFor(() => state.fetch_jobs.find(item => item.url === lost)?.status === 'saved');
    state.fetch_jobs = [];
    await refresh();

    state.notes = [
      {id: 'edited-old', text: 'Older question', at: '2026-09-22T00:00:00', acknowledged_at: '2026-09-22T00:01:00', ack_kind: 'reply', ack_text: 'First answer', ack_edited_at: null},
      {id: 'newer-note', text: 'Newer question', at: '2026-09-22T00:02:00'}
    ];
    await refresh();
    assert.equal(get('#log-edited').hidden, true);
    // A re-ack appends a block: the first answer stays, the new one lands under it with its stamp.
    state.notes[0].replies = [{kind: 'reply', text: 'Second answer', at: '2026-09-22T00:03:00', html: '<p>Second answer</p>'}];
    state.notes[0].ack_edited_at = '2026-09-22T00:03:00';
    await refresh();
    editedNode = get('#history').children[0];
    assert.match(editedNode.children[2].textContent, / · Replied again \d+ hours ago/);
    const grown = editedNode.children[1];
    assert.equal(grown.children.length, 2, 'the first answer stays and the re-ack adds a block');
    assert.equal(grown.children[0].tagName, 'p');
    assert.equal(grown.children[0].textContent, 'First answer');
    assert.equal(grown.children[1].className, 'reply-block');
    assert.equal(grown.children[1].innerHTML, '<p>Second answer</p>');
    assert.match(grown.children[1].title, /^Replied again \d+ hours ago/);
    assert.equal(get('#history').children[1].children[2].children[0].dataset.full, 'newer-note', 'edits keep log order');
    assert.equal(get('#notes-pip').hidden, false);
    assert.equal(get('#log-edited').hidden, false);
    get('#log-search').value = 'zzz';
    get('#log-search').events.input();
    await get('#log-edited').events.click();
    assert.equal(editedNode.children[1].scrolledIntoView, true);
    assert.equal(get('#log-search').value, '', 'the jump clears a search that could hide its target');
    assert.equal(get('#notes-pip').hidden, true);
    await refresh();
    assert.equal(get('#log-edited').hidden, true, 'an unchanged poll does not notify again');
    assert.deepEqual(replySeenCalls, [{url: '/api/messages/edited-old/replies/seen', count: 1}], 'viewed reply count is written to the server');
    state.notes[0].replies.push({kind: 'note', text: 'Third block, same second'});
    state.notes[0].replies[1].at = '2026-09-22T00:03:00';
    await refresh();
    assert.equal(get('#log-edited').hidden, false, 'same-second replies still notify');
    assert.equal(editedNode.children[1].children.length, 3);
    assert.equal(editedNode.children[1].children[2].children[0].textContent, 'Third block, same second', 'a note ack renders as plain text');
    await get('#log-edited').events.click();
    vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../assets/app.js'), 'utf8'), {...context});
    await tick();
    assert.deepEqual(replySeenCalls.map(call => call.count), [1, 2], 'same-second replies get distinct viewed counts');
    assert.equal(get('#log-edited').hidden, true, 'the SQLite-backed viewed count survives a reload');
    state.reports = [{id: 'stable', title: 'Stable', updated_at: '2026-09-22T00:00:00', seen_at: '2026-09-22T00:00:01'}];
    await refresh();
    get('#report-select').value = 'stable';
    get('#reports-tab').events.click();
    await tick(); await tick();
    renders = get('#report').renders;
    state.reports.push({id: 'other', title: 'Other report', updated_at: '2026-09-22T01:00:00'});
    await refresh(); await tick();
    assert.equal(get('#report').renders, renders, 'another report must not render the unchanged current report');
    state.last_check = new Date().toISOString();
    await refresh(); await tick();
    assert.equal(get('#report').renders, renders, 'CLI inbox checks do not render the report');
    state.reports[0].updated_at = '2026-09-22T02:00:00';
    await refresh(); await tick();
    assert.equal(get('#report').renders, renders + 1, 'current-report updates still render');
    renders = get('#report').renders;
    replacements = get('#report').replacements;
    get('#refresh-report').events.click();
    assert.equal(get('#report').replacements, replacements, 'keep old content until the refresh arrives');
    await tick(); await tick();
    assert.equal(get('#report').renders, renders + 1, 'explicit refresh still renders');
    state.notes = [{id:'notes-only', text:'No tasks yet', at:'2026-09-22T12:00:00'}];
    state.tasks = null;
    await refresh();
    copiesBeforeNotesOnly = copied.length;
    const statusBeforeCopy = get('#send-status').textContent;
    await get('#copy-state').events.click();
    await tick();
    assert.equal(copied.length, copiesBeforeNotesOnly + 1, 'copy a session before its first task');
    assert.match(copied.at(-1), /"id":"notes-only"/);
    assert.equal(get('#copy-state').dataset.state, 'good');
  });

  await t.test('A reply click clears immediately, survives refresh, and restores a failed write', async () => {
    const keptNotes = state.notes;
    let release = () => {};
    let pendingWrite = null;
    try {
      state.notes = [{ id: 'optimistic-reply', text: 'Question', at: '2026-10-13T03:00:00',
        acknowledged_at: '2026-10-13T03:01:00', ack_kind: 'reply', ack_text: 'First answer',
        ack_edited_at: '2026-10-13T03:02:00', ack_edited_seen_count: 0,
        replies: [{ kind: 'reply', text: 'Second answer', at: '2026-10-13T03:02:00' }] }];
      await refresh();
      replySeenWait = new Promise(resolve => { release = resolve; });
      const destination = get('#history').children[0].children[1];
      const historyReplacements = get('#history').replacements;
      pendingWrite = get('#log-edited').events.click();
      assert.equal(get('#history').replacements, historyReplacements, 'keep the focused answer attached during the optimistic render');
      assert.equal(destination.dataset.highlight, 'true', 'highlight the reached answer before its write completes');
      assert.equal(destination.scrolledIntoView, true);
      assert.equal(destination.focused, true);
      assert.equal(get('#log-edited').hidden, true, 'do not wait for the server receipt');
      assert.equal(get('#notes-pip').hidden, true);
      await refresh();
      assert.equal(get('#log-edited').hidden, true, 'a pending write survives a refresh');
      assert.equal(destination.dataset.highlight, 'true', 'a poll preserves the destination highlight');
      assert.equal(get('#history').replacements, historyReplacements, 'a poll must not detach the focused answer');
      state.notes[0].replies.push({ kind: 'reply', text: 'Newer answer', at: '2026-10-13T03:03:00' });
      state.notes[0].ack_edited_at = '2026-10-13T03:03:00';
      await refresh();
      assert.equal(get('#log-edited').hidden, false, 'a newer reply stays unread');
      release();
      await pendingWrite;
      replySeenWait = null;
      assert.equal(get('#log-edited').hidden, false, 'the old receipt cannot clear a newer reply');
      const composerBeforeReplyFailure = get('#send-status').textContent;
      replySeenError = true;
      const failed = get('#log-edited').events.click();
      assert.equal(get('#log-edited').hidden, true);
      await failed;
      assert.equal(get('#log-edited').hidden, false, 'a failed write restores the indicator');
      assert.equal(get('#send-status').textContent, composerBeforeReplyFailure, 'reply feedback stays outside the composer');
      assert.match(get('#log-edited').title, /Write failed/, 'the reply control retains its failure detail');
      assert.match(get('#log-edited').getAttribute('aria-label'), /Write failed/, 'the reply failure has an accessible label');
      replySeenError = false;
      await get('#log-edited').events.click();
      assert.equal(get('#log-edited').hidden, true);
      state.notes[0].ack_edited_seen_count = 1;
      await refresh();
      assert.equal(get('#log-edited').hidden, true, 'a stale poll cannot undo a successful receipt');
      state.notes[0].ack_edited_seen_count = 2;
      await refresh();
      assert.equal(get('#log-edited').hidden, true);

    } finally {
      replySeenWait = null;
      replySeenError = false;
      release();
      if (pendingWrite) await pendingWrite;
      state.notes = keptNotes;
      await refresh();
    }
  });

  await t.test('Seeing an edited acknowledgement keeps the log where its jump landed', async () => {
    // The seen receipt changes the note state and makes the next poll re-render the log. Browser
    // layout clamps a replaced scroll list before the rows return, so preserve the reply's place.
    state = {
      notes: [
        { id: 'edited-scroll', text: 'Earlier question', at: '2026-10-13T03:00:00',
          acknowledged_at: '2026-10-13T03:01:00', ack_kind: 'reply', ack_text: 'First answer',
          ack_edited_at: '2026-10-13T03:02:00', ack_edited_seen_count: 0,
          replies: [{ kind: 'reply', text: 'Second answer', at: '2026-10-13T03:02:00' }] },
        { id: 'newer-scroll', text: 'Later question', at: '2026-10-13T03:03:00' }
      ],
      reports: [], tasks: { upcoming: [], finished: [] }, fetch_jobs: [], last_check: null
    };
    const history = get('#history');
    get('#log-search').value = '';
    get('#log-search').events.input();
    await refresh();
    const node = history.children.find(item => item.children[2].children[0].dataset.full === 'edited-scroll');
    const answer = node.children[1];
    history.scrollHeight = 900;
    history.clientHeight = 300;
    history.scrollTop = 600;
    history.events.scroll();
    const scrollIntoView = answer.scrollIntoView.bind(answer);
    answer.scrollIntoView = options => {
      assert.equal(options.behavior, 'smooth', 'New reply scrolls with animation');
      assert.equal(get('#log-edited').hidden, true, 'render the optimistic receipt before starting the animation');
      scrollIntoView(); history.scrollTop = 240;
    };
    const replaceChildren = history.replaceChildren.bind(history);
    history.replaceChildren = (...children) => { replaceChildren(...children); history.scrollTop = 0; };
    try {
      const before = replySeenCalls.length;
      await get('#log-edited').events.click();
      assert.equal(history.scrollTop, 240, 'the new-reply jump lands on its answer');
      await refresh();
      assert.equal(history.scrollTop, 240, 'the seen-state refresh keeps the answer in view');
      assert.equal(replySeenCalls.length, before + 1, 'the reply receipt reaches the server');
    } finally {
      history.replaceChildren = replaceChildren;
      answer.scrollIntoView = scrollIntoView;
    }
  });

  await t.test('Old-message jumps mount their target and honor reduced motion', async () => {
    const history = get('#history');
    try {
      for (const reduce of [false, true]) {
        reducedMotion = reduce;
        const id = `old-jump-${reduce}`;
        state = {
          notes: [{ id, text: 'Old question', at: '2026-10-13T03:00:00',
            acknowledged_at: '2026-10-13T03:01:00', ack_kind: 'reply', ack_text: 'First answer',
            ack_edited_at: '2026-10-13T03:02:00', ack_edited_seen_count: 0,
            replies: [{ kind: 'reply', text: 'Later answer', at: '2026-10-13T03:02:00' }] }],
          reports: [], tasks: { upcoming: [], finished: [] }, fetch_jobs: [], last_check: null
        };
        get('#log-search').value = '';
        get('#log-search').events.input();
        await refresh();
        const row = history.children.find(item => item.children[2].children[0].dataset.full === id);
        const answer = row.children[1];
        answer.scrollIntoView = options => {
          assert.equal(history.contains(answer), true);
          assert.equal(options.behavior, reduce ? 'auto' : 'smooth', 'mounted New reply honors motion settings');
        };
        await get('#log-edited').events.click();
        state.notes[0].replies.push({kind: 'reply', text: 'Newest answer', at: '2026-10-13T03:03:00'});
        state.notes.push(...Array.from({length: reduce ? 1000 : 500}, (_, index) => ({
          id: `later-${reduce}-${index}`, text: 'Later question', at: '2026-10-13T03:03:00'
        })));
        await refresh();
        assert.equal(history.contains(row), false, 'the old target starts outside the mounted window');
        answer.scrollIntoView = options => {
          assert.equal(history.contains(answer), true, 'mount before scrolling the reply');
          assert.equal(options.behavior, reduce ? 'auto' : 'smooth');
          answer.scrolledIntoView = true;
        };
        await get('#log-edited').events.click();
        assert.equal(history.contains(row), true);
        assert.equal(answer.scrolledIntoView, true);
        assert.equal(answer.focused, true);
        assert.equal(answer.dataset.highlight, 'true');

        // Follow another old row through its reference.
        const referenceId = `reference-${reduce}`;
        state.notes.unshift({id: referenceId, text: 'Referenced question', at: '2026-10-13T02:00:00'});
        await refresh();
        assert.equal(history.children.some(item => item.children[2].children[0].dataset.full === referenceId), false);
        const target = new Element();
        target.className = 'ack-reference';
        target.dataset = {referenceType: 'note', referenceId};
        documentEvents.click({target, preventDefault() {}});
        const reference = history.children.find(item => item.children[2].children[0].dataset.full === referenceId);
        assert.ok(reference, 'a reference reveals its old message');
        assert.equal(reference.scrolledIntoView, true);
        assert.equal(reference.focused, true);
      }
    } finally {
      reducedMotion = false;
    }
  });

  await t.test('Acknowledgement IDs link to matching messages, reports, and tasks', async () => {
    state = {
      notes: [
        { id: 'question', text: 'Question', at: '2026-09-22T12:00:00',
          acknowledged_at: '2026-09-22T12:01:00', ack_kind: 'reply', ack_text: 'See references',
          ack_html: '<p>See <code>report-ref</code>, <code>message-ref</code>, <code>task-ref</code>, <code>missing-ref</code>, <code>duplicate-ref</code>.</p><pre><code>task-ref\n</code></pre>' },
        { id: 'message-ref', text: 'Linked message', at: '2026-09-22T12:02:00' }
      ],
      reports: [
        { id: 'report-ref', title: 'Linked report', updated_at: '2026-09-22T12:00:00' },
        { id: 'duplicate-ref', title: 'Duplicate report', updated_at: '2026-09-22T12:00:00' }
      ],
      tasks: {
        upcoming: [
          { type: 'unspecified', id: 'task-ref', title: 'Linked task', details: [], status: 'upcoming', order: 1 },
          { type: 'unspecified', id: 'duplicate-ref', title: 'Duplicate task', details: [], status: 'upcoming', order: 2 }
        ],
        finished: [], updated_at: '2026-09-22T12:00:00'
      },
      fetch_jobs: [], last_check: null
    };
    await refresh();
    const row = id => get('#history').children.find(item => item.children[2].children[0].dataset.full === id);
    const answer = row('question').children[1];
    for (const [type, id, panel] of [
      ['report', 'report-ref', 'reports-panel'],
      ['note', 'message-ref', 'notes-panel'],
      ['task', 'task-ref', 'tasks-panel']
    ]) {
      assert.match(answer.innerHTML, new RegExp(`<a class="ack-reference" href="#${panel}" data-reference-type="${type}" data-reference-id="${id}"><code>${id}</code></a>`));
    }
    assert.match(answer.innerHTML, /<code>missing-ref<\/code>/, 'unknown IDs stay plain code');
    assert.match(answer.innerHTML, /<code>duplicate-ref<\/code>/, 'ambiguous IDs stay plain code');
    assert.match(answer.innerHTML, /<pre><code>task-ref\n<\/code><\/pre>/, 'code blocks stay unchanged');

    const follow = (type, id) => {
      const target = new Element();
      target.className = 'ack-reference';
      target.dataset = { referenceType: type, referenceId: id };
      let prevented = false;
      documentEvents.click({ target, preventDefault: () => { prevented = true; } });
      assert.equal(prevented, true, `${type} navigation prevents the fragment jump`);
    };
    get('#log-search').value = 'zzz';
    get('#log-search').events.input();
    follow('report', 'report-ref');
    assert.equal(get('#reports-panel').hidden, false);
    assert.equal(get('#report-select').value, 'report-ref');
    await tick(); await tick();

    follow('note', 'message-ref');
    assert.equal(get('#notes-panel').hidden, false);
    assert.equal(get('#log-search').value, '', 'note links clear a search that could hide their target');
    assert.equal(row('message-ref').hidden, false);
    assert.equal(row('message-ref').scrolledIntoView, true);

    follow('task', 'task-ref');
    assert.equal(get('#tasks-panel').hidden, false);
    const taskTitle = get('#tasks-current-body').children[0].children[0];
    assert.equal(taskTitle.scrolledIntoView, true);
    assert.equal(taskTitle.focused, true);
    const arrivedRow = get('#tasks-current-body').children[0];
    assert.equal(arrivedRow.dataset.highlight, 'true', 'the destination row lights up');
    // Leaving the tab clears the mark at once, before its timer runs.
    get('#notes-tab').events.click();
    assert.equal(arrivedRow.dataset.highlight, undefined,
      'navigating away clears the highlight');
    // A fresh arrival clears itself after the short delay as well.
    follow('task', 'task-ref');
    assert.equal(get('#tasks-current-body').children[0].dataset.highlight, 'true');
    await new Promise(resolve => setTimeout(resolve, 2600));
    assert.equal(get('#tasks-current-body').children[0].dataset.highlight, undefined,
      'the highlight clears after the delay without touching the row');
  });

  await t.test("The composer footer names the key and its hour, and the tooltip keeps the host", async () => {
    state.agent_key = {
      key: 'K'.repeat(43),
      host: 'https://arena-proxy.example.ts.net',
      at: '2026-10-03T15:00:00+00:00',
    };
    await refresh();
    assert.match(get('#agent-key').textContent, /^Key K{7} · \d{2}:\d{2}$/,
      'seven characters and the hour name the key');
    assert.doesNotMatch(get('#agent-key').textContent, /K{8}/, 'the full key never prints');
    assert.equal(get('#agent-key').children.find(child => child.tagName === 'code')?.textContent,
      'KKKKKKK', 'only the key characters carry the code element');
    assert.equal(get('#agent-key').children[0], 'Key ', 'the label stays prose');
    assert.doesNotMatch(get('#agent-key').textContent, /example/,
      'the host leaves the line, so the footer fits a narrow width');
    assert.match(get('#agent-key').title, /^https:\/\/arena-proxy\.example\.ts\.net · set (just now|\d+ (mins?|hours?) ago)$/,
      'the tooltip keeps the host and the full stamp');
    state.agent_key = { key: 'K'.repeat(43), host: null, at: '2026-10-03T15:00:00+00:00' };
    await refresh();
    assert.match(get('#agent-key').title, /^set (just now|\d+ (mins?|hours?) ago)$/,
      'a hostless record leaves the tooltip with the stamp alone');
    state.agent_key = null;
    await refresh();
    assert.equal(get('#agent-key').textContent, 'No key recorded.');
    assert.equal(get('#agent-key').title, '', 'the tooltip clears with the record');
  });

  await t.test('A quiet note informs the agent and stays out of the log', async () => {
    state.notes = [
      { id: 'loud', text: 'Owner-facing line', at: '2026-10-05T05:00:00' },
      { id: 'murmur', text: 'The recorded agent key expired.', at: '2026-10-05T05:01:00', quiet: 1 },
    ];
    await refresh();
    const texts = get('#history').children.map(node => node.children[0].textContent);
    assert.deepEqual(texts, ['Owner-facing line'], 'the quiet note never renders');
    assert.match(get('#connection-text').textContent, /^1 messages saved/, 'the tally counts the log');
    const cached = JSON.parse(storage.get('arena-preview-v1:state-cache'));
    assert.equal(cached.notes.length, 2, 'the cached state keeps the quiet note');
  });

  await t.test('Loading older filtered messages never inserts quiet-note placeholders', async () => {
    const keptNotes = state.notes;
    const history = get('#history');
    try {
      state.notes = Array.from({ length: 80 }, (_, index) => ({
        id: `scroll-${index}`, text: `mark ${index}`, at: '2026-10-05T05:00:00', quiet: index % 2
      }));
      await refresh();
      get('#log-search').value = 'mark';
      get('#log-search').events.input();
      history.scrollHeight = 1000;
      history.clientHeight = 100;
      history.scrollTop = 0;
      history.events.scroll();
      assert.ok(history.children.every(node => node instanceof Element),
        'every rendered item must be a message row, never undefined');
      assert.ok(history.children.every(node => Number(node.children[2].children[0].dataset.full.split('-')[1]) % 2 === 0),
        'quiet notes must stay out of older pages');
      assert.equal(JSON.parse(storage.get('arena-preview-v1:state-cache')).notes.length, 80);
    } finally {
      get('#log-search').value = '';
      get('#log-search').events.input();
      state.notes = keptNotes;
      await refresh();
    }
  });

  await t.test('Skip poll toggles one flag, and a poll consumes the arm', async () => {
    // The press must write no note: the agent reads the flag, ends its wait, and no stale
    // "skip poll" line waits in the log for a later turn to misread.
    const composerBeforeSkip = get('#send-status').textContent;
    state.skip_poll = null;
    await refresh();
    assert.equal(get('#skip-poll').getAttribute('aria-pressed'), 'false');
    assert.equal(get('#skip-poll').dataset.state, '');
    assert.equal(get('#skip-poll').getAttribute('aria-label'), 'Ask the agent to end its wait now');
    state.notes = [];
    get('#skip-poll').events.click();
    await tick();
    assert.deepEqual(skipCalls.at(-1), { skip: true }, 'the press arms the flag');
    assert.equal(state.notes.length, 0, 'no note rides the press');
    assert.equal(get('#skip-poll').getAttribute('aria-pressed'), 'true');
    assert.equal(get('#skip-poll').dataset.state, 'good');
    assert.match(get('#skip-poll').getAttribute('aria-label'), /^Skip poll armed /);
    assert.equal(get('#send-status').textContent, composerBeforeSkip, 'arming Skip poll leaves composer feedback alone');
    // A second press clears the flag, so the owner can take the skip back.
    get('#skip-poll').events.click();
    await tick();
    assert.deepEqual(skipCalls.at(-1), { skip: false }, 'the second press disarms the flag');
    assert.equal(get('#skip-poll').getAttribute('aria-pressed'), 'false');
    assert.equal(get('#skip-poll').dataset.state, '');
    assert.equal(get('#skip-poll').getAttribute('aria-label'), 'Ask the agent to end its wait now');
    assert.equal(get('#send-status').textContent, composerBeforeSkip, 'clearing Skip poll leaves composer feedback alone');
    // An armed flag ends the wait, and the next state paints the button unarmed again.
    get('#skip-poll').events.click();
    await tick();
    assert.equal(get('#skip-poll').getAttribute('aria-pressed'), 'true');
    state.skip_poll = null;
    await refresh();
    assert.equal(get('#skip-poll').getAttribute('aria-pressed'), 'false');
    assert.equal(get('#skip-poll').dataset.state, '');
    failedWrites.add('/api/skip-poll');
    try {
      await get('#skip-poll').events.click();
      assert.equal(get('#send-status').textContent, composerBeforeSkip, 'Skip poll errors stay outside the composer');
      assert.equal(get('#skip-poll').dataset.state, 'bad', 'the failed control remains visible');
      assert.match(get('#skip-poll').title, /Write failed/);
      assert.match(get('#skip-poll').getAttribute('aria-label'), /Write failed/);
    } finally {
      failedWrites.delete('/api/skip-poll');
      await get('#skip-poll').events.click();
      await get('#skip-poll').events.click();
    }
  });
  await t.test("Quoting a task title leaves the row tooltip on the task ID", async () => {
    const priorTasks = state.tasks;
    state.tasks = {
      upcoming: [
        { type: 'unspecified', id: 'head-filler', title: 'Head filler', details: [], status: 'upcoming',
          order: 1, updated_at: new Date().toISOString() },
        { type: 'unspecified', id: 'hover-stays', title: 'Hover stays', details: [], status: 'upcoming',
          order: 2, updated_at: new Date().toISOString() }
      ],
      finished: [],
      updated_at: new Date().toISOString()
    };
    await refresh();
    try {
      const body = get('#tasks-upcoming-body');
      const row = body.children.find(item => item.title === 'hover-stays');
      const title = taskPart(row, 'task-title');
      assert.equal(title.className, 'task-title');
      documentEvents.click({ target: title, preventDefault: () => {} });
      assert.equal(get('#note').value.startsWith('RE: hover-stays'), true, 'the quote still lands');
      assert.equal(title.title, row.title, 'the quoted title retains the task ID tooltip');
      assert.equal(row.title, 'hover-stays', 'the row keeps the task ID as its tooltip');
    } finally {
      state.tasks = priorTasks;
      get('#note').value = '';
      await refresh();
    }
  });
  await t.test("The age timer repaints a receipt stamp that render minted earlier", async () => {
    const priorNotes = state.notes;
    state.notes = [{ id: 'age-note', text: 'age note',
      at: new Date(Date.now() - 4 * 60000).toISOString(), acknowledged_at: null, ack_kind: null,
      ack_text: null, ack_edited_at: null, replies: [], ack_edited_seen_count: 0, seen_at: null,
      task_id: null }];
    await refresh();
    try {
      const stamp = get('#history').children[0].children[2].children[3];
      assert.match(stamp.textContent, /4 mins ago/);
      stamp.dataset.at = new Date(Date.now() - 21 * 60000).toISOString();
      const repaint = intervals.filter(callback => callback.name === 'repaintReceiptAges').at(-1);
      assert.ok(repaint, 'an interval repaints the receipt ages');
      repaint();
      assert.match(stamp.textContent, /21 mins ago/);
    } finally {
      state.notes = priorNotes;
      await refresh();
    }
  });
  await t.test('Browser writes keep pending state through polls and restore failed changes', async () => {
    const keptState = state;
    const paths = ['/api/reports/pending-ack/ack-seen', '/api/skip-poll', '/api/fetch-jobs/pending-drop/drop'];
    let release = () => {};
    let writing;
    let polling;
    let releaseState = () => {};
    const hold = path => pendingWrites.set(path, new Promise(resolve => { release = resolve; }));
    try {
      get('#notes-tab').events.click();
      state = { notes: [], reports: [{ id: 'pending-ack', title: 'Pending receipt',
        updated_at: '2026-10-13T03:00:00', seen_at: '2026-10-13T03:00:00',
        latest_answer_acknowledged_at: '2026-10-13T03:01:00', ack_seen_at: null }],
        fetch_jobs: [{ id: 'pending-drop', url: 'https://example.test/saved.txt',
          approval: 'approved', status: 'saved', present: true, file: 'saved.txt',
          size: 1, sha256: 'a'.repeat(64) }], tasks: { upcoming: [], finished: [] } };
      await refresh();
      hold(paths[0]);
      writing = get('#mark-report-acks-seen').events.click();
      assert.equal(get('#report-pip').hidden, true, 'the receipt clears before the write answers');
      await refresh();
      assert.equal(get('#report-pip').hidden, true, 'a poll cannot undo the pending receipt');
      failedWrites.add(paths[0]);
      release(); await writing;
      assert.equal(get('#report-pip').hidden, false, 'a failed write restores the receipt');
      assert.equal(get('#mark-report-acks-seen').disabled, false, 'the failed action can be retried');
      pendingWrites.clear(); failedWrites.clear();

      hold(paths[1]);
      writing = get('#skip-poll').events.click();
      assert.equal(get('#skip-poll').getAttribute('aria-pressed'), 'true');
      await refresh();
      assert.equal(get('#skip-poll').getAttribute('aria-pressed'), 'true', 'pending toggles survive polls');
      failedWrites.add(paths[1]);
      release(); await writing;
      assert.equal(get('#skip-poll').getAttribute('aria-pressed'), 'false');
      pendingWrites.clear(); failedWrites.clear();

      hold(paths[2]);
      const row = get('#fetch-list').children.find(row => row.textContent.includes('saved.txt'));
      const drop = row.children.find(child => child.className === 'icon-button fetch-drop');
      await drop.events.click();
      writing = drop.events.click();
      assert.equal(get('#fetch-list').children.some(row => row.textContent.includes('saved.txt')), false);
      await refresh();
      assert.equal(get('#fetch-list').children.some(row => row.textContent.includes('saved.txt')), false);
      failedWrites.add(paths[2]);
      release(); await writing;
      assert.equal(get('#fetch-list').children.some(row => row.textContent.includes('saved.txt')), true);
      pendingWrites.clear(); failedWrites.clear();

      state.fetch_jobs.push({ id: 'pending-approval', url: 'https://example.test/approval.txt',
        status: 'queued', approval: 'pending', origin: 'agent' });
      await refresh();
      const remoteCount = remoteCalls.length;
      hold('/api/fetch-jobs/pending-approval/approve');
      const approval = get('#fetch-approvals').children.find(row => row.textContent.includes('approval.txt'));
      writing = approval.children.find(child => child.className === 'row tight fetch-decisions')
        .children.find(button => button.textContent === 'Approve').events.click();
      assert.equal(get('#fetch-approvals').children.some(row => row.textContent.includes('approval.txt')), false,
        'approval projects into history before the response');
      await refresh();
      assert.equal(remoteCalls.length, remoteCount, 'pending approval never authorizes a remote fetch');
      failedWrites.add('/api/fetch-jobs/pending-approval/approve');
      release(); await writing;
      assert.equal(get('#fetch-approvals').children.some(row => row.textContent.includes('approval.txt')), true);
      pendingWrites.clear(); failedWrites.clear();

      get('#fetch-url').value = 'https://example.test/new-job.txt';
      hold('/api/fetch-jobs');
      writing = get('#fetch-form').events.submit(event({}));
      assert.ok(get('#fetch-list').children.some(row => row.textContent.includes('new-job.txt')));
      await refresh();
      assert.equal(remoteCalls.length, remoteCount, 'a pending enqueue is not a claimed server job');
      failedWrites.add('/api/fetch-jobs');
      release(); await writing;
      assert.equal(get('#fetch-list').children.some(row => row.textContent.includes('new-job.txt')), false);
      assert.equal(get('#fetch-url').value, 'https://example.test/new-job.txt', 'failure keeps the URL');
      pendingWrites.clear(); failedWrites.clear();

      const readySendLabel = get('#send').textContent;
      get('#note').value = 'Pending draft stays safe';
      hold('/api/notes');
      writing = get('#form').events.submit(event({}));
      assert.equal(get('#note').value, '', 'submission clears before the pending message renders');
      assert.equal(get('#send').disabled, true, 'Send stays locked during a save');
      assert.equal(get('#send').textContent, get('#send').dataset.busyLabel, 'the pending label names the active save');
      assert.ok(get('#history').children.some(row => row.textContent.includes('Pending draft stays safe')));
      await refresh();
      assert.ok(get('#history').children.some(row => row.textContent.includes('Pending draft stays safe')));
      failedWrites.add('/api/notes');
      release(); await writing;
      assert.equal(get('#history').children.some(row => row.textContent.includes('Pending draft stays safe')), false);
      assert.equal(get('#note').value, 'Pending draft stays safe', 'failure keeps the message draft');
      assert.equal(get('#send').disabled, false);
      assert.equal(get('#send').textContent, readySendLabel, 'failure restores the ready label');
      pendingWrites.clear(); failedWrites.clear();

      const nextDraft = 'A new draft while a message is pending';
      get('#note').value = 'The first message';
      hold('/api/notes');
      writing = get('#form').events.submit(event({}));
      assert.equal(get('#note').value, '');
      get('#note').value = nextDraft;
      get('#note').events.input();
      failedWrites.add('/api/notes');
      release(); await writing;
      assert.equal(get('#note').value, nextDraft, 'failure does not replace a new draft');
      pendingWrites.clear(); failedWrites.clear();

      const repeatedDraft = 'The same text typed as a new draft';
      get('#note').value = repeatedDraft;
      hold('/api/notes');
      writing = get('#form').events.submit(event({}));
      assert.equal(get('#note').value, '');
      get('#note').value = repeatedDraft;
      get('#note').events.input();
      release(); await writing;
      assert.equal(get('#note').value, repeatedDraft, 'confirmation keeps a newly typed identical draft');
      assert.equal(get('#send').disabled, false);
      assert.equal(get('#send').textContent, readySendLabel, 'success restores the ready label');
      pendingWrites.clear(); failedWrites.clear();

      get('#note').value = 'The submitted draft';
      hold('/api/notes');
      writing = get('#form').events.submit(event({}));
      get('#note').value = 'A replacement draft';
      get('#note').events.input();
      get('#note').value = '';
      get('#note').events.input();
      failedWrites.add('/api/notes');
      release(); await writing;
      assert.equal(get('#note').value, '', 'failure does not restore over an intentionally cleared new draft');
      pendingWrites.clear(); failedWrites.clear();

      const nestedImage = context.renderMessageMarkdown('![outer ![shot\\]](https://example.test/inner.png)](https://example.test/outer.png)');
      assert.ok(nestedImage.includes('alt="outer shot]"'), 'nested image alt text retains escaped punctuation');
      const markdownDraft = '> A pending quote\n\n**Bold text**';
      get('#note').value = markdownDraft;
      hold('/api/notes');
      writing = get('#form').events.submit(event({}));
      const markdownRow = get('#history').children.find(row => row.textContent.includes('A pending quote'));
      const markdownBody = part(markdownRow, 'message-text');
      assert.ok(markdownBody, 'pending and confirmed messages use the same body style');
      assert.ok(markdownBody.innerHTML.includes('<blockquote>'));
      assert.ok(markdownBody.innerHTML.includes('<strong>'));
      assert.equal(get('#note').value, '');
      failedWrites.add('/api/notes');
      release(); await writing;
      assert.equal(get('#note').value, markdownDraft, 'Markdown rendering leaves the recovered source unchanged');
      pendingWrites.clear(); failedWrites.clear();

      state.reports[0].updated_at = reportRevision;
      state.reports[0].latest_answer_acknowledged_at = null;
      reportFields = 3;
      get('#report').fields = [text, picks, verdict];
      await refresh();
      get('#report-select').value = 'pending-ack';
      get('#reports-tab').events.click();
      await tick(); await tick();
      textInput.value = 'Pending report entry';
      hold('/api/reports/pending-ack/submit');
      writing = get('#report-form').events.submit(event({}));
      assert.equal(get('#report-agent-ack').hidden, false, 'a pending answer has a receipt');
      await refresh();
      assert.equal(get('#report-agent-ack').hidden, false, 'polls keep the pending answer receipt');
      failedWrites.add('/api/reports/pending-ack/submit');
      release(); await writing;
      assert.equal(get('#report-agent-ack').hidden, true, 'a failed answer restores the old receipt');
      assert.equal(textInput.value, 'Pending report entry', 'failure keeps the form entries');
      pendingWrites.clear(); failedWrites.clear();

      hold('/api/reports/pending-ack/unpublish');
      await get('#unpublish-report').events.click();
      writing = get('#unpublish-report').events.click();
      assert.equal(get('#report-select').children.some(option => option.value === 'pending-ack'), false);
      await refresh();
      assert.equal(get('#report-select').children.some(option => option.value === 'pending-ack'), false);
      failedWrites.add('/api/reports/pending-ack/unpublish');
      release(); await writing;
      assert.ok(get('#report-select').children.some(option => option.value === 'pending-ack'));
      pendingWrites.clear(); failedWrites.clear();
      get('#notes-tab').events.click();
      await tick();
      hold('/api/skip-poll');
      writing = get('#skip-poll').events.click();
      stateResponseWait = new Promise(resolve => { releaseState = resolve; });
      polling = refresh();
      await tick();
      release(); await writing;
      releaseState(); await polling;
      assert.equal(get('#skip-poll').getAttribute('aria-pressed'), 'true',
        'a poll captured before confirmation cannot undo the confirmed write');

    } finally {
      release(); releaseState();
      if (writing) await writing;
      if (polling) await polling;
      stateResponseWait = null;
      pendingWrites.clear(); failedWrites.clear();
      state = keptState;
      await refresh();
    }
  });


  await t.test('Highlights use safe Markdown tokens and leave code and destinations literal', () => {
    const render = context.renderMessageMarkdown;
    for (const [source, expected] of [
      ['==safe==', '<mark>safe</mark>'],
      ['==one **two**==', '<mark>one <strong>two</strong></mark>'],
      ['**==safe==**', '<strong><mark>safe</mark></strong>'],
      ['==one ~~gone~~==', '<mark>one <s>gone</s></mark>'],
      ['==[safe](https://example.com)==', '<mark><a href="https://example.com"'],
      ['==<script>alert(1)</script>==', '<mark>&lt;script&gt;alert(1)&lt;/script&gt;</mark>'],
    ]) {
      const html = render(source);
      assert.ok(html.includes(expected), source);
      assert.ok(!html.includes('<script>'), 'raw HTML stays escaped');
    }
    for (const source of ['`==code==`', '```text\n==code==\n```', String.raw`\==literal==`,
      '== unmarked ==', '==unclosed', '====', '[label](https://example.com/==file==)']) {
      assert.ok(!render(source).includes('<mark>'), source);
    }
    const unsafe = render('==[bad](javascript:alert(1))==');
    assert.ok(unsafe.includes('<mark>'));
    assert.ok(!unsafe.includes('href='));
    const image = render('==text== ![image](https://example.com/image.png =20x10)');
    assert.ok(image.includes('<mark>text</mark>'));
    assert.ok(image.includes('<img src="https://example.com/image.png"'));
    assert.ok(image.includes('width="20" height="10"'));
  });

  await t.test('Successful message receipts retain Markdown until the state poll', async () => {
    const keptState = state;
    const keptSendHandler = sendHandler;
    let releaseState = () => {};
    try {
      state = { ...state, notes: [], reports: [], fetch_jobs: [], tasks: { upcoming: [], finished: [] } };
      await refresh();
      get('#notes-tab').events.click();
      for (const withHtml of [false, true]) {
        const draft = '> test\n\ntest';
        const localHtml = context.renderMessageMarkdown(draft);
        const serverHtml = localHtml.replace('<blockquote>', '<blockquote class="server-rendered">');
        sendHandler = async posted => {
          const receipt = { ...posted, at: new Date().toISOString() };
          state.notes.push({ ...receipt, html: serverHtml });
          stateResponseWait = new Promise(resolve => { releaseState = resolve; });
          return response(withHtml ? { ...receipt, html: serverHtml } : receipt);
        };
        get('#note').value = draft;
        await get('#form').events.submit(event({}));
        const confirmedBody = get('#history').children.at(-1).children[0];
        assert.equal(confirmedBody.className, 'message-text',
          'successful confirmation keeps the rendered message body');
        assert.equal(confirmedBody.innerHTML, withHtml ? serverHtml : localHtml,
          'server HTML takes precedence, and a raw receipt keeps the local rendering');
        assert.equal(get('#note').value, '');
        releaseState();
        stateResponseWait = null;
        await tick();
        await refresh();
        assert.equal(get('#history').children.at(-1).children[0].innerHTML, serverHtml,
          'the following state poll uses the server rendering');
      }
    } finally {
      releaseState();
      stateResponseWait = null;
      sendHandler = keptSendHandler;
      state = keptState;
      await tick();
      await refresh();
    }
  });


  await t.test('The header interrupt carries context without a note or a dialog', async () => {
    const keptState = state;
    const notesBefore = sent.length;
    const draft = get('#note').value;
    state = { ...state, reports: [{ id: 'context-report', title: 'Context',
      updated_at: new Date().toISOString(), seen_at: new Date().toISOString() }],
      tasks: { finished: [], upcoming: [{ id: 'current-work', title: 'Current work',
        status: 'upcoming', order: 1, details: [] }] }, agent_interrupt_pending: false };
    try {
      get('#notes-tab').events.click();
      await refresh(); await tick();
      const button = get('#agent-interrupt');
      assert.equal(typeof button.events.click, 'function');
      assert.equal(button.getAttribute('aria-pressed'), 'false');
      let release;
      pendingWrites.set('/api/agent-interrupt', new Promise(resolve => { release = resolve; }));
      const pending = button.events.click();
      assert.equal(button.disabled, true);
      assert.equal(button.getAttribute('aria-busy'), 'true');
      const busyNode = button.children.at(-1);
      const busyIcon = busyNode?.getAttribute('class');
      await refresh();
      assert.equal(button.children.at(-1), busyNode, 'a poll does not restart the loader');
      await button.events.click();
      release();
      pendingWrites.delete('/api/agent-interrupt');
      await pending;
      assert.equal(interruptCalls.length, 1, 'a repeated busy press sends no second request');
      assert.equal(busyIcon, 'lucide lucide-loader-circle', 'a pending write uses the Lucide loader, not the flag');
      assert.equal(button.children.at(-1)?.getAttribute('class'), 'lucide lucide-flag', 'the flag returns after the write');
      assert.equal(interruptCalls.at(-1).tab, 'notes');
      assert.equal(button.getAttribute('aria-pressed'), 'true');
      assert.equal(button.getAttribute('aria-busy'), 'false');
      assert.equal(button.dataset.state, 'bad', 'the server-reported pending flag is red');
      assert.equal(button.disabled, false);
      assert.equal(interruptCalls.at(-1).armed, true, 'the request explicitly arms the flag');
      await button.events.click();
      assert.equal(interruptCalls.at(-1).armed, false, 'a second press explicitly disarms the flag');
      assert.equal(button.getAttribute('aria-pressed'), 'false');
      assert.equal(state.agent_interrupt_pending, false);
      await button.events.click();
      assert.equal(button.getAttribute('aria-pressed'), 'true', 'a cleared flag can be armed again');
      state.agent_interrupt_pending = false;
      await refresh();
      assert.equal(button.getAttribute('aria-pressed'), 'false', 'the hook consumption clears the button');
      assert.equal(button.dataset.state, '');
      get('#reports-tab').events.click();
      await tick(); await tick();
      const selected = get('#report-select').value;
      const reportStatus = get('#report-status').textContent;
      await button.events.click();
      assert.equal(interruptCalls.at(-1).tab, 'reports');
      assert.equal(interruptCalls.at(-1).report_id, selected);
      assert.equal(get('#report-select').value, selected);
      assert.equal(get('#report-status').textContent, reportStatus, 'the action has no report status line');
      state.agent_interrupt_pending = false;
      await refresh();
      get('#tasks-tab').events.click();
      await button.events.click();
      assert.equal(interruptCalls.at(-1).tab, 'tasks');
      assert.equal(interruptCalls.at(-1).armed, true);
      failedWrites.add('/api/agent-interrupt');
      await button.events.click();
      assert.equal(button.getAttribute('aria-pressed'), 'true', 'a refused disarm retains the pending flag');
      failedWrites.delete('/api/agent-interrupt');
      await button.events.click();
      assert.equal(button.getAttribute('aria-pressed'), 'false', 'a refused disarm can be retried');
      assert.equal(sent.length, notesBefore, 'the action writes no note');
      assert.equal(get('#note').value, draft, 'the action keeps the composer draft');
      state.agent_interrupt_pending = false;
      await refresh();
      failedWrites.add('/api/agent-interrupt');
      await button.events.click();
      assert.equal(button.disabled, false, 'a failed request can be retried');
      assert.equal(button.children.at(-1).getAttribute('class'), 'lucide lucide-flag', 'a failed write restores the flag');
      assert.equal(button.getAttribute('aria-busy'), 'false');
      assert.equal(button.getAttribute('aria-pressed'), 'false');
      assert.match(button.title, /Write failed/);
    } finally {
      pendingWrites.delete('/api/agent-interrupt');
      failedWrites.delete('/api/agent-interrupt');
      state = keptState;
      await refresh();
    }
  });

  await t.test('WebSockets route JSON, retain binary HTTP, queue changes, and reject lost replies', async () => {
    const sockets = [];
    class SocketStub {
      constructor(url) { this.url = String(url); this.readyState = 0; this.events = {}; this.sent = []; sockets.push(this); }
      addEventListener(name, callback) { this.events[name] = callback; }
      send(text) { this.sent.push(JSON.parse(text)); }
      close() { this.readyState = 3; this.events.close?.(); }
      emit(value) { this.events.message({data: JSON.stringify(value)}); }
    }
    context.WebSocket = SocketStub;
    context.Response = Response;
    context.window.location = {href: 'https://preview.example/'};
    context.connectSocket();
    const socket = sockets[0];
    assert.equal(socket.url, 'wss://preview.example/api/ws');
    socket.readyState = 1;
    socket.events.open();
    const states = () => socket.sent.filter(value => value.path === '/api/state');
    assert.equal(states().length, 1);
    socket.emit({type: 'change'});
    socket.emit({type: 'change'});
    const snapshot = {...state, notes: [], reports: [], fetch_jobs: []};
    const reply = (call, body, status = 200) => socket.emit({type: 'response', id: call.id, status,
      body: typeof body === 'string' ? body : JSON.stringify(body), contentType: 'application/json'});
    reply(states()[0], snapshot);
    await tick(); await tick();
    assert.equal(states().length, 2, 'changes during refresh cause one final refresh');
    reply(states()[1], snapshot);
    await tick(); await tick();
    const json = context.request('/api/markdown', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: '{}'});
    const call = socket.sent.at(-1);
    assert.equal(call.path, '/api/markdown');
    assert.equal(call.method, 'POST');
    reply(call, '<strong>socket</strong>');
    assert.equal(await (await json).text(), '<strong>socket</strong>');
    const before = socket.sent.length;
    await context.attempt('/api/markdown', {method: 'POST', body: new Blob(['bytes'])}, 100);
    assert.equal(socket.sent.length, before, 'binary bodies keep HTTP');
    socket.emit({type: 'heartbeat'});
    assert.equal(socket.sent.at(-1).type, 'pong');
    const lost = context.request('/api/markdown', {retryOnFailure: false});
    context.WebSocket = undefined;
    socket.close();
    await assert.rejects(lost, /closed/i);
    await tick(); await tick();
    assert.ok(intervals.filter(callback => callback.name === 'refreshState').length > 1,
      'the original polling path starts again after disconnect');
  });

});

test('the phone footer keeps Send beside a long status', () => {
  // The stacked key and status lines must shrink inside the footer row, so a long staged-files
  // line wraps within its own column and never pushes the Send note button onto a line of its own.
  const css = fs.readFileSync(path.join(__dirname, '../assets/style.css'), 'utf8');
  const rule = /\.compose\s+\.row\s*>\s*\.stack\s*{[^}]*}/.exec(css);
  assert.ok(rule, 'the footer stack has its own rule');
  assert.match(rule[0], /flex:\s*1\s+1\s+0\b/, 'the status stack starts from zero width so Send stays on its line');
});

test('a wide table scrolls sideways on its own and keeps its words whole', () => {
  // Report and message text can wrap anywhere. Each table scrolls independently, as on GitHub.
  // Cells wrap only between words, so longer words stay whole.
  const css = fs.readFileSync(path.join(__dirname, '../assets/style.css'), 'utf8');
  const rules = [...css.replace(/\/\*[\s\S]*?\*\//g, '').matchAll(/([^{}]+)\{([^{}]*)\}/g)];
  const body = selector => {
    const rule = rules.find(match => match[1].split(',').some(name => name.trim() === selector));
    assert.ok(rule, `${selector} has a rule`);
    return rule[2];
  };
  for (const selector of ['.report table', '.message-text table']) {
    for (const [property, value] of [['display', 'block'], ['width', 'max-content'], ['max-width', '100%'], ['overflow-x', 'auto']]) {
      assert.match(body(selector), new RegExp(`(?:^|;)\\s*${property}:\\s*${value}\\s*(?:;|$)`));
    }
  }
  for (const selector of ['.report th', '.report td', '.message-text th', '.message-text td']) {
    assert.match(body(selector), /(?:^|;)\s*overflow-wrap:\s*normal\s*(?:;|$)/);
  }
});

test('the toolbar and filter controls share the ui control sizes', () => {
  // Comments ahead of a rule would ride in its selector text, so the sheet reads bare.
  const css = fs.readFileSync(path.join(__dirname, '../assets/style.css'), 'utf8')
    .replace(new RegExp('/\\*[\\s\\S]*?\\*/', 'g'), '');
  const html = fs.readFileSync(path.join(__dirname, '../assets/index.html'), 'utf8');
  const rules = [...css.matchAll(/([^{}]+)\{([^{}]*)\}/g)];
  // The report icon buttons ride the shared icon-button class, so no rule names a square for them.
  for (const match of rules) {
    const namesReportButton = /#(copy|refresh|mark-report-acks-seen|next-report|unpublish)-report/.test(match[1]);
    assert.ok(!namesReportButton || !/width:\s*\d+px/.test(match[2]),
      'no fixed square names a report button');
  }
  for (const id of ['copy-report', 'refresh-report', 'mark-report-acks-seen', 'next-report', 'unpublish-report']) {
    assert.match(html, new RegExp(`id="${id}" class="icon-button"`), `${id} rides the icon-button class`);
  }
  // A select keeps the message search's own height, so header and toolbar controls read as one row.
  const heightOf = body => (body.match(/(?<!min-)height:\s*([^;]+);/) || [])[1];
  const selectRule = rules.find(match => match[1].trim() === 'select');
  const namesSelector = (match, name) =>
    match[1].split(',').map(part => part.trim()).includes(name);
  const searchRule = rules.find(match => namesSelector(match, '#log-search'));
  assert.equal(heightOf(selectRule[2]), heightOf(searchRule[2]),
    'the select height follows the message search height');
  // The task filter keeps the report select's wrap and chevron.
  assert.match(html, /class="select-wrap task-filter"/, 'the task filter rides the shared select wrap');
  assert.match(html, /<select[^>]+id="task-type-filter"[^>]+aria-label="[^"]+"/,
    'the task type select retains an accessible name without a visible label');
  // The downloads url input keeps the same single-line control height as the search and the selects.
  const fetchRule = rules.find(match => match[1].trim() === '.fetch-controls input');
  assert.equal(heightOf(fetchRule[2]), heightOf(searchRule[2]),
    'the downloads input height follows the message search height');
});


test('scroll fades grow over a longer distance than their maximum depth', () => {
  const css = fs.readFileSync(path.join(__dirname, '../assets/style.css'), 'utf8');
  const lengths = new Map([...css.matchAll(/--(edge-fade|edge-ramp):\s*([\d.]+)px/g)]
    .map(([, name, value]) => [name, Number(value)]));
  assert.ok(lengths.get('edge-ramp') > lengths.get('edge-fade'), 'the scroll ramp must be longer than the fade depth');
  const ranges = [...css.matchAll(/animation-timeline:\s*(?:auto,\s*)?scroll\(self\),\s*scroll\(self\);\s*animation-range:\s*([^;]+);/g)]
    .map(match => match[1]);
  assert.ok(ranges.length > 0);
  for (const range of ranges) {
    assert.ok(range.includes('var(--edge-ramp)'), 'all scrollable ranges use the longer ramp');
    assert.equal(range.includes('var(--edge-fade)'), false, 'maximum mask depth does not set ramp distance');
  }
});

test('the queue end uses an accented line without an end card', () => {
  const css = fs.readFileSync(path.join(__dirname, '../assets/style.css'), 'utf8');
  assert.doesNotMatch(css, /\.task-drop-end/);
  const hit = css.match(/\.task-list\[data-dragging\]\s*\{[^}]*padding-bottom:\s*(\d+)px/);
  assert.ok(hit && px(hit[1]) > 0, 'the list keeps an end-drop hit region during a drag');
  const line = css.match(/\.task-list\[data-drop\]\s*\{[^}]*box-shadow:\s*inset\s+0\s+(-?\d+)px/);
  assert.ok(line && Number(line[1]) < 0, 'the accented line marks the list bottom');
});
