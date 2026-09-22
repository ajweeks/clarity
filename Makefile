# Set uv to either /root/.local/bin/uv or uv depending on which one exists
UV := $(shell command -v uv >/dev/null 2>&1 && echo "uv" || echo "/root/.local/bin/uv")

# Serve https://clarity.ajweeks.com through the Cloudflare tunnel (needs .env).
run:
	bash scripts/start_server.sh

# Local development at http://127.0.0.1:9114 without the login screen.
dev:
	CLARITY_DISABLE_AUTH=1 $(UV) run --frozen clarity-api

test:
	$(UV) run --frozen pytest
