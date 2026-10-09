# Model marks

The SVG files in this folder are the marks beside a model name. Each file holds the mark of 1
provider or developer. The file name is the name in the model id, such as `openrouter` or
`mistralai`. A name without a file shows its own text in the text color.

## Source

The files come from `lobehub/lobe-icons` at commit `82e641b`, from the mono set under
`packages/static-svg/icons/`. The license is MIT, in `LICENSE`. A file keeps the shape of its
origin under the catalog name. `daedalus.svg` is not from that set: it is the repo logo
[`daedalus/dashboard/ui/logo.svg`](../logo.svg) in 1 color.

## Renamed files

Each file keeps the name of its origin, except these files:

| File | Origin |
| --- | --- |
| `black-forest-labs.svg` | `bfl.svg` |
| `deepseek-ai.svg` | `deepseek.svg` |
| `dots-studio.svg` | `dotsstudio.svg` |
| `fish-audio.svg` | `fishaudio.svg` |
| `ibm-granite.svg` | `ibm.svg` |
| `meta-llama.svg` | `meta.svg` |
| `runwayml.svg` | `runway.svg` |
| `kilo.svg` | `kilocode.svg` |
| `mistralai.svg` | `mistral.svg` |
| `moonshotai.svg` | `moonshot.svg` |
| `myshell-ai.svg` | `myshell.svg` |
| `stabilityai.svg` | `stability.svg` |
| `z-ai.svg`, `zai-org.svg` | `zai.svg` |

## Capability icons

`cap/brain.svg` and `cap/wrench.svg` draw the reasoning and tools chips. They come from `lucide-icons/lucide` at commit `a04f228`. Their license is ISC, appended to `LICENSE`. The app inlines the same paths in `app.js`.
