# Set uv to either /root/.local/bin/uv or uv depending on which one exists
UV := $(shell command -v uv >/dev/null 2>&1 && echo "uv" || echo "/root/.local/bin/uv")

run:
	$(UV) run --frozen streamlit run --server.port 9113 clarity/main.py

run-api:
	$(UV) run --frozen clarity-api

deploy:
	git ls-files | rsync -avzP --files-from=- . pine:/srv/clarity
	rsync -avzP config-prod.yaml pine:/srv/clarity/config.yaml
	rsync -avzP clarity.service pine:/etc/systemd/system/
	rsync -avzP clarity-api.service pine:/etc/systemd/system/
	ssh pine "systemctl daemon-reload && systemctl restart clarity && journalctl -u clarity -f"
