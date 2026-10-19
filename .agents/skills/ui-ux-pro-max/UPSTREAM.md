# UI/UX Pro Max source

- Repository: <https://github.com/nextlevelbuilder/ui-ux-pro-max-skill>
- Revision: `a38d04c3d5c298c851dbe5e6ee1965ee3de42cb5` (tag `v2.15.0`)
- Package: `ui-ux-pro-max-cli@2.15.0`
- License: MIT. See `LICENSE`.
- Upstream files: `SKILL.md`, `data/`, `scripts/`, and the sibling skill folders `banner-design`, `brand`, `design`, `design-system`, and `slides`.
- Update: run `npx -y ui-ux-pro-max-cli@2.15.0 init --ai universal` in a temporary folder, copy the same folders, and review the diff.

## Local changes

- `ui-styling` is not vendored.
- This copy omits the upstream `scripts/tests` folder because it expects the upstream source root.
- Paths that name `.claude/skills/` in the sibling skills now name `.agents/skills/`.
- The banner and brand skills name optional helper skills (`ai-artist`, `ai-multimodal`, `chrome-devtools`). This repository does not vendor those helpers.
