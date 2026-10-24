# Recipe: Shared modal manager

Route every modal through one native `<dialog>` element opened with `showModal()`, mounting reusable `<template>` views into the shared shell while preserving top-layer inertness, focus restoration, and `returnValue` resolution.

## Rules

- Open the shared `<dialog>` with `showModal()`, never `show()` or the `open` attribute, and never place `tabindex` on `<dialog>`.
- Wire `aria-labelledby` to `#modal-title`, place the symmetric Lucide `x` exit button at the top right (`Clause 6`, `Clause 15`), and place action buttons at the bottom right inside `<form method="dialog">`.
- Put `autofocus` on the first meaningful control or safe non-destructive button (`Cancel`), never on the top-right `x` exit button, and suppress the initial `:focus-visible` ring on open until keyboard navigation (`Tab` or arrow keys).
- Store `document.activeElement` before opening and restore focus to the invoking trigger on `close`.

## Shared shell and templates

```html
<dialog id="modal-shell" class="modal-shell" aria-labelledby="modal-title">
  <form id="modal-form" method="dialog" class="modal-card">
    <header class="modal-header">
      <h2 id="modal-title" class="modal-title"></h2>
      <button type="submit" value="cancel" class="icon-btn" aria-label="Close dialog">
        <svg class="lucide lucide-x" viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M18 6 6 18"/><path d="m6 6 12 12"/></svg>
      </button>
    </header>
    <div id="modal-body" class="modal-body"></div>
    <footer id="modal-actions" class="modal-actions"></footer>
  </form>
</dialog>

<template data-modal-template="confirm">
  <p data-slot="message"></p>
  <div data-slot="actions">
    <button type="submit" value="cancel" class="btn" autofocus>Cancel</button>
    <button type="submit" value="confirm" class="btn btn-primary" data-slot="confirm-label">Confirm</button>
  </div>
</template>
```

## Controller

```js
export function createModalManager(shell = document.getElementById('modal-shell')) {
  const titleEl = shell.querySelector('#modal-title');
  const bodyEl = shell.querySelector('#modal-body');
  const actionsEl = shell.querySelector('#modal-actions');
  const registry = new Map();
  let activeTrigger = null;

  for (const tpl of document.querySelectorAll('template[data-modal-template]')) {
    registry.set(tpl.dataset.modalTemplate, tpl);
  }

  return {
    register(name, templateEl) {
      registry.set(name, templateEl);
    },
    open(name, { title = '', slots = {}, trigger = document.activeElement } = {}) {
      const tpl = registry.get(name);
      if (!tpl) throw new Error(`Unknown modal template: ${name}`);
      if (shell.open) shell.close('replaced');
      activeTrigger = trigger;
      titleEl.textContent = title;
      const frag = tpl.content.cloneNode(true);
      for (const [slot, text] of Object.entries(slots)) {
        const node = frag.querySelector(`[data-slot="${slot}"]`);
        if (node) node.textContent = text;
      }
      const actionSlot = frag.querySelector('[data-slot="actions"]');
      actionsEl.replaceChildren(...(actionSlot ? actionSlot.childNodes : []));
      if (actionSlot) actionSlot.remove();
      bodyEl.replaceChildren(frag);
      shell.returnValue = 'cancel';
      shell.showModal();
      return new Promise(resolve => {
        shell.addEventListener(
          'close',
          () => {
            const triggerEl = activeTrigger;
            activeTrigger = null;
            if (triggerEl && typeof triggerEl.focus === 'function') triggerEl.focus();
            resolve(shell.returnValue || 'cancel');
          },
          { once: true },
        );
      });
    },
  };
}
```
