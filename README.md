# HomeConnect

HomeConnect is a property and tenancy management application for landlords and property teams in Kenya. It includes property and unit listings, tenant invitations, tenancy records, maintenance requests, rent tracking, manual payments, and an M-Pesa Express integration designed around each landlord's Paybill or Till.

## Project layout

- `backend/` — Django application, API, web app, migrations, and tests.
- `outputs/kenyan-property-platform-spec.md` — product and architecture specification.

## Run locally

See the [backend setup and deployment notes](backend/README.md). The PostgreSQL test suite uses a disposable test database; do not point tests at production data.

## Status

This project is under active development. The local PostgreSQL suite currently passes. Production hosting, HTTPS, email delivery, and live M-Pesa credentials still need to be configured before launch.
