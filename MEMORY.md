# MEMORY.md

## Ruda

- Ruda is project and financial director at Království TianDe, a Czech natural-cosmetics e-shop.
- Key collaborators in the business context are his mother Renata Králová and colleague Denisa Horová.
- I should speak Czech naturally with him unless he asks otherwise.
- He wants me to think ahead, check my work carefully before giving it to him, avoid half-done outputs, and stay calm in tone.
- For image generation, prefer Google Gemini 3.1 Flash first. When he asks for a visual, prepare an English prompt optimized for Gemini 3.1 and send it to the Google image generation provider. If he asks for Instagram format, use 9:16 by default.

## Království TianDe, current workstreams

### E-shop platform and internal tooling
- The e-shop runs on WPJShop.
- A major ongoing technical track is a single-file warehouse/reporting dashboard connected to the WPJShop GraphQL admin API, with a Cloudflare Worker proxy in front of it.
- Important WPJShop quirks already learned from prior work:
  - GraphQL introspection is effectively unavailable.
  - Order item quantity uses the `pieces` field.
  - Per-warehouse stock uses `stores { store { id } inStore }`.
  - WPJShop date strings may arrive as `YYYY-MM-DD HH:MM:SS` and need normalization before JS date parsing.
  - Negative `inStore` values can reflect available stock after reservations, not necessarily a dashboard bug.

### Customer mobile app
- Another active workstream is a customer-facing mobile app for Království TianDe.
- Desired core features: product catalog with cart, order management/tracking, push notifications, and loyalty / points.
- The current state is a clickable HTML prototype in TianDe brand colors, with Expo React Native discussed as a next step.
- The app visual identity uses deep purple `#2E1245`, gold `#C9A84C`, and cream `#FAF6EE`.

### AI and marketing support
- There was prior work on an internal AI assistant for the e-shop team, meant to answer questions about products, inventory, orders, and FAQ data.
- There was also substantial marketing support work: AI banner prompting, Meta Ads creative briefs, and product-specific ad concepts.

### Compliance and product communication
- The e-shop/product catalog has already been reviewed for risky medical or health claims under Czech/EU rules.
- Roughly 49 products were flagged as medium-to-high risk and 16 as high risk needing urgent reformulation.

## Business context
- Království TianDe has over 10 years of operation and a large customer base.
- A related business initiative involves a weight-loss course with about 7,000 active participants.

## Notes to self
- I reviewed an imported Claude history archive on 2026-04-15 to absorb this context.
- Ruda wants a daily 08:00 Europe/Prague report with previous-day WPJ and 4PX data once the automation is wired.
- If any old API tokens were exposed in earlier chats/exports, recommend rotating them rather than reusing them.
