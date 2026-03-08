
# TODO

[ ] Server setup
    [ ] Port forward 80/443
    - On your router:
        - Port forward 80 → server
        - Port forward 443 → server
    - On your server firewall:
        - Allow inbound 80/tcp and 443/tcp
    [ ] Run scripts
```bash
export ANTHROPIC_API_KEY=key
export ALLOWED_ORIGINS=https://ajweeks.com,https://www.ajweeks.com
bash scripts/deploy_api_server.sh
export DOMAIN=api.ajweeks.com
export PROXY=caddy
bash scripts/setup_https_proxy.sh
```
    [ ] Test:
    - `curl https://api.ajweeks.com/health`
    [ ] Push web app to call `fetch("https://api.ajweeks.com/api/fix", { ... })`