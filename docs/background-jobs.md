# Background jobs (cron on the droplet)

Some work has to happen on a timer, whether or not anyone has the app open. Two management commands
do it, and cron on the production droplet runs them **every minute**.

| Command | What it does each run |
|---|---|
| `dispatch_requests` | Takes back offers no interpreter answered in time, offers waiting requests to free interpreters (emergencies first), fails requests that waited too long (the requester is told, and admins are alerted for an emergency, V2-024), sends booked-session reminders and handles booked-session no-shows. |
| `expire_stale_calls` | Fails calls nobody joined, or that never connected in time, and settles sessions whose departure grace window has ended (someone left and did not come back). |

Without them, requests only move while the requester's app is polling, booked-session reminders and
no-shows never run, and abandoned calls stay "in progress" for ever (they show in the admin's live
sessions and in institution session history).

Both commands are safe to run as often as you like: each run only acts on what is due.

## How it is set up

The backend runs in Docker on the droplet as the container **`signconnect-backend-1`**. The jobs are
one line in **root's crontab** (`crontab -e` as root):

```
* * * * * flock -n /tmp/signconnect-jobs.lock sh -c 'docker exec signconnect-backend-1 python manage.py dispatch_requests && docker exec signconnect-backend-1 python manage.py expire_stale_calls' >> /var/log/signconnect-jobs.log 2>&1
```

- `* * * * *`: every minute.
- `flock -n`: if a run is still going, the next one is skipped instead of piling up.
- Output (and any error) goes to `/var/log/signconnect-jobs.log`.

It lives on the server, not in the code, so it is not changed by deploys. Set up on 23 November 2026.

## Checking it works

```bash
crontab -l                                  # the line above should be listed
tail -6 /var/log/signconnect-jobs.log       # a new pair of lines every minute
```

Each minute adds two lines, for example:

```
expired=0 offered=0 failed=0 scheduled_offered=0 reminders_sent=0 no_shows=0
failed=0 settled=0
```

Numbers above 0 mean the job did something that minute. Zeros are normal when nothing was due.

To run the jobs once by hand:

```bash
docker exec signconnect-backend-1 python manage.py dispatch_requests
docker exec signconnect-backend-1 python manage.py expire_stale_calls
```

## When something goes wrong

| What you see in the log | Why | What to do |
|---|---|---|
| `No such container: signconnect-backend-1` | The backend container was renamed (for example after a change to the Docker setup). | Find the new name with `docker ps`, then fix it in `crontab -e` (it appears twice in the line). |
| Database or connection errors | The database is down or unreachable. | Fix the database; the jobs catch up by themselves on the next minute. |
| No new lines at all | Cron is not running, or the line was removed. | `crontab -l` to check the line; `systemctl status cron` to check cron. |

## Stopping it

Remove the line with `crontab -e`. Nothing else depends on it, so the site keeps working; only the
timed work above stops.

## Keeping the log small

The log grows by two short lines a minute (well under 100 MB a year). To empty it at any time:

```bash
: > /var/log/signconnect-jobs.log
```
