# bodegahtx.com

Source for the Bodega HTX website.

- `index.html` – homepage source, including the store data (`bodega-data`).
- `build.py` – builds `dist/` (homepage, a page per store, sitemap, robots).
- `assets/` – logos and photos.
- `prices.json` – today's fuel prices. The homepage price board reads this file
  straight from GitHub, so price updates show without a rebuild.
- `wrangler.jsonc` – Cloudflare settings. Every change to `main` (except
  `prices.json`) is built and deployed by Cloudflare automatically.

Build locally: `python3 build.py`, or `python3 build.py --serve` to preview on port 8000.
