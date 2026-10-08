'use strict';

// The proxy injects this file into the preview page, so Android Chrome can offer
// the installed-app frame. An installable frame is optional: the preview works
// without it. The app takes no click on an outside link; the share target is the
// only path in.
if ('serviceWorker' in navigator) {
  navigator.serviceWorker.register('/pwa/service-worker.js').catch(() => {});
}
