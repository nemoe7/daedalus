# Recipe: Shared modal manager

Route every modal through one native `<dialog>` element opened with `showModal()`, mounting reusable `<template>` views into the shared shell while preserving top-layer inertness, focus restoration, and `returnValue` resolution.

![Modal Close Button Dark-Mode Example](../examples/modal-close-button.png)

![Modal Autofocus Dark-Mode Example](../examples/modal-autofocus.png)

![Modal Close States Dark-Mode Example](../examples/modal-close-states.png)

## Rules

- Open the shared `<dialog>` with `showModal()`, never `show()` or the `open` attribute, and never place `tabindex` on `<dialog>`.
- Wire `aria-labelledby` to `#modal-title`, place the borderless, transparent Lucide `x` `.close-button` (`32x32 px`, `8 px` padding, `-8 px` margin) exit button at the top right (`Clause 6`) as the sole dismiss button without a redundant footer `Cancel` button, aligned to the title and content edge with a negative-margin hit box and hover/focus/active surface and color feedback, and place primary action buttons at the bottom right inside `<form method="dialog">`.
- Put `autofocus` on the first form control inside the dialog, or on the top-right `.close-button` when there are no text inputs, shifting its `X` glyph color from `var(--muted)` to `var(--text)` on `:focus-visible`.
- Style `.close-button` as a borderless, transparent `32x32 px` control (`8 px` padding, `-8 px` margin) aligned to the content edge that reveals a surface background (`var(--surface-2)`), border (`var(--line)`), and text-color shift (`var(--text)` on hover and `:focus-visible`, `var(--accent)` on `:active`).
- Gate every delete action behind a confirmation modal that states what will be deleted, uses the top-right `.close-button` (`X`) as the dismiss control without a redundant `Cancel` button, and places the destructive confirmation button at the bottom right.
- Store `document.activeElement` before opening and restore focus to the invoking trigger on `close`.

## Shared shell and templates

```html
<dialog id="modal-shell" class="modal-shell" aria-labelledby="modal-title">
  <form id="modal-form" method="dialog" class="modal-card">
    <header class="modal-header">
      <h2 id="modal-title" class="modal-title"></h2>
      <button type="submit" value="cancel" class="close-button" aria-label="Close dialog">
        <svg class="lucide lucide-x" viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M18 6 6 18"/><path d="m6 6 12 12"/></svg>
      </button>
    </header>
    <div id="modal-body" class="modal-body"></div>
    <footer id="modal-actions" class="modal-actions"></footer>
  </form>
</dialog>

<template data-modal-template="confirm-delete">
  <p data-slot="message"></p>
  <div data-slot="actions">
    <button type="submit" value="confirm" class="button button--danger" data-slot="confirm-label">Delete</button>
  </div>
</template>
```

## Styles (`style.css`)

```css
.modal-card {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  padding: 20px;
  border: 1px solid var(--line);
  border-radius: var(--radius);
  background: var(--surface);
  color: var(--text);
}

.modal-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--space-3);
}

.close-button {
  position: relative;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: var(--row);
  height: var(--row);
  padding: var(--space-2);
  margin: calc(-1 * var(--space-2)) calc(-1 * var(--space-2)) calc(-1 * var(--space-2)) 0;
  border: 1px solid transparent;
  border-radius: var(--radius);
  background: transparent;
  color: var(--muted);
  transition: background var(--fast) var(--ease), border-color var(--fast) var(--ease), color var(--fast) var(--ease);
}

.close-button:focus-visible {
  border-color: var(--line);
  background: var(--surface-2);
  color: var(--text);
  outline: var(--focus-ring) solid var(--focus);
  outline-offset: 2px;
}

@media (hover: hover) and (pointer: fine) {
  .close-button:hover {
    border-color: var(--line);
    background: var(--surface-2);
    color: var(--text);
  }
}

.close-button:active {
  border-color: var(--line);
  background: var(--surface-2);
  color: var(--accent);
}

.modal-actions {
  display: flex;
  justify-content: flex-end;
  gap: var(--space-2);
}
```

## Controller

```js
export function createModalManager(shell = document.getElementById('modal-shell')) {
  const titleEl = shell.querySelector('#modal-title');
  const bodyEl = shell.querySelector('#modal-body');
  const actionsEl = shell.querySelector('#modal-actions');
  const closeBtn = shell.querySelector('.close-button');
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
      const firstInput = bodyEl.querySelector('input, select, textarea');
      if (firstInput) {
        firstInput.setAttribute('autofocus', '');
        closeBtn?.removeAttribute('autofocus');
      } else {
        closeBtn?.setAttribute('autofocus', '');
      }
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
