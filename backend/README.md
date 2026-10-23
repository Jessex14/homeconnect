# Kenyan Property Platform — Foundation and Property/Tenancy Phase

Implemented modules: accounts, organizations/roles, property/building/unit records, tenant invitations and tenancy lifecycle, maintenance ticket workflows, rent obligations and payment allocations, manual payment records, a sandbox-capable M-Pesa Express integration, a policy-gated notification outbox, a provider-independent rent Q&A interface, a responsive installable PWA shell, domain/security audit events, and health checks. The rent Q&A provider is deliberately unconfigured by default.

## Requirements and setup

- Python 3.11 or later
- PostgreSQL for deployment and concurrency/release verification

```powershell
cd backend
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

`.env.example` lists configuration keys; the application reads process environment variables and does not parse `.env` files. Set `DJANGO_SECRET_KEY`, `DJANGO_ALLOWED_HOSTS`, and `DATABASE_URL` in the process environment (or a secrets manager). The provided example URL is PostgreSQL. Debug mode may fall back to SQLite for local development only; production requires PostgreSQL and fails closed without required secrets/hosts/database configuration.

```powershell
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

## Repeatable tests

Local fast suite using the test runner's isolated in-memory SQLite database:

```powershell
./run-tests.ps1
```

For CI/release, point `DATABASE_URL` at a disposable PostgreSQL database and run:

```powershell
./run-tests.ps1 -UsePostgres
```

The PostgreSQL suite applies migrations and includes a concurrent overlapping-invitation acceptance test. A PostgreSQL exclusion constraint backed by `btree_gist` protects tenancy periods at the database layer. The concurrency acceptance is skipped under SQLite. Do not use production data for tests.

## Current workflows

- Organization owners create properties; owners and assigned property managers create/update property records, buildings and units. Caretakers can read assigned property/unit information; tenants can see only properties/units related to their own accepted or historical tenancy.
- Owners assign existing accounts as managers or caretakers to explicit properties and can deactivate those memberships from the HomeConnect Team screen. Manager/caretaker registration and account invitations are not included yet; each person must already have an account.
- Tenant invitations are sent to an email address with a one-time link to the HomeConnect invitation review page. A signed-in account whose email matches the invite can review terms and accept for themselves. New link tokens are placed in the browser URL fragment and removed from browser history as soon as the app loads; the server stores only a token hash, then invalidates it after use. The invitee must already have an account; account registration and email verification policy remain open.
- Owners and assigned managers can start an invitation from a unit in the Properties screen. They enter rent, due day, dates, and deposit terms; the app marks the end date as exclusive and reports email delivery status. They can review invitation history, resend a pending link, or cancel a pending invitation from that unit's invitation list.
- Tenancy date ranges use `[start_date, end_date)`. The end date is exclusive; adjacent records are allowed when one ends on the date the next begins. This is a storage/business convention and must be confirmed against the product's tenancy agreement wording before launch.
- Future accepted tenancies are activated by an authorized manager on/after the start date. A manager records termination explicitly with an effective end date; the system does not infer legal termination or automatically end a tenancy merely because a planned date arrives.
- Accepted terms are versioned and preserved. This phase does not offer an accepted-terms amendment workflow; changes require product approval and explicit tenant re-acceptance design.
- The HomeConnect Tenancies screen shows the signed-in tenant's own records and the current organization's manager-visible records. The server supplies manager-only activate/end capabilities; ending a tenancy requires an effective date and remains an operational record, not a legal determination.
- Organization owners can configure their own Daraja sandbox merchant account under **M-Pesa**. Tenant STK Push requests use that organization's Paybill/Till, and successful callbacks create immutable M-Pesa-confirmed payment records. Manual records stay labeled as unconfirmed. Payment records are not automatically treated as rent paid: a manager creates a monthly rent charge, then allocates recorded funds to it. Excess funds remain available for later charges. The screen shows remaining balances and overdue charges.
- Full-month rent charges use the accepted monthly rent. For the first or last partial month, the landlord enters the amount agreed with the tenant. The app does not prorate automatically. Approved changes are recorded as signed adjustments with a reason; payment allocations and their reversals also preserve an audit trail.
- Rent Q&A is scoped to one authorized tenancy at a time. It uses server-computed ledger balances, sends only tenancy/rent/payment facts to the configured provider, validates returned source IDs against supplied records, and cannot write or alter records. Set `RENT_QA_PROVIDER=apps.assistant.openai_provider.OpenAIRentAnswerProvider`, `OPENAI_API_KEY`, and optionally `OPENAI_MODEL` in the server environment to enable it; the default model is `gpt-6-luna`. API usage is billed separately by token. Leave `RENT_QA_PROVIDER` unset to keep the feature disabled. The OpenAI adapter uses structured JSON output and requests `store=false`; the API key is read only from the server environment and is never sent to the browser. Review the provider's current [API pricing](https://developers.openai.com/api/docs/pricing) and data handling before enabling it.
- Tenants and authorized property staff can open and print a receipt for a payment record. Manual receipts explicitly say the payment was not independently verified; M-Pesa receipts include the Safaricom receipt when available. Receipts show reversals and do not imply that a payment has been allocated to rent.
- M-Pesa callback routes require a public HTTPS base URL (`MPESA_CALLBACK_BASE_URL`); localhost cannot receive Safaricom callbacks. Sandbox is the default. Production requests remain disabled unless the host explicitly sets `MPESA_ALLOW_PRODUCTION=true`. Store a generated Fernet key in `MPESA_CREDENTIALS_ENCRYPTION_KEY`; changing it without re-encrypting the stored credentials makes them unreadable. In debug mode only, encryption falls back to a key derived from `DJANGO_SECRET_KEY`.
- Daraja consumer credentials and passkeys are saved encrypted per organization and never returned to the browser. Keep the database, application secret, and encryption key protected. Do not put live credentials in `.env.example`, source control, or chat.
- Important property, invitation and tenancy transitions are recorded in append-only domain audit events. Audit reads are not exposed as an API yet; database-level privileges/retention controls remain deployment work.

## API endpoints

All paths are under `/api/v1/`, require authenticated sessions except existing account recovery endpoints, and use server-side object scoping.

- `GET/POST /organizations/{id}/properties`
- `GET/POST /organizations/{id}/members`, `DELETE /organizations/{id}/members/{membership_id}`
- `GET/PATCH /properties/{id}`, `POST /properties/{id}/archive`
- `GET/POST /properties/{id}/buildings`, `GET/PATCH /buildings/{id}`
- `GET/POST /properties/{id}/units`, `GET/PATCH /units/{id}`
- `POST /units/{id}/invitations`
- `POST /invitations/review`, `/invitations/accept`, `/invitations/decline`
- `POST /invitations/{id}/resend`, `/invitations/{id}/cancel`
- `GET /tenancies`, `GET /tenancies/{id}`, `POST /tenancies/{id}/activate`, `/tenancies/{id}/end`
- `GET/PUT /organizations/{id}/mpesa`, `POST /tenancies/{id}/mpesa-payments`, `GET /mpesa-payments/{attempt_id}`; Safaricom callbacks arrive at `/payment-callbacks/mpesa/{callback_token}`
- `GET/POST /tenancies/{id}/payments`, `GET /payments/{payment_id}/receipt`, `POST /payments/{payment_id}/reverse`
- `GET/POST /tenancies/{id}/rent-obligations`, `POST /rent-obligations/{obligation_id}/adjustments`, `/rent-obligations/{obligation_id}/allocations`, `POST /payment-allocations/{allocation_id}/reverse`
- `POST /tenancies/{id}/rent-questions`
- `GET/POST /maintenance-tickets`, `GET /maintenance-tickets/{id}`, `GET /maintenance-attachments/{attachment_id}`, `/maintenance-tickets/{id}/assign`, `/maintenance-tickets/{id}/transition`, and `GET/POST /maintenance-tickets/{id}/comments`

Health endpoints are `/health/live` and `/health/ready`. Readiness returns only status and does not expose database details.

## Security and deployment notes

- Browser APIs use Django sessions, CSRF protection, HttpOnly/SameSite cookies, and secure cookies outside debug mode. Login is explicitly CSRF-protected.
- Server-derived organization membership and property assignments determine scope. Tenant access requires an accepted tenancy relationship; client-supplied owner/tenant/property identifiers do not grant authority.
- Invitation email delivery uses Django's configured email backend. Configure `TENANT_INVITATION_FRONTEND_URL` to the actual review page and `TENANT_INVITATION_TTL_HOURS` to the approved expiry policy. Failed delivery is recorded so an authorized manager can retry.
- Maintenance requests are created by tenants with an active tenancy. Tenants may attach one JPEG, PNG or WebP photo up to 8 MB. The server decodes and re-encodes it, removes embedded metadata, and stores it outside the public media directory. Photo downloads require the tenant or authorized property staff; responses are private and not cached. Owners and assigned managers can view and assign tickets; caretakers can view/update only tickets assigned to their property membership. Tenant-visible and internal comments are separated, status/assignment/comment events are retained, and a closure note is required. Email/SMS maintenance notifications are not enabled; channel consent/vendor choices remain open.
- The HomeConnect maintenance screen supports opening a request, reading role-visible updates, posting tenant-visible comments (or internal staff notes where authorized), making status transitions exposed by the scoped detail API, and assigning requests to active managers/caretakers assigned to that property. Only owners and managers receive assignment candidates; the server validates each assignment.
- Maintenance submission creates a minimal notification intent in the same database transaction as the ticket and audit event. Intents remain `awaiting_policy`; there is no dispatcher and no email/SMS is sent until channel preferences, consent, vendor, and retention decisions are approved.
- Security/domain audit rows block ordinary model/queryset updates and deletes. Production database permissions, backup immutability, audit retention, monitoring and restore drills still require deployment design.
- The same-origin PWA is served at `/`; its service worker caches only the app shell and static assets. API responses and authenticated records are never stored in the offline cache. Run `python manage.py collectstatic` as part of a production static-assets deployment.
- Password recovery sends a short-lived, single-use link. Outbound email credentials must remain in deployment secrets.
