# Deploying the free demo (Render + Neon + Vercel)

This is a **demo/staging** setup on free tiers. For the pilot, use `config.settings.prod` and a
paid host with a persistent disk and real email (see the limits at the bottom).

## 1. Database (Neon)

1. Create a free project at neon.com and copy the **connection string**
   (`postgresql://user:password@ep-xxxx.neon.tech/dbname?sslmode=require`).
2. Keep it secret. It goes into Render as `DATABASE_URL`.

## 2. Backend (Render)

1. Render dashboard: **New > Blueprint**, pick the `vri-api` repo and the branch to deploy (`dev`).
   Render reads `render.yaml`. (Or create a Web Service by hand: Runtime Docker, plan Free.)
2. Fill in the variables Render asks for:
   | Variable | Value |
   | --- | --- |
   | `DATABASE_URL` | the Neon connection string |
   | `CORS_ALLOWED_ORIGINS` | your Vercel URL, e.g. `https://signconnect.vercel.app` (no trailing slash) |
   | `CSRF_TRUSTED_ORIGINS` | the same Vercel URL (needed for Django admin login over HTTPS) |
   `SECRET_KEY` is generated automatically. `DJANGO_SETTINGS_MODULE=config.settings.staging` is set by the blueprint.
3. Deploy. On every start the container runs migrations, collects static files and starts gunicorn.
4. Check `https://<your-service>.onrender.com/api/docs/` opens Swagger.

### Create an admin user (once)

Render's free plan has no shell, so create the admin from your own machine against the Neon database
(run in the API folder, with Docker running):

```powershell
docker compose run --rm -e DATABASE_URL="<neon connection string>" -e DJANGO_SETTINGS_MODULE=config.settings.staging backend python manage.py createsuperuser
```

## 3. Web (Vercel)

1. Import the `vri-web` repo in Vercel. Framework: Vite.
2. Add the environment variable **`VITE_API_URL`** = `https://<your-service>.onrender.com`
   (no trailing slash). Vite bakes it in at build time, so redeploy after changing it.
3. `vercel.json` in the web repo makes page refreshes on routes such as `/register/deaf` work.
4. Copy the Vercel URL into `CORS_ALLOWED_ORIGINS` and `CSRF_TRUSTED_ORIGINS` on Render and redeploy the backend.
   Preview deployments get different URLs; only the production URL is allowed.

## Limits of the free setup

- Render sleeps the backend after 15 minutes without traffic; the next request takes about a minute.
  Open the site a few minutes before a demo, or ping `/api/health/` (it also wakes the Neon database) with a free uptime monitor.
- No persistent disk: uploaded certificates are lost on every redeploy or restart.
- No email: account recovery codes are printed in the Render logs.
- One gunicorn worker and an in-memory cache, so rate limits are per process. Fine for a demo.
- Swagger docs are public in `staging`. They are staff-only in `prod`.
