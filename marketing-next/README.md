# SwitchIt! marketing site

Next.js 15 App Router, React 19, Tailwind CSS 4 and component CSS modules.

```sh
npm install
npm run dev -- --port 3210
npm run build
python3 -m http.server 3210 --directory out
```

The production build exports static pages to `out/`. Stop the development server before building: Next shares the `.next` cache between the two commands. Turbopack is used because the parent directory's `!` conflicts with webpack loader syntax.

## Interface

- `components/hero.*`: responsive hero and switch illustration.
- `components/migration-demo.*`: interactive synthetic replay and installation commands.
- `components/product-story.*`: sources, workflow, verdicts, and targets.
- `components/site-chrome.tsx`: navigation and footer.
- `app/guide/page.tsx`: setup guide.
- `public/downloads/`: bundled v0.1.0 Python wheel. Update it with future package releases.

The browser demo runs deterministic illustrative outcomes. It makes no provider requests and does not evaluate real provider quality. Installation uses the source repository or bundled wheel; the page does not assume a PyPI release exists.

## Visual and interaction verification

Run against a development or static preview. Supply an available Playwright installation; it is not a production dependency.

```sh
PLAYWRIGHT_MODULE=/path/to/playwright PREVIEW_URL=http://localhost:3210 node scripts/verify-ui.cjs
```

The script checks six viewport sizes, workflow tabs, replay states, expandable check evidence, clipboard success and failure, mobile navigation, the guide, package download, and browser errors. Screenshots and the check report are saved in `artifacts/visual-review/`.

## Visual reference

The visual direction follows Monet’s RunwayML feature showcase:
https://github.com/monet-design/monet-registry/tree/main/src/components/registry/runwayml-com-feature-3

Reference details: Inter, white typography, atmospheric olive/gold imagery,
asymmetric columns, hairline link rows, and small frosted buttons. The background
at `public/images/runway-atmosphere.webp` comes from the corresponding public
Monet registry asset. Reference captures are in `artifacts/reference/`.

The homepage expands that visual language into a full-width hero, interactive workflow explorer, broad replay workspace, outcome section, provider routing map, and package-installation section. Reduced-motion preferences and keyboard navigation are supported.

## Production

Live site: https://getswitchit.vercel.app

Vercel project: `aniiiixds-projects/getswitchit`. The deployment serves the verified static `out/` export. It contains no server functions or provider credentials. Deployment details are in `artifacts/deployment.json`.

For future releases, rebuild, copy `out/` to a clean staging directory, and use the same static Vercel settings (`framework: null`, `buildCommand: null`, `outputDirectory: "."`, `trailingSlash: true`). Link that directory explicitly to `getswitchit` in `aniiiixds-projects`, inspect the link, then deploy to production. Exclude `.env*` and `.vercel/` from uploads.
