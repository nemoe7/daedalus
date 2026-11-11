// Shared Lucide and Markdown SVG icons for Arena Steering views.

export function lucideSvg(
  name,
  body,
  { size = 16, strokeWidth = 2, extraClass = "" } = {},
) {
  const cls = `lucide lucide-${name}${extraClass ? ` ${extraClass}` : ""}`;
  return `<svg class="${cls}" width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="${strokeWidth}" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">${body}</svg>`;
}

export const ARROW_DOWN_SVG = lucideSvg(
  "arrow-down",
  '<path d="M12 5v14"></path><path d="m19 12-7 7-7-7"></path>',
);
export const ARROW_UP_SVG = lucideSvg(
  "arrow-up",
  '<path d="m5 12 7-7 7 7"></path><path d="M12 19V5"></path>',
);
export const BRACES_14_SVG = lucideSvg(
  "braces",
  '<path d="M8 3H7a2 2 0 0 0-2 2v5a2 2 0 0 1-2 2 2 2 0 0 1 2 2v5c0 1.1.9 2 2 2h1"></path><path d="M16 21h1a2 2 0 0 0 2-2v-5c0-1.1.9-2 2-2a2 2 0 0 1-2-2V5a2 2 0 0 0-2-2h-1"></path>',
  { size: 14 },
);
export const CHECK_14_SVG = lucideSvg("check", '<path d="M20 6 9 17l-5-5"></path>', {
  size: 14,
});
export const CHECK_SVG = lucideSvg("check", '<path d="M20 6 9 17l-5-5"></path>');
export const CHEVRON_DOWN_14_SVG = lucideSvg(
  "chevron-down",
  '<path d="m6 9 6 6 6-6"></path>',
  { size: 14 },
);
export const CHEVRON_SELECT_SVG = lucideSvg(
  "chevron-down",
  '<path d="m6 9 6 6 6-6"></path>',
  { size: 14, extraClass: "select-chevron" },
);
export const COLLAPSE_SVG = lucideSvg(
  "minimize-2",
  '<path d="m14 10 7-7"></path><path d="M20 10h-6V4"></path><path d="m3 21 7-7"></path><path d="M4 14h6v6"></path>',
);
export const COPY_14_SVG = lucideSvg(
  "copy",
  '<rect width="14" height="14" x="8" y="8" rx="2" ry="2"></rect><path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2"></path>',
  { size: 14 },
);
export const COPY_SVG = lucideSvg(
  "copy",
  '<rect width="14" height="14" x="8" y="8" rx="2" ry="2"></rect><path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2"></path>',
);
export const DISMISS_SVG = lucideSvg(
  "x",
  '<path d="M18 6 6 18"></path><path d="m6 6 12 12"></path>',
);
export const EXPAND_SVG = lucideSvg(
  "maximize-2",
  '<path d="M15 3h6v6"></path><path d="m21 3-7 7"></path><path d="m3 21 7-7"></path><path d="M9 21H3v-6"></path>',
);
export const EYE_CLOSED_SVG = lucideSvg(
  "eye-off",
  '<path d="M10.733 5.076a10.744 10.744 0 0 1 11.205 6.575 1 1 0 0 1 0 .696 10.747 10.747 0 0 1-1.444 2.49"></path><path d="M14.084 14.158a3 3 0 0 1-4.242-4.242"></path><path d="M17.479 17.499a10.75 10.75 0 0 1-15.417-5.151 1 1 0 0 1 0-.696 10.75 10.75 0 0 1 4.446-5.143"></path><path d="m2 2 20 20"></path>',
);
export const EYE_OPEN_SVG = lucideSvg(
  "eye",
  '<path d="M2.062 12.348a1 1 0 0 1 0-.696 10.75 10.75 0 0 1 19.876 0 1 1 0 0 1 0 .696 10.75 10.75 0 0 1-19.876 0"></path><circle cx="12" cy="12" r="3"></circle>',
);
export const FILE_SVG = lucideSvg(
  "file-text",
  '<path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7z"></path><path d="M14 2v6h6"></path><path d="M10 9H8"></path><path d="M16 13H8"></path><path d="M16 17H8"></path>',
);
export const PAPERCLIP_SVG = lucideSvg(
  "paperclip",
  '<path d="m16 6-8.414 8.586a2 2 0 0 0 2.829 2.829l8.414-8.586a4 4 0 1 0-5.657-5.657l-8.379 8.551a6 6 0 1 0 8.485 8.485l8.379-8.551"></path>',
);
export const SEND_PLANE_SVG = lucideSvg(
  "send",
  '<path d="M14.536 21.686a.5.5 0 0 0 .937-.024l6.5-19a.496.496 0 0 0-.635-.635l-19 6.5a.5.5 0 0 0-.024.937l7.93 3.18a2 2 0 0 1 1.112 1.11z"></path><path d="m21.854 2.147-10.94 10.939"></path>',
);
export const SENDING_SVG = lucideSvg(
  "loader-circle",
  '<path d="M21 12a9 9 0 1 1-6.219-8.56"></path>',
);
export const SKIP_FORWARD_SVG = lucideSvg(
  "skip-forward",
  '<path d="M21 4v16"></path><path d="M6.029 4.285A2 2 0 0 0 3 6v12a2 2 0 0 0 3.029 1.715l9.997-5.998a2 2 0 0 0 .003-3.432z"></path>',
);
export const SURVEY_BAD_SVG = lucideSvg(
  "x",
  '<path d="M18 6 6 18"></path><path d="m6 6 12 12"></path>',
  { strokeWidth: 2.2 },
);
export const SURVEY_OK_SVG = lucideSvg("check", '<path d="M20 6 9 17l-5-5"></path>', {
  strokeWidth: 2.2,
});
export const SURVEY_WARN_SVG = lucideSvg(
  "circle-question-mark",
  '<circle cx="12" cy="12" r="10"></circle><path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3"></path><path d="M12 17h.01"></path>',
);
export const UNANSWERED_SVG = lucideSvg(
  "circle-help",
  '<circle cx="12" cy="12" r="10"></circle><path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3"></path><path d="M12 17h.01"></path>',
  { extraClass: "report-picker-help-icon" },
);
export const MARKDOWN_SVG = `<svg class="markdown-icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M22.27 19.385H1.73A1.73 1.73 0 0 1 0 17.655V6.345a1.73 1.73 0 0 1 1.73-1.73h20.54A1.73 1.73 0 0 1 24 6.345v11.308a1.73 1.73 0 0 1-1.73 1.731Zm-16.501-3.462v-4.5l2.308 2.885 2.307-2.885v4.5h2.308V8.078h-2.308l-2.307 2.885-2.308-2.885H3.46v7.847Zm15.463-3.923h-2.309V8.077h-2.307V12h-2.308l3.461 4.039Z" fill="currentColor" stroke="none"></path></svg>`;
