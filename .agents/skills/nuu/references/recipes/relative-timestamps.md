# Recipe: Relative timestamps

Format relative timestamps with the singular, plural, and abbreviated forms below. Omit seconds (`< 1` minute -> `just now` / `now`) unless second-level resolution is explicitly requested. Keep the surrounding text face, and add `font-variant-numeric: tabular-nums` where stamps stack in a column.

![Relative Timestamps Dark-Mode Example](../examples/relative-timestamps.png)

| Unit | Singular (1) | Plural (2+) | Abbreviated |
| --- | --- | --- | --- |
| now | just now | — | now |
| second | a second ago | X seconds ago | Xs ago |
| minute | a minute ago | X minutes ago | Xmin ago |
| hour | an hour ago | X hours ago | Xh ago |
| day | yesterday | X days ago | Xd ago |
| week | last week | X weeks ago | Xwk ago |
| month | last month | X months ago | Xmo ago |
| year | last year | X years ago | Xyr ago |

Use the abbreviated column on a phone, or wherever the stamp shares a line with a name. Use the full form where the line has room. A `title` carries the full form when the visible text is abbreviated.

## Reference implementation

```js
function relativeTime(value, { compact = false, includeSeconds = false, now = Date.now() } = {}) {
  const stamp = value instanceof Date ? value.getTime() : new Date(value).getTime();
  const seconds = Math.max(0, Math.floor((now - stamp) / 1000));
  if (includeSeconds) {
    if (seconds < 1) return compact ? 'now' : 'just now';
    if (seconds < 60) return compact ? `${seconds}s ago` : (seconds === 1 ? 'a second ago' : `${seconds} seconds ago`);
  }
  const minutes = Math.floor(seconds / 60);
  if (minutes < 1) return compact ? 'now' : 'just now';
  if (minutes < 60) return compact ? `${minutes}min ago` : (minutes === 1 ? 'a minute ago' : `${minutes} minutes ago`);
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return compact ? `${hours}h ago` : (hours === 1 ? 'an hour ago' : `${hours} hours ago`);
  const days = Math.floor(hours / 24);
  if (days < 7) return compact ? `${days}d ago` : (days === 1 ? 'yesterday' : `${days} days ago`);
  if (days < 30) {
    const weeks = Math.floor(days / 7);
    return compact ? `${weeks}wk ago` : (weeks === 1 ? 'last week' : `${weeks} weeks ago`);
  }
  if (days < 365) {
    const months = Math.max(1, Math.floor(days / 30));
    return compact ? `${months}mo ago` : (months === 1 ? 'last month' : `${months} months ago`);
  }
  const years = Math.max(1, Math.floor(days / 365));
  return compact ? `${years}yr ago` : (years === 1 ? 'last year' : `${years} years ago`);
}
```
