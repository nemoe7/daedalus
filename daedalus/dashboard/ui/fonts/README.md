# Bundled fonts

This folder holds bundled variable web fonts in `.woff2` format for the dashboard.

## Source

| File | Family | Origin | Version | License |
| --- | --- | --- | --- | --- |
| `inter-var.woff2` | `Inter` (`100 900`) | `@fontsource-variable/inter` (`inter-latin-wght-normal.woff2`) | `5.3.0` | SIL Open Font License 1.1 (`LICENSE`) |
| `monaspace-neon-var.woff2` | `Monaspace Neon` (`200 800`) | `githubnext/monaspace` (`Monaspace Neon Var.woff2`) | `v1.400` | SIL Open Font License 1.1 (`LICENSE`) |

## Delivery

`index.html` preloads both `.woff2` files with `crossorigin` before `style.css`. `style.css` declares `@font-face` rules with `font-display: block` and metric-matched local fallback faces (`Inter Fallback` and `Monaspace Neon Fallback`).
