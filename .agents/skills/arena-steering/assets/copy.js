// Copy through a temporary selection so it also works in embedded previews.
export function copyText(text, trigger = null) {
  const field = document.createElement("textarea");
  const previous = document.activeElement;
  const selection = document.getSelection();
  const ranges = selection ? Array.from({ length: selection.rangeCount }, (_, i) => selection.getRangeAt(i).cloneRange()) : [];
  field.value = String(text ?? "");
  field.setAttribute("readonly", "");
  field.style.cssText = "position:fixed;left:0;top:0;opacity:0;pointer-events:none";
  try {
    (trigger?.closest("dialog[open]") || document.body).append(field);
    field.select();
    return typeof document.execCommand === "function" && document.execCommand("copy") === true;
  } catch {
    return false;
  } finally {
    field.remove();
    if (previous instanceof HTMLElement && previous.isConnected) previous.focus({ preventScroll: true });
    if (selection) {
      selection.removeAllRanges();
      for (const range of ranges) selection.addRange(range);
    }
  }
}
