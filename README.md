# Clarity

It's simple.
1. Paste in some text
2. Get an AI to improve it
3. Review the suggestions:
    :red[red text is yours], :green[green is suggestions].
4. Click to toggle diffs between the original and new version.

## Run locally

```bash
cp .env.example .env   # add your ANTHROPIC_API_KEY
make dev               # http://127.0.0.1:9114, login disabled
```

## Host it

`make run` serves the app at https://clarity.ajweeks.com through a Cloudflare Tunnel,
behind a password + Cloudflare Turnstile login. See [server-instructions.md](./server-instructions.md).
