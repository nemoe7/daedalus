// Relative timestamp formatting and periodic DOM timer (`nuu` relative-timestamps recipe).

export function relativeTime(
  value,
  { compact = false, includeSeconds = false, now = Date.now() } = {}
) {
  const stamp =
    value instanceof Date ? value.getTime() : new Date(value).getTime();
  if (Number.isNaN(stamp)) {
    return compact ? "now" : "just now";
  }
  const seconds = Math.max(0, Math.floor((now - stamp) / 1000));
  if (includeSeconds) {
    if (seconds < 1) return compact ? "now" : "just now";
    if (seconds < 60) {
      return compact
        ? `${seconds}s ago`
        : seconds === 1
          ? "a second ago"
          : `${seconds} seconds ago`;
    }
  }
  const minutes = Math.floor(seconds / 60);
  if (minutes < 1) return compact ? "now" : "just now";
  if (minutes < 60) {
    return compact
      ? `${minutes}min ago`
      : minutes === 1
        ? "a minute ago"
        : `${minutes} minutes ago`;
  }
  const hours = Math.floor(minutes / 60);
  if (hours < 24) {
    return compact
      ? `${hours}h ago`
      : hours === 1
        ? "an hour ago"
        : `${hours} hours ago`;
  }
  const days = Math.floor(hours / 24);
  if (days < 7) {
    return compact
      ? `${days}d ago`
      : days === 1
        ? "yesterday"
        : `${days} days ago`;
  }
  if (days < 30) {
    const weeks = Math.floor(days / 7);
    return compact
      ? `${weeks}wk ago`
      : weeks === 1
        ? "last week"
        : `${weeks} weeks ago`;
  }
  if (days < 365) {
    const months = Math.max(1, Math.floor(days / 30));
    return compact
      ? `${months}mo ago`
      : months === 1
        ? "last month"
        : `${months} months ago`;
  }
  const years = Math.max(1, Math.floor(days / 365));
  return compact
    ? `${years}yr ago`
    : years === 1
      ? "last year"
      : `${years} years ago`;
}

export function nextRelativeDelay(
  stampMs,
  { includeSeconds = false, now = Date.now() } = {}
) {
  if (Number.isNaN(stampMs)) return Infinity;
  const ageMs = Math.max(0, now - stampMs);
  if (includeSeconds && ageMs < 60_000) {
    return Math.max(50, 1_000 - (ageMs % 1_000));
  }
  if (ageMs < 3_600_000) {
    return Math.max(250, 60_000 - (ageMs % 60_000));
  }
  if (ageMs < 86_400_000) {
    return Math.max(1_000, 3_600_000 - (ageMs % 3_600_000));
  }
  return 3_600_000;
}

export function mountRelativeTimers(root = document) {
  const selector = "[data-timestamp], time[datetime]";
  const nodes = [
    ...(root.matches?.(selector) ? [root] : []),
    ...root.querySelectorAll(selector),
  ];
  const compactViewport = window.matchMedia?.("(max-width: 60em)");
  let timer = 0;
  let disposed = false;
  const refresh = () => {
    const now = Date.now();
    let minDelay = Infinity;
    for (const node of nodes) {
      const value =
        node.getAttribute("data-timestamp") || node.getAttribute("datetime");
      if (!value) continue;
      const stamp = new Date(value);
      const stampMs = stamp.getTime();
      if (Number.isNaN(stampMs)) continue;
      const includeSeconds =
        node.getAttribute("data-include-seconds") === "true";
      const nextText = relativeTime(stamp, {
        compact: Boolean(compactViewport?.matches),
        includeSeconds,
        now,
      });
      const prefix = node.getAttribute("data-prefix");
      const text = prefix ? `${prefix} ${nextText}` : nextText;
      if (node.textContent !== text) {
        node.textContent = text;
      }
      const stateLabel = node.getAttribute("data-state-label");
      if (stateLabel) node.setAttribute("aria-label", `${stateLabel}, ${nextText}`);
      const delay = nextRelativeDelay(stampMs, { includeSeconds, now });
      if (delay < minDelay) {
        minDelay = delay;
      }
    }
    return minDelay;
  };
  const tick = () => {
    if (disposed) return;
    window.clearTimeout(timer);
    const delay = refresh();
    if (Number.isFinite(delay)) {
      timer = window.setTimeout(tick, delay);
    }
  };
  if (!nodes.length) return () => {};
  compactViewport?.addEventListener("change", tick);
  tick();
  return () => {
    disposed = true;
    compactViewport?.removeEventListener("change", tick);
    window.clearTimeout(timer);
  };
}
