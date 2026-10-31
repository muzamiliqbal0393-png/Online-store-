# StyleHub — complete local store

A responsive 12-screen store matching the supplied navy / purple / white reference, with a Python API and SQLite persistence. No runtime packages are required beyond Python 3.10+.

## Start

```sh
cd backend
python server.py
```

Open `http://localhost:3000`. In Arena, use the **StyleHub Website** live preview. The application must be served by `server.py` — opening `index.html` directly or using a plain static server will not provide the API.

## Administrator access

The first launch creates a random administrator password and writes it to **`StyleHub-admin-access.txt` in the parent folder** (outside the public website folder). Sign in through the normal Login page, then open **Admin Dashboard** in the footer.

Change the initial password in **My Account → Profile Settings**. Do not share the credential file or include it in a public deployment. To configure the first administrator on a fresh database, set `STYLEHUB_ADMIN_EMAIL` and `STYLEHUB_ADMIN_PASSWORD` before first launch. Existing accounts are not reset by changing these variables.

## Included functionality

- All 12 reference screens, plus working shipping, returns, FAQ, privacy and terms pages.
- Catalog search, price filtering, sorting, categories, availability, saved wishlist and cart.
- Account registration and password verification using salted scrypt hashes; HttpOnly session cookies; CSRF checks; administrator-only API authorization; basic sign-in rate limiting.
- SQLite-backed user accounts, saved addresses, wishlists, contact messages, products, stock, order snapshots and order-status history.
- Cash on Delivery checkout; server-side pricing and coupon validation; inventory checks in a write transaction.
- Free delivery at a product subtotal of Rs. 2,500; otherwise Rs. 200. Coupon **STYLE10** gives 10% off the product subtotal.
- Administrator product add/edit/hide, inventory edits, customer list, messages inbox, order details and status management, real sales summaries, CSV export and editable store contact details.
- Cancellation returns inventory once and cannot be reopened accidentally.
- Browser carts are local to that browser. Signed-in wishlists and addresses persist in the database.

## Database and backup

The database is stored at `data/store.sqlite3`. It is never served as a public file. Keep the entire `data/` directory on persistent storage.

```sh
python backup.py /path/to/backups/stylehub.sqlite3
```

Use SQLite's backup interface (as in `backup.py`) instead of copying an open database arbitrarily. Backups contain personal information; protect and encrypt them appropriately. Session data is also in the database.

## Container setup

A Dockerfile and Compose file are included:

```sh
docker compose -f backend/compose.yaml up --build -d
```

This runs on port 3000 and persists the database and first-run credential file in named Docker volumes. Retrieve the initial login with `docker compose exec stylehub cat /app/access/StyleHub-admin-access.txt`. Put this service behind an HTTPS reverse proxy before any public use. Set `COOKIE_SECURE=1` when serving exclusively through HTTPS. Never expose the database or credential file through your reverse proxy. The service binds to `0.0.0.0`; all browser API requests use relative URLs.

The supplied Python HTTP service is intended for this local/reference implementation and small controlled deployments, not a hardened high-traffic platform. Before commercial launch, review deployment security, request limits, monitoring, data retention, backups, and privacy/legal obligations.

## Still requires your external setup

- **Online payments:** a Stripe adapter and signed webhook verification are implemented but unconfigured and not provider-tested. JazzCash signing/configuration scaffolding and Easypaisa configuration scaffolding are included, but their real checkout adapters remain disabled and require merchant-approved API documentation and implementation. See `PAYMENTS.md`. No online method is live.
- **Public domain / hosting / TLS:** source and container setup are provided; no public deployment has been created.
- **Transactional email / password-reset email:** no email service is configured. Contact messages are received in the administrator inbox, not sent by email.
- **Courier integration:** statuses are manually updated by the administrator, not live courier tracking.
- **Real store details and catalog:** initial products, prices, stock, contact details, and policy text are reference defaults. Product imagery was extracted from the supplied low-resolution screenshot and cleaned up; replace with original licensed retail photography for a production store. Review all terms, contact details, offers and delivery commitments before launch.

## Tests

`test-api.py` exercises API authorization, CSRF, account isolation, totals, discounts, stock and cancellation behavior against a running server. `test-ui.cjs` is a Playwright end-to-end smoke test; it needs Playwright and Chromium installed. Tests create QA-prefixed records — use a test database, not a live production database.

Verified during delivery: registration, login/logout, checkout, order persistence, administrator product creation, customer messages, protected routes, and desktop/mobile rendering without JavaScript errors or horizontal overflow on tested pages.

## Payment configuration

See **PAYMENTS.md** for exact implementation status, activation instructions and limitations. Copy `.env.example` to a private `.env` for Docker Compose, or export its variables before starting Python. The server does not automatically parse `.env`. Cash on Delivery remains available without any provider account. Admin → Payments shows readiness without exposing secrets.

## Verified customer reviews

- Product cards and detail pages show real database-derived averages and counts, or an unrated state when there is no published feedback.
- A signed-in customer can review a product only after a delivered order for that product. Online orders must also have verified paid status.
- Ratings are integers from 1 to 5; titles and review text are validated server-side. One review per customer/product can be edited, not duplicated.
- Admin → Reviews can hide and restore feedback. Hidden reviews are excluded from public lists, rating distributions, averages and counts. Editing a hidden review does not restore it automatically.
- Public reviewers are shown by first name and last initial, without email or user ID.
- `test-reviews.cjs` tests eligibility, validation, aggregate changes, moderation, persistence and responsive UI against an isolated test server on port 3001. Do not run it against real customer data.
- The delivered review screenshots are clearly labeled demonstrations from an isolated sample-data store. They are not genuine customer endorsements. The main store has no seeded customer reviews.
