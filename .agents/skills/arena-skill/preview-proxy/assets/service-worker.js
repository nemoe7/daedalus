'use strict';

// A minimal worker with no caching, so the preview never serves stale bytes. It
// holds a fetch listener only because Android Chrome asks for one before offering
// the install.
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', event => event.waitUntil(self.clients.claim()));
self.addEventListener('fetch', () => {});
