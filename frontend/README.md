# Vietra frontend

React 19 storefront for natural-language discovery, explainable
recommendations, voice interaction, rich assistant commerce cards, cart,
simulated checkout, and QR vouchers.

## Development

```bash
corepack pnpm install
VITE_API_BASE_URL=http://localhost:8000/api/v1 corepack pnpm dev
```

## Quality and UI tests

```bash
corepack pnpm build
corepack pnpm lint
corepack pnpm exec playwright install chromium
PLAYWRIGHT_BASE_URL=http://localhost:5173 corepack pnpm test:e2e
```

See the repository-level `README.md` and `docs/DEPLOYMENT.md` for complete
local and Azure instructions.
