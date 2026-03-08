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

Note that you need to have the `ANTHROPIC_API_KEY` environment variable set to your Anthropic API key to
use Anthropic models by default, or create a `config.yaml` file in the `clarity_web` directory with the following to use any compatible API provider:

```yaml
# config.yaml
api_base: ...
api_key: ...
```

## Modify and run locally (or to set up your own instance)

```bash
uv run streamlit run clarity/main.py
```
