# Recipe: Chromium

- Install `@sparticuz/chromium` from npm.
- Inflate `bin/al2023.tar.br` with the package's `inflate`. Fonts come from `bin/fonts.tar.br`.
- Call `setupLambdaEnvironment` on the extracted `lib`.
- Launch the package's extracted binary.
- Install the extracted binary at the Playwright default executable path, and set `CHROMIUM_BIN` to it.
