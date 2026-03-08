# Clarity

It's simple.
1. Paste in some text
2. Get an AI to improve it
3. Review the suggestions:
    :red[red text is yours], :green[green is suggestions].
4. Click to toggle diffs between the original and new version.

![Clarity](./images/screenshot.webp)

## Run locally

The simplest way to run the app locally is using [`uvx`](https://docs.astral.sh/uv/#scripts)

```bash
uvx git+https://github.com/ajweeks/clarity
```

Note that you need to have the `ANTHROPIC_API_KEY` set to use Anthropic models by default. Otherwise, set `OPENAI_API_KEY` to use OpenAI models.

## Modify and run locally (or to set up your own instance)

```bash
uv run streamlit run clarity/main.py
```

## Run as a backend API (for static sites like GitHub Pages)

If your frontend is on GitHub Pages, keep your API key private by running this repo's API service on your home server.

For setup and deployment steps, see [server-instructions.md](./server-instructions.md).
