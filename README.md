# CashFlow

CashFlow is a Django 6 web platform for small clubs, local venues and private communities that need to manage catalog stock, purchases, user balances, events and daily operations.

It includes two complete experiences:
- Admin workspace (`/admin-panel/...`) for operations, approvals and analytics.
- User workspace (`/user/...`) for catalog, cart, orders, balance and events.

## Current Scope

The project currently includes:
- Live UI updates without page reload using two transports:
  - WebSockets when Channels + Daphne are available.
  - HTTP polling fallback when Channels/Daphne are not installed.
- Multi-language support (`en`, `es`) with compiled locales.
- Audited monetary flows (orders, balance requests, monthly fees).
- Product stock adjustment history and operational logs.
- Two complete web workspaces (admin and user) with live synchronization.
- Optimized mobile views for operations and purchase flow.

## Tech Stack

- Python 3.11+
- Django `>=6.0.2`
- Channels `>=4.1,<5.0`
- Daphne `>=4.1,<5.0`
- Pillow `>=10.0,<12.0`
- qrcode `>=7.4,<9.0`
- SQLite (default local DB)

## Architecture

- HTTP: Django views (CBV-heavy) and templates.
- ASGI: `ProtocolTypeRouter` with HTTP + WebSocket handling.
- WebSocket endpoint: `/ws/live-updates/`
- Channel layer: `InMemoryChannelLayer` (development profile).
- Template live-refresh regions: partial updates without full page reload.

Important: `InMemoryChannelLayer` is not suitable for multi-instance production. For production scaling, use Redis (`channels_redis`).

## Functional Domains

- Inventory (`inventory/`): categories, suppliers, products, images, reviews, product sheets, stock adjustment logs.
- Sales (`sales/`): direct sales, orders, order approval/rejection/edit/delete with stock and balance impact.
- Customers (`customers/`): user profile, balance requests, balance logs, monthly fee settings.
- Core (`core/`): dashboards, actions board, events, notices, gamification, strikes, system settings, auth flow.

## Key Features

### Admin

- Dashboard with live KPIs and cash charts.
- Actions board with prioritized pending tasks.
- Product management:
  - CRUD, category/supplier management.
  - Stock adjustment with full audit history.
  - Low-stock alerts with deep links to products.
- Sales and transactions view (sales + approved orders).
- Order review and approval detail flow.
- Balance requests approval/rejection and balance logs.
- Events and registrations management.
- Gamification management and reward completion.
- Reviews moderation.
- System page for environment/backups/log visibility.

### User

- Dashboard timeline (orders, events, notices, gamification progress).
- Product catalog and cart workflow.
- Order create, detail, repeat and edit.
- Balance page with top-up requests and history.
- Event registration/unregistration.
- Profile management and purchase history.

## Routing Overview

- Root auth pages:
  - `/` and `/login/` for sign-in.
  - `/dashboard/` smart redirect (admin/user).

- Admin routes (examples):
  - `/admin-panel/dashboard/`
  - `/admin-panel/actions/`
  - `/admin-panel/products/` and related CRUD routes
  - `/admin-panel/sales/`
  - `/admin-panel/orders/<id>/` (order review)
  - `/admin-panel/balance-requests/`
  - `/admin-panel/events/`
  - `/admin-panel/gamifications/`
  - `/admin-panel/reviews/`
  - `/admin-panel/system/`

Note: `/admin-panel/orders/` is kept as a legacy route and currently redirects to `/admin-panel/actions/`.

- User routes (examples):
  - `/user/dashboard/`
  - `/user/products/` + cart endpoints
  - `/user/orders/`
  - `/user/balance/`
  - `/user/events/<id>/`
  - `/user/profile/`

## Project Structure

```text
CashFlow/
  CashFlow/      # settings, asgi, routing, urls
  core/          # webviews, controllers, middleware, signals, templates glue
  inventory/     # catalog domain
  sales/         # sales and orders domain + services
  customers/     # balances, profiles, monthly fees
  templates/     # global/admin/user templates
  static/        # static assets
  media/         # uploaded files
  locale/        # en/es translations
  manage.py
```

## Functional Flow (High Level)

1. Admin manages catalog (products, categories, suppliers) and keeps stock healthy.
2. User browses products, adds items to cart and places an order.
3. Admin reviews orders and approves/rejects based on stock and business rules.
4. Approved orders affect balances and operational metrics.
5. Balance top-up requests are reviewed by admin and reflected in user views.
6. Events and gamification modules complement day-to-day club activity.

Extended flow details:

1. Catalog preparation:
  - Admin creates and updates categories, suppliers and products.
  - Product images and details are maintained to keep the catalog clear for users.
2. Purchase intent and cart stage:
  - User navigates catalog filters and product pages.
  - Cart quantities are adjusted before checkout.
3. Order processing lifecycle:
  - Order enters pending review state.
  - Admin validates product availability and operational constraints.
  - Admin action (approve or reject) closes the review cycle.
4. Financial impact stage:
  - Approved operations contribute to sales and dashboard metrics.
  - Related balance records are updated with auditable entries.
5. Balance management cycle:
  - User creates top-up request.
  - Admin approves or rejects request.
  - User balance views refresh without requiring full-page navigation.
6. Engagement and retention cycle:
  - Events open registration windows.
  - User joins or leaves events.
  - Gamification progress and rewards are updated as actions complete.
7. Daily operations loop:
  - Admin actions board centralizes pending tasks.
  - Prioritized cards reduce response time for critical operations.
8. Live synchronization layer:
  - Relevant pages receive websocket-triggered partial refreshes.
  - Admin and user interfaces stay aligned after key state changes.

## Business Rules and Consistency

- Monetary metrics are aligned between dashboard and sales views for approved orders.
- Stock updates are audited when adjusted manually.
- Pending tasks are prioritized in admin actions to reduce operational delays.
- Legacy admin orders listing route remains as a compatibility redirect to actions.

Additional consistency rules:

- Order status governance:
  - Pending orders require explicit admin review before final outcome.
  - Only approved orders are considered in consolidated monetary reporting.
- Monetary date consistency:
  - Financial aggregations use a consistent business timestamp strategy for approved orders.
  - Dashboard and sales pages follow the same calculation criteria.
- Stock integrity:
  - Manual stock edits generate traceable adjustment history.
  - Approval and sales operations must preserve non-negative stock rules.
- Balance traceability:
  - Balance requests and resulting movements are recorded for audit purposes.
  - Admin approval decisions remain visible through logs and operational views.
- UX consistency across devices:
  - Mobile and desktop views share the same domain behavior and validation rules.
  - Visual differences do not alter permission boundaries or business outcomes.
- Real-time coherence:
  - Post-action refresh targets are scoped to affected regions to avoid stale summaries.
  - Notifications and action badges reflect current pending workload.
- Backward compatibility:
  - Legacy routes kept for transition do not duplicate business logic.
  - Canonical workflows are centralized in active action and detail views.

## Roles and Permissions

- Admin role:
  - Full operational control (catalog, sales approvals, balance requests, events, moderation, settings).
  - Visibility over KPIs, logs and pending-action queues.
- User role:
  - Product browsing and purchase flow.
  - Personal balance requests/history.
  - Event participation and profile management.

## Getting Started

Installation and deployment recipe is documented in [receta.md](receta.md).

## Realtime Notes

- Live mode can work with or without Channels/Daphne.
- Transport selection is automatic:
  - WebSocket (`/ws/live-updates/`) when runtime supports Channels + Daphne.
  - Polling fallback otherwise (no extra compilation dependencies required).
- Admin can enable or disable live mode from `System > Environment variables`.
- Admin navigation badges and selected dashboard regions refresh automatically.
- User balance/orders/dashboard regions refresh automatically after relevant events.

Compatibility mode:
- Set `ENABLE_REALTIME=0` to disable live mode entirely.
- Keep `ENABLE_REALTIME=1` to allow live mode (WebSocket when available, polling fallback otherwise).

## Environment Variables

Use `.env.example` as baseline.

### Test/Performance Variables

- `RUN_PERFORMANCE_TESTS`
- `PERFORMANCE_MAX_SECONDS`
- `TEST_VOLUME_MIN`, `TEST_VOLUME_MAX`
- `TEST_BATCH_MIN`, `TEST_BATCH_MAX`
- `TEST_BULK_BATCH_SIZE`
- `TEST_PERF_USERS_COUNT`
- `TEST_PERF_MONTHLY_USERS_COUNT`
- `TEST_PERF_PRODUCTS_COUNT`
- `TEST_PERF_LOW_STOCK_RATIO`
- `TEST_PERF_MONTHLY_ENABLED_DAYS_AGO`
- `TEST_SCALE_USERS_COUNT`
- `TEST_SCALE_MONTHLY_USERS_COUNT`
- `TEST_SCALE_MONTHLY_ENABLED_DAYS_AGO`
- `TEST_SCALE_PENDING_REQUESTS_COUNT`
- `TEST_SCALE_CATALOG_PRODUCTS_COUNT`
- `TEST_SCALE_REVIEWS_COUNT`

### Security/Runtime Variables (optional overrides)

- `SECRET_KEY`
- `DEBUG`
- `ENABLE_REALTIME`
- `LIVE_UPDATES_POLL_SECONDS`
- `ALLOWED_HOSTS`
- `APP_PUBLIC_URL`
- `APP_PUBLIC_PORT`
- `SECURE_SSL_REDIRECT`
- `SESSION_COOKIE_SECURE`
- `CSRF_COOKIE_SECURE`
- `SECURE_HSTS_SECONDS`
- `SECURE_HSTS_INCLUDE_SUBDOMAINS`
- `SECURE_HSTS_PRELOAD`
- `BACKEND_LOG_RETENTION_DAYS`

## Useful Commands

```bash
python manage.py check
python manage.py test
python -m flake8
```

Translations:

```bash
# If gettext is installed
python manage.py compilemessages

# Windows fallback used in this project setup
python -c "import polib,pathlib; base=pathlib.Path('locale'); [polib.pofile(str(p)).save_as_mofile(str(p.with_suffix('.mo'))) for p in base.rglob('django.po')]"
```

## Testing

Run all tests:

```bash
python manage.py test
```

Run by module:

```bash
python manage.py test core.tests.test_smoke
python manage.py test core.tests.test_integration
python manage.py test core.tests.test_e2e
python manage.py test core.tests.test_performance
python manage.py test core.tests.test_web_additional
```

Run by tag:

```bash
python manage.py test --tag=smoke
python manage.py test --tag=integration
python manage.py test --tag=e2e
python manage.py test --tag=performance
python manage.py test --tag=security
python manage.py test --tag=stability
python manage.py test --tag=scalability
```

## Logging and Auditing

- Backend operation logs: `backendlog.log`
- Rotating handler with Windows-safe rollover (`core.logging_handlers.WindowsSafeTimedRotatingFileHandler`).
- Financial and stock changes are auditable:
  - `BalanceLog` for monetary movements.
  - `ProductStockAdjustmentLog` for manual stock changes.

## Operational Recommendations

- Use regular backups for DB and uploaded media.
- Keep translations compiled after editing locale files.
- In production, monitor websocket behavior and reconnect patterns.
- Keep test suites tagged and run smoke/integration checks before releases.

## Troubleshooting Quick Guide

- If live updates do not arrive:
  - Confirm ASGI stack and websocket route (`/ws/live-updates/`) are active.
  - Verify channel layer configuration for your environment.
- If static files are missing in production:
  - Run `collectstatic` and verify static serving strategy.
- If messages/log rotation fail on Windows:
  - Verify the custom Windows-safe rotating handler remains configured.

## Deployment Notes

Before production:

- Set `DEBUG=False`.
- Configure `ALLOWED_HOSTS` for your real domain(s).
- Use HTTPS and keep secure cookie/HSTS settings enabled.
- Prefer PostgreSQL for medium/high load.
- Replace in-memory channel layer with Redis-backed channels.

### Ready-to-use production templates

- `.env.production.example`: baseline environment variables for production.
- `gunicorn.conf.py`: Gunicorn configuration for ASGI (`CashFlow.asgi:application`).


### Database configuration

The app now supports two production-safe DB strategies from environment variables:

- `DATABASE_URL` (recommended), example:
  - `postgresql://cashflow:password@127.0.0.1:5432/cashflow`
- Split variables (`DB_ENGINE`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT`).

If neither is set, the app falls back to SQLite for local development.

CashFlow is designed to remain lightweight and can run on low-power on-prem devices (for example Raspberry Pi / Orange Pi) for small venues.

