# Hosting HomeConnect on Render

The repository includes a Render Blueprint at `render.yaml`. It defines one Django web service, a managed PostgreSQL database, and a persistent disk for listing photos and private maintenance attachments. Both the app and database use Singapore, Render's closest available region to Kenya.

## Before creating the hosted services

- Connect the GitHub account that can access `Jessex14/homeconnect` to Render.
- Review the recurring cost before applying the Blueprint. At current prices, this setup is about $13.55/month: the web service is about $7, PostgreSQL compute about $6 plus $0.30 for its 1 GB disk, and the 1 GB media disk about $0.25. Taxes, extra usage, and provider price changes can affect the total. Check Render's final dashboard estimate.
- Do not use a free Render PostgreSQL database for HomeConnect records: free databases expire after 30 days and have no backups. Free web services cannot retain uploaded photos or maintenance attachments.

## Deploy

1. In Render, choose **New** → **Blueprint** and connect `Jessex14/homeconnect` on the `main` branch.
2. Review the Blueprint's web service, PostgreSQL database, region, and recurring costs. Deploy only if the estimate is acceptable.
3. Wait for the first deploy to pass its health check at `/health/ready`. Render supplies the `onrender.com` HTTPS address and TLS certificate.
4. Create the first HomeConnect owner account using the `bootstrap_owner` management command from the Render Shell. Supply the owner email and password as temporary shell environment values; never put them in GitHub or send them in chat.
5. Configure an email provider before relying on invitations or password-reset emails. Keep M-Pesa in sandbox until the hosted callback URL, Daraja credentials, and a production readiness review are complete.

The hosted database starts empty. Local accounts, listings, and photos are not included in GitHub and are not copied by this deployment. They must be migrated or recreated separately. Do not import real tenant information until backups, email, access, and payment handling have been reviewed.

## Environment details

Render generates `DJANGO_SECRET_KEY`, links the managed database through `DATABASE_URL`, and sets `RENDER_EXTERNAL_HOSTNAME`. Django uses that hostname for allowed hosts and CSRF origins, redirects HTTP to HTTPS, serves collected static assets with WhiteNoise, and stores uploaded files beneath the mounted persistent disk.
