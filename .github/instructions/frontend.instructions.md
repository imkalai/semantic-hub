---
applyTo: "static/**"
---

# Frontend in Semantic Hub

- Plain ES modules with `// @ts-check` and JSDoc types. No framework, no npm, no build step, no CDN scripts
  (the Content-Security-Policy only allows `'self'`).
- Design language is Looker Helper's: reuse the tokens and components in `static/css/app.css` (`.btn`, `.pill`,
  `.card`, `.acc`, `.seg`, `.kpi`, `.field`, ...). Put app-specific styles in `static/css/hub.css`, using the CSS
  variables only (no hard-coded colours) so light and dark themes both work. Dark theme: black backgrounds,
  green accents.
- All calls go through `api()` in `static/js/api.js` (adds the required `X-Requested-With` header and turns errors
  into readable messages). Escape every piece of data with `esc()` before putting it into HTML.
- Pages render into the container `route()` passes in (`static/js/main.js`); query inside that `root`, never
  `document`, so a slow, outdated render can't overwrite the current page.
- The current scope lives in `static/js/state.js` (`state.scope`, `scopeQuery()`); include it in API calls that
  read or write the ontology, retrieval or questions.
- Text: plain English, short, explain what happens and what the user can do next. Check pages at phone width.
