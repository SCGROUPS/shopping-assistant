# Vietra frontend

React 19 storefront for natural-language discovery, explainable
recommendations, voice interaction, rich assistant commerce cards, cart,
simulated checkout, and QR vouchers.

## Development

```bash
corepack pnpm install
corepack pnpm dev
```

The dev server proxies `/api/v1` to `http://127.0.0.1:8000`, so the API is
same-origin exactly as it is in the deployed container. Point the proxy
elsewhere with `VITE_DEV_API_TARGET`, or bypass it entirely by setting
`VITE_API_BASE_URL` to an absolute URL.

## Quality and UI tests

```bash
corepack pnpm build
corepack pnpm lint
corepack pnpm exec playwright install chromium
PLAYWRIGHT_BASE_URL=http://localhost:5173 corepack pnpm test:e2e
```

The suite runs against whatever `PLAYWRIGHT_BASE_URL` points at — a dev server
or a deployed environment — so specs address the API through that origin
rather than assuming a backend on localhost.

See the repository-level `README.md` and `docs/DEPLOYMENT.md` for complete
local and Azure instructions.

For the assistant presence and engagement model — ambient launcher,
friction-triggered nudges, contextual cold opens, per-card entry points, and
the storefront/assistant shared-state contract — see
[../docs/SYSTEM_DESIGN.md](../docs/SYSTEM_DESIGN.md) sections 8 and 9.
