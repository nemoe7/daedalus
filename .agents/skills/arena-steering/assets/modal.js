// Shared native `<dialog>` modal manager (`nuu` modal-manager recipe).

export function createModalManager(
  shell = typeof document !== "undefined"
    ? document.getElementById("modal-shell")
    : null
) {
  if (!shell) {
    return {
      register() {},
      async open() {
        return "confirm";
      },
    };
  }
  const titleEl = shell.querySelector("#modal-title");
  const bodyEl = shell.querySelector("#modal-body");
  const actionsEl = shell.querySelector("#modal-actions");
  const closeBtn = shell.querySelector(".close-button");
  const registry = new Map();
  let activeTrigger = null;
  let activeClose = null;

  for (const tpl of document.querySelectorAll("template[data-modal-template]")) {
    registry.set(tpl.dataset.modalTemplate, tpl);
  }

  return {
    register(name, templateEl) {
      registry.set(name, templateEl);
    },
    open(
      name,
      { title = "", slots = {}, trigger = document.activeElement } = {}
    ) {
      const tpl = registry.get(name);
      if (!tpl) throw new Error(`Unknown modal template: ${name}`);
      if (shell.open) {
        activeClose?.("replaced");
        shell.close("replaced");
      }
      activeTrigger = trigger;
      titleEl.textContent = title;
      const frag = tpl.content.cloneNode(true);
      for (const [slot, text] of Object.entries(slots)) {
        const node = frag.querySelector(`[data-slot="${slot}"]`);
        if (node) node.textContent = text;
      }
      if (arguments[1]?.htmlSlots) {
        for (const [slot, html] of Object.entries(arguments[1].htmlSlots)) {
          const node = frag.querySelector(`[data-slot="${slot}"]`);
          if (node) node.innerHTML = html;
        }
      }
      const actionSlot = frag.querySelector('[data-slot="actions"]');
      actionsEl.replaceChildren(...(actionSlot ? actionSlot.childNodes : []));
      if (actionSlot) actionSlot.remove();
      bodyEl.replaceChildren(frag);
      const firstInput = bodyEl.querySelector("input, select, textarea");
      if (firstInput) {
        firstInput.setAttribute("autofocus", "");
        closeBtn?.removeAttribute("autofocus");
      } else {
        closeBtn?.setAttribute("autofocus", "");
      }
      shell.returnValue = "cancel";
      shell.showModal();
      return new Promise((resolve) => {
        const finish = (value) => {
          shell.removeEventListener("close", onClose);
          if (activeClose === finish) activeClose = null;
          const triggerEl = activeTrigger;
          activeTrigger = null;
          if (value !== "replaced" && triggerEl && typeof triggerEl.focus === "function") {
            triggerEl.focus();
          }
          resolve(value);
        };
        const onClose = () => {
          if (!shell.open) finish(shell.returnValue || "cancel");
        };
        activeClose = finish;
        shell.addEventListener("close", onClose);
      });
    },
  };
}
