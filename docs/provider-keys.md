# Provider keys

Each provider needs 1 key, and daedalus skips a provider without one. The steps below make the key. Copy it right after you create it: some pages show the key only once.

## Cloudflare Workers AI

1. Go to <https://dash.cloudflare.com> and sign in. A new account needs no payment method.
2. In the left menu, pick **AI**, then **Workers AI**.
3. Under **Use REST API**, pick **Create Workers AI API Token**.
4. Copy the token and the **Account ID** in the right sidebar.

## Google Gemini

1. Go to <https://aistudio.google.com/apikey> with a Google account.
2. Pick **Create API key**.
3. Copy the key.

## Groq

1. Go to <https://console.groq.com/keys> and sign in, or create an account.
2. Pick **Create API Key**.
3. Copy the key.

## Kilo

1. Go to <https://app.kilo.ai> and sign in, or create an account.
2. Open the **API Key** section at the bottom of the dashboard.
3. Copy the key.

## Mistral

1. Go to <https://console.mistral.ai/api-keys/> and sign in, or create an account.
2. Pick **Create new key**.
3. Copy the key.

## OpenRouter

1. Go to <https://openrouter.ai/settings/keys> and sign in, or create an account.
2. Pick **Create Key**.
3. Copy the key. The `:free` models need no credit balance.

## Pollinations

1. Go to <https://enter.pollinations.ai> and sign up.
2. Generate a key.
3. Copy the key. Pollinations is not free: the account spends its Pollen balance, and Pollen does not refill.

## Z.ai

1. Go to <https://z.ai/model-api> and sign in, or create an account.
2. Go to <https://z.ai/manage-apikey/apikey-list>.
3. Create a key and copy it.

A key goes into the `env:NAME` value of the provider file, or into the dashboard. The names are in [Configuration](configuration.md#environment-variables). [Providers](providers.md#use-the-key) gives the dashboard steps.
