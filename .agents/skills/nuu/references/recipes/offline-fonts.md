# Recipe: Bundled and offline fonts without reload jitter

Eliminate Flash of Unstyled Text (FOUT) and layout shift on reload by matching the delivery pattern to how the page is served.

## 1. Served local or bundled `.woff2` (`http://` or `https://`)

- Bundle one subset variable `.woff2` file per family (`font-weight: 100 900`) instead of separate static files per weight.
- Place `<link rel="preload" href="./fonts/inter-var.woff2" as="font" type="font/woff2" crossorigin>` at the top of `<head>` before every `<link rel="stylesheet">` and `<script>`. Always keep `crossorigin` even for same-origin bundled files, or the browser ignores the preload cache.
- Declare `font-display: block;` (never `swap`) on bundled local `@font-face` rules so the browser holds text paint during the `< 10ms` local decode instead of painting one fallback frame first.

## 2. Single-file, `file://`, or guaranteed zero-latency offline HTML

- Inline the subset `.woff2` directly in a `<head>` `<style>` block as `src: url("data:font/woff2;base64,...") format("woff2"); font-display: block;`. Because the bytes parse with `<head>`, reloads never wait on an asynchronous file fetch and `file://` CORS restrictions never apply.
- Alternatively, pass a `Uint8Array` or `ArrayBuffer` directly to `const face = new FontFace("Inter", buffer, { weight: "100 900" }); document.fonts.add(face);` in a synchronous `<head>` script before `<body>` renders.

## 3. Metric-matched fallback safety net

- Pair every custom `@font-face` with a local metric-adjusted fallback face so line boxes and character widths stay identical even if a font fails to load:

```css
@font-face {
  font-family: "Inter";
  src: url("./fonts/inter-var.woff2") format("woff2");
  font-weight: 100 900;
  font-style: normal;
  font-display: block;
}
@font-face {
  font-family: "Inter Fallback";
  src: local("Arial");
  size-adjust: 107.64%;
  ascent-override: 90.49%;
  descent-override: 22.48%;
  line-gap-override: 0%;
}
:root {
  --font: "Inter", "Inter Fallback", system-ui, -apple-system, "Segoe UI", sans-serif;
}
```

## 4. JS measurement and animation readiness

- Wait for `await document.fonts.ready` before running layout measurements (`getBoundingClientRect()`, `measure.py`, or GSAP SplitText/ScrollTrigger calculations) so measurements always read final glyph metrics.
