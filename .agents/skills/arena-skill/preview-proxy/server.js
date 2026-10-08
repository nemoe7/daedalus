'use strict';

const fs = require('node:fs');
const http = require('node:http');
const https = require('node:https');
const path = require('node:path');
const crypto = require('node:crypto');

const PORT = Number(process.env.PORT || 8080);
const COOKIE_NAME = 'arena_preview_target';
const COOKIE_MAX_AGE = 12 * 60 * 60;
const COOKIE_SECRET = process.env.PROXY_COOKIE_SECRET || '';
const HOP_BY_HOP_HEADERS = [
  'connection', 'keep-alive', 'proxy-authenticate', 'proxy-authorization',
  'te', 'trailer', 'transfer-encoding', 'upgrade', 'proxy-connection'
];
// Browser navigation metadata triggers Arena's mobile preview gate, so the proxy
// drops it before the upstream request.
const FETCH_METADATA_HEADERS = [
  'sec-fetch-dest', 'sec-fetch-mode', 'sec-fetch-site', 'sec-fetch-user', 'referer'
];
const ALLOWED_METHODS = new Set(['GET', 'HEAD', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS']);
const PREVIEW_HOST = /^sbx-[a-z0-9-]+\.arena\.site$/i;
const ASSET_TYPES = {
  'register.js': 'text/javascript; charset=utf-8',
  'service-worker.js': 'text/javascript; charset=utf-8',
  'icon.svg': 'image/svg+xml'
};
const ASSETS = path.join(__dirname, 'assets');
// The head rewrite buffers one small page; a larger body streams untouched.
const HEAD_CAP = 1_000_000;

if (COOKIE_SECRET.length < 32) {
  console.error('PROXY_COOKIE_SECRET must be at least 32 characters.');
  process.exit(1);
}

function isAllowedPreview(url) {
  return url.protocol === 'https:' &&
    !url.username && !url.password && !url.port &&
    PREVIEW_HOST.test(url.hostname) &&
    url.pathname === '/' && !url.search && !url.hash;
}

function parseSelectedUrl(raw) {
  let url;
  try {
    url = new URL(raw);
  } catch {
    throw new Error('The url parameter must be a valid, URL-encoded HTTPS preview URL.');
  }
  if (!isAllowedPreview(url)) {
    throw new Error('Only HTTPS preview roots on sbx-*.arena.site are accepted.');
  }
  return url;
}

function signOrigin(origin) {
  const payload = Buffer.from(origin, 'utf8').toString('base64url');
  const signature = crypto.createHmac('sha256', COOKIE_SECRET).update(payload).digest('base64url');
  return `${payload}.${signature}`;
}

function verifyOriginToken(token) {
  if (!token || typeof token !== 'string') return null;
  const dot = token.lastIndexOf('.');
  if (dot <= 0 || dot === token.length - 1) return null;
  const payload = token.slice(0, dot);
  const given = Buffer.from(token.slice(dot + 1));
  const expected = Buffer.from(
    crypto.createHmac('sha256', COOKIE_SECRET).update(payload).digest('base64url')
  );
  if (given.length !== expected.length || !crypto.timingSafeEqual(given, expected)) return null;
  try {
    const url = new URL(`${Buffer.from(payload, 'base64url').toString('utf8')}/`);
    return isAllowedPreview(url) ? url.origin : null;
  } catch {
    return null;
  }
}

function readCookie(header, name) {
  if (!header) return null;
  for (const part of header.split(';')) {
    const item = part.trim();
    const eq = item.indexOf('=');
    if (eq > 0 && item.slice(0, eq) === name) return item.slice(eq + 1);
  }
  return null;
}

function withoutProxyCookie(header) {
  if (!header) return '';
  return header.split(';')
    .map(item => item.trim())
    .filter(Boolean)
    .filter(item => {
      const eq = item.indexOf('=');
      return item.slice(0, eq < 0 ? item.length : eq) !== COOKIE_NAME;
    })
    .join('; ');
}

function makeTarget(origin, pathname, search) {
  const target = new URL(`${origin}/`);
  target.pathname = pathname || '/';
  target.search = search || '';
  return target;
}

function sendText(res, status, text) {
  res.writeHead(status, {
    'content-type': 'text/plain; charset=utf-8',
    'cache-control': 'no-store',
    'x-content-type-options': 'nosniff'
  });
  res.end(text);
}

function rewriteArenaCookie(cookie) {
  // The browser is on the tailnet host, so make Arena's cookie host-only there;
  // the proxy forwards it upstream on later requests.
  return cookie.replace(/;\s*domain=\.?arena\.site(?=;|$)/i, '');
}

function pwaHead() {
  return '<link rel="manifest" href="/pwa/manifest.webmanifest">' +
    '<script src="/pwa/register.js" defer></script>';
}

function injectPwa(html) {
  const found = html.search(/<\/head\s*>/i);
  return found < 0 ? null : html.slice(0, found) + pwaHead() + html.slice(found);
}

function manifestJson(origin) {
  return JSON.stringify({
    name: 'Arena preview',
    short_name: 'Preview',
    start_url: origin ? `/?url=${encodeURIComponent(`${origin}/`)}` : '/',
    scope: '/',
    display: 'standalone',
    background_color: '#1f2430',
    theme_color: '#1f2430',
    // A launch reuses the open window, so the preview stays in-page.
    launch_handler: { client_mode: 'navigate-existing' },
    // The installed app takes a shared link; Android has no per-link "open with"
    // entry for a web app, so the share sheet is the system path in.
    share_target: {
      action: '/pwa/share',
      method: 'GET',
      params: { title: 'title', text: 'text', url: 'url' }
    },
    icons: [{ src: '/pwa/icon.svg', sizes: 'any', type: 'image/svg+xml' }]
  });
}

function sharePreview(incoming, res) {
  const shared = incoming.searchParams.get('url') ||
    (incoming.searchParams.get('text') || '').match(/https:\/\/sbx-[a-z0-9-]+\.arena\.site\/?/i)?.[0] ||
    '';
  let selected;
  try {
    selected = parseSelectedUrl(shared);
  } catch {
    return sendText(res, 400, 'Share an Arena preview link: https://sbx-*.arena.site/');
  }
  res.writeHead(302, {
    location: `/?url=${encodeURIComponent(`${selected.origin}/`)}`,
    'cache-control': 'no-store'
  });
  return res.end();
}

function servePwa(incoming, req, res) {
  if (incoming.pathname === '/pwa/share') return sharePreview(incoming, res);
  if (incoming.pathname === '/pwa/manifest.webmanifest') {
    const origin = verifyOriginToken(readCookie(req.headers.cookie, COOKIE_NAME));
    res.writeHead(200, {
      'content-type': 'application/manifest+json; charset=utf-8',
      'cache-control': 'no-store',
      'x-content-type-options': 'nosniff'
    });
    return res.end(manifestJson(origin));
  }
  const name = incoming.pathname.slice('/pwa/'.length);
  if (!Object.hasOwn(ASSET_TYPES, name) || name.includes('/')) {
    return sendText(res, 404, 'Unknown installed-app file.');
  }
  let body;
  try {
    body = fs.readFileSync(path.join(ASSETS, name));
  } catch {
    return sendText(res, 500, 'The installed-app file is missing.');
  }
  const headers = {
    'content-type': ASSET_TYPES[name],
    'cache-control': 'no-store',
    'x-content-type-options': 'nosniff'
  };
  // The worker controls the whole proxy origin, not only the /pwa/ prefix.
  if (name === 'service-worker.js') headers['service-worker-allowed'] = '/';
  res.writeHead(200, headers);
  return res.end(body);
}

function rewriteHtml(upstreamRes, res, status, headers) {
  const chunks = [];
  let size = 0;
  let capped = false;
  const flush = () => {
    capped = true;
    res.writeHead(status, headers);
    for (const item of chunks) res.write(item);
  };
  upstreamRes.on('data', chunk => {
    if (capped) return;
    size += chunk.length;
    if (size > HEAD_CAP) {
      // Too large to rewrite: flush the head buffer and stream the rest.
      flush();
      res.write(chunk);
      upstreamRes.pipe(res);
      return;
    }
    chunks.push(chunk);
  });
  upstreamRes.on('end', () => {
    if (capped) return;
    const html = Buffer.concat(chunks).toString('utf8');
    const rewritten = injectPwa(html);
    const body = Buffer.from(rewritten === null ? html : rewritten, 'utf8');
    headers['content-length'] = String(body.length);
    res.writeHead(status, headers);
    res.end(body);
  });
  upstreamRes.on('error', err => {
    if (!res.headersSent) sendText(res, 502, `Upstream request failed: ${err.message}`);
    else res.destroy(err);
  });
}

const server = http.createServer((req, res) => {
  if (!ALLOWED_METHODS.has(req.method)) {
    return sendText(res, 405, 'Method not allowed.');
  }

  let incoming;
  try {
    incoming = new URL(req.url, 'http://proxy.invalid');
  } catch {
    return sendText(res, 400, 'Invalid request URL.');
  }

  if (incoming.pathname === '/healthz') return sendText(res, 200, 'ok');
  if (incoming.pathname.startsWith('/pwa/')) return servePwa(incoming, req, res);

  let target;
  let targetCookie = null;
  try {
    if (incoming.searchParams.has('url')) {
      if (incoming.pathname !== '/') {
        return sendText(res, 400, 'Pass url only on the viewer root: /?url=<encoded-preview-url>');
      }
      const selected = parseSelectedUrl(incoming.searchParams.get('url'));
      target = selected;
      targetCookie = `${COOKIE_NAME}=${signOrigin(selected.origin)}; Path=/; Max-Age=${COOKIE_MAX_AGE}; HttpOnly; Secure; SameSite=Lax`;
    } else {
      const origin = verifyOriginToken(readCookie(req.headers.cookie, COOKIE_NAME));
      if (!origin) {
        return sendText(res, 400, 'Choose a preview first: /?url=<URL-encoded https://sbx-….arena.site/>');
      }
      target = makeTarget(origin, incoming.pathname, incoming.search);
    }
  } catch (err) {
    return sendText(res, 400, err.message || 'Invalid preview URL.');
  }

  const headers = { ...req.headers };
  for (const name of HOP_BY_HOP_HEADERS) delete headers[name];
  for (const name of FETCH_METADATA_HEADERS) delete headers[name];
  const forwardedCookies = withoutProxyCookie(headers.cookie);
  if (forwardedCookies) headers.cookie = forwardedCookies;
  else delete headers.cookie;
  headers.host = target.host;

  // The target origin comes from parseSelectedUrl or the signed cookie, and isAllowedPreview
  // pins both to HTTPS roots on sbx-*.arena.site with no credentials, port or path. CodeQL
  // cannot model that pattern allowlist, so .github/codeql/codeql-config.yml excludes
  // js/request-forgery for this forwarding request.
  const upstream = https.request(target, { method: req.method, headers, timeout: 120000 },
    upstreamRes => {
      const responseHeaders = { ...upstreamRes.headers };
      for (const name of HOP_BY_HOP_HEADERS) delete responseHeaders[name];
      if (responseHeaders['set-cookie']) {
        const cookies = responseHeaders['set-cookie'].map(rewriteArenaCookie);
        responseHeaders['set-cookie'] = targetCookie ? [targetCookie, ...cookies] : cookies;
      } else if (targetCookie) {
        responseHeaders['set-cookie'] = [targetCookie];
      }
      const location = responseHeaders.location;
      if (location) {
        try {
          const redirected = new URL(location, target);
          if (redirected.origin === target.origin) {
            responseHeaders.location = `${redirected.pathname}${redirected.search}${redirected.hash}`;
          }
        } catch {
          // Leave malformed or relative Location values unchanged.
        }
      }
      responseHeaders['cache-control'] = 'no-store';
      const status = upstreamRes.statusCode || 502;
      const isHtml = /\btext\/html\b/i.test(String(responseHeaders['content-type'] || ''));
      if (isHtml && req.method !== 'HEAD' && !responseHeaders['content-encoding']) {
        delete responseHeaders['content-length'];
        return rewriteHtml(upstreamRes, res, status, responseHeaders);
      }
      res.writeHead(status, responseHeaders);
      upstreamRes.pipe(res);
    });

  upstream.setTimeout(120000, () => upstream.destroy(new Error('Upstream timeout')));
  upstream.on('error', err => {
    if (!res.headersSent) sendText(res, 502, `Upstream request failed: ${err.message}`);
    else res.destroy(err);
  });
  req.on('aborted', () => upstream.destroy());
  req.pipe(upstream);
});

server.headersTimeout = 65_000;
server.requestTimeout = 10 * 60 * 1000;
server.listen(PORT, '127.0.0.1', () => {
  console.log(`Arena preview proxy listening on 127.0.0.1:${PORT}`);
  console.log('Allowed target pattern: https://sbx-*.arena.site/');
});
