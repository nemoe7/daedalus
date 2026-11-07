# Recipe: Chromium

- Install `@sparticuz/chromium` from npm.
- Inflate `bin/al2023.tar.br` with the package's `inflate`. Fonts come from `bin/fonts.tar.br`.
- Call `setupLambdaEnvironment` on the extracted `lib`.
- Launch the package's extracted binary.
- NEVER use a Playwright-managed browser.
