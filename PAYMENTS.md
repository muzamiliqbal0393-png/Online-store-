# Payment setup — actual implementation status

## What is ready

- **Cash on Delivery:** working end to end.
- **Stripe:** server-side Checkout Session creation, idempotency key, server-calculated PKR amounts, signed raw-body webhook verification, five-minute timestamp tolerance, event deduplication, authenticated server-to-server payment reconciliation, amount/currency/order/session checks, verified paid state, expiration/cancellation restocking, and a customer payment-status screen. No real provider transaction has been run: no merchant credentials were supplied. Unit tests use explicitly mocked provider responses, not real funds.
- **JazzCash:** configuration-status panel and tested signing primitives only. The payment method remains disabled. A hosted/direct wallet checkout and merchant-approved response verification have NOT been activated or fully implemented.
- **Easypaisa:** configuration-status panel and environment-variable placeholders only. The payment method remains disabled. The adapter still requires the approved, current merchant integration contract and implementation. No signing algorithm is guessed from conflicting third-party examples.

Do not describe the wallet methods as live integrations. Filling in their environment variables does not turn them on.

## Enable Stripe in TEST mode

1. Use an eligible, approved Stripe merchant account. Business-country eligibility, presentment-currency support and settlement details must be verified with Stripe. Do not choose a false business country.
2. Put the site behind a trusted HTTPS reverse proxy and set `PUBLIC_BASE_URL` to its externally reachable origin, for example `https://shop.your-domain.example`. The application does not construct payment URLs from untrusted request Host headers.
3. Set these private server environment variables (see `.env.example`):
   - `PAYMENT_MODE=test`
   - `STRIPE_ENABLED=1`
   - `STRIPE_SECRET_KEY` to the account's **test** secret key
   - `STRIPE_WEBHOOK_SECRET` to the webhook signing secret
   - `COOKIE_SECURE=1` when the entire application is served over HTTPS
4. Configure a Stripe webhook at:
   `https://YOUR-DOMAIN/api/payments/stripe/webhook`
5. Subscribe to `checkout.session.completed`, `checkout.session.async_payment_succeeded`, `checkout.session.async_payment_failed`, and `checkout.session.expired`.
6. Restart the server. The Payments panel shows configuration presence, not proof of provider approval or a completed test.
7. Run the provider's test-card flow, declined-payment test, expiry test, repeated-event test and interrupted-return test. Confirm the order is not marked paid merely because the customer reaches a success URL. Confirm stock is released on verified expiration.
8. For live operation, repeat the provider setup with **live** keys, `PAYMENT_MODE=live`, and the live webhook secret only after all tests and commercial readiness reviews pass. This project has not been deployed publicly.

Browser card numbers, CVVs and wallet PINs are never collected by StyleHub. Stripe Checkout is hosted by Stripe. Do not add card-input fields to this site.

## Order state and inventory

Online orders begin with `payment_state=pending` and reserve stock. A successful redirect never marks them paid. A signed webhook is checked against current provider state; an authenticated customer may also use **Check Payment Status** to perform the same server-side provider check.

Pending online orders cannot be shipped from the admin panel. A payment completed after cancellation is flagged `review` and requires operator review rather than silently shipping an already-restocked item.

The pending-payment page can request provider-confirmed checkout expiration to cancel an unpaid order and release stock. Abandoned checkout sessions expire after 30 minutes; the corresponding webhook is needed to reconcile and release stock. Monitor webhook failures and reconcile stuck orders. No background recovery worker or automatic refund processor is included.

Paid online orders cannot be cancelled through the basic status selector. A reviewed provider-refund workflow must be implemented before enabling automated refunds. COD remains unchanged.

## JazzCash activation work still required

Get the approved merchant account, current API version, sandbox credentials, production credentials, callback/return specification, server-side transaction-query specification and official test cases from your JazzCash onboarding team. Validate the included signing primitive against their official fixtures. Implement the approved checkout and trusted response/status-query paths, then run sandbox tests. The wallet's method must remain disabled until those steps are complete.

Official starting documentation: https://sandbox.jazzcash.com.pk/SandboxDocumentation/index.html

## Easypaisa activation work still required

Get the store ID, username/password, hash/encryption key, current merchant integration pack, allowed endpoints, callback validation rules and transaction inquiry specification from Easypaisa onboarding. Different documentation versions describe different field names and encryption flows; this project intentionally does not invent a production adapter. Implement and sandbox-test the approved contract before enabling it.

## Secret handling

Never share keys in chat, commit a real `.env`, include credentials in a ZIP, or put secrets into the admin UI. The payment-status API returns booleans and public method labels, not key values. Payment secrets are read only from server environment variables. `.env.example` contains empty placeholders and is safe to share.

## Tests

```sh
python -m unittest test_payments.py -v
python test-api.py     # running server; creates QA records
node test-ui.cjs       # running server + Playwright; creates QA records
```

Unit-test provider responses are mocks; passing unit tests is not evidence of a successful real Stripe, JazzCash or Easypaisa transaction.
