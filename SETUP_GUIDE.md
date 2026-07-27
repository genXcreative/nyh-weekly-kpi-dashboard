# Weekly KPI Report — GitHub Actions Setup Guide

This replaces the Colab notebook's scheduling with a free, reliable GitHub Actions
cron job. Once set up, the report rebuilds and publishes itself automatically every
Monday — no Colab session, no browser, nothing to remember to run.

## What's in this folder

Copy all four of these into your `nyh-weekly-kpi-dashboard` repo, preserving the
folder structure exactly:

```
weekly_kpi_report.py              → repo root
requirements.txt                  → repo root
dashboard_template.html           → repo root
.github/workflows/weekly_report.yml → repo's .github/workflows/ folder
```

Note the `.github` folder starts with a dot — make sure your file browser/upload
tool shows hidden files, or use git from the command line (see Step 3).

## Step 1 — Gather your credential values

You already have all of these; they're the same values already sitting in
`MyDrive/NYH_Pipeline/.env` and `google-ads.yaml` from the Colab setup. Open both
files and note down:

From `.env`:
- `SHOPIFY_STORE_DOMAIN`
- `SHOPIFY_CLIENT_ID`
- `SHOPIFY_CLIENT_SECRET`
- `GOOGLE_ADS_CUSTOMER_ID`

From `google-ads.yaml`:
- `developer_token`
- `client_id`
- `client_secret`
- `refresh_token`
- `login_customer_id`

## Step 2 — Add them as GitHub Secrets

In your repo: **Settings → Secrets and variables → Actions → New repository secret**.
Create one secret per row below (name on the left must match exactly):

| Secret name | Value comes from |
|---|---|
| `SHOPIFY_STORE_DOMAIN` | `.env` → `SHOPIFY_STORE_DOMAIN` |
| `SHOPIFY_CLIENT_ID` | `.env` → `SHOPIFY_CLIENT_ID` |
| `SHOPIFY_CLIENT_SECRET` | `.env` → `SHOPIFY_CLIENT_SECRET` |
| `GOOGLE_ADS_CUSTOMER_ID` | `.env` → `GOOGLE_ADS_CUSTOMER_ID` |
| `GOOGLE_ADS_DEVELOPER_TOKEN` | `google-ads.yaml` → `developer_token` |
| `GOOGLE_ADS_CLIENT_ID` | `google-ads.yaml` → `client_id` |
| `GOOGLE_ADS_CLIENT_SECRET` | `google-ads.yaml` → `client_secret` |
| `GOOGLE_ADS_REFRESH_TOKEN` | `google-ads.yaml` → `refresh_token` |
| `GOOGLE_ADS_LOGIN_CUSTOMER_ID` | `google-ads.yaml` → `login_customer_id` |

These are encrypted at rest and never shown in logs — GitHub automatically masks
any secret value if it accidentally appears in workflow output.

Note: this new setup does **not** need a `GITHUB_TOKEN` secret. The old Colab Cell 11
required a personal access token to push from outside GitHub; this workflow runs
*inside* GitHub already, so it uses the automatic, repo-scoped token GitHub injects
into every Action run. One less credential to manage — and the classic PAT you
created earlier for Cell 11 can be deleted at github.com/settings/tokens if you
haven't already, since nothing in this new setup uses it.

## Step 3 — Push the files to your repo

**Easiest for hidden folders like `.github`:** use git from a terminal.

```bash
git clone https://github.com/<your-username>/nyh-weekly-kpi-dashboard.git
cd nyh-weekly-kpi-dashboard
# copy weekly_kpi_report.py, requirements.txt, dashboard_template.html, and the
# .github/workflows/weekly_report.yml file (with folders) into this directory
git add .
git commit -m "Add GitHub Actions weekly KPI pipeline"
git push
```

**Alternative (no terminal):** GitHub's web uploader ("Add file → Upload files")
does support dragging in a `.github/workflows/weekly_report.yml` path directly if
your OS file picker lets you select the whole folder tree — otherwise create the
`.github/workflows/` folder structure by naming the file `.github/workflows/weekly_report.yml`
in the "Add file → Create new file" box, which auto-creates folders from slashes.

## Step 4 — One repo setting to check

**Settings → Actions → General → Workflow permissions** — make sure this is set to
**"Read and write permissions"**. This is what lets the workflow commit the
rebuilt `index.html` back to the repo after each run. If it's set to read-only,
the run will succeed at building the report but fail on the final commit step.

## Step 5 — Test it before waiting for Monday

Go to the **Actions** tab in your repo → click **"Weekly KPI Report"** in the
left sidebar → click **"Run workflow"** (this button exists because the workflow
file includes `workflow_dispatch`, a manual trigger for testing). Watch it run —
it takes maybe 1-2 minutes. If it fails, click into the failed step to see exactly
which secret or permission is off.

Once it succeeds, check `https://<your-username>.github.io/nyh-weekly-kpi-dashboard/`
— it should show the freshly rebuilt report.

## Ongoing schedule

The workflow runs automatically every **Monday at 14:00 UTC** (6am Pacific Daylight
Time / 7am Pacific Standard Time — always Monday morning Pacific either way, cron
itself doesn't observe daylight saving but this time was chosen so it doesn't
matter). To change the day/time, edit the `cron:` line in
`.github/workflows/weekly_report.yml` — [crontab.guru](https://crontab.guru) is a
handy way to build the schedule string.

## Do you need GitHub Pro for this?

No. GitHub's free tier includes enough Actions minutes for a job this small
(roughly 1-2 minutes/week, against a free monthly allowance in the thousands of
minutes) — nothing here requires paying for GitHub. Pro would only matter if you
wanted things unrelated to this pipeline, like advanced repo insights on a private
repo.

## What happens to the Colab notebook?

Nothing — it still works if you ever want to run it manually or debug something
interactively. But once this GitHub Actions workflow is confirmed working, you
won't need to open Colab for the weekly report anymore.

## Running the report for a past date

The manual "Run workflow" button now has an optional **`as_of_date`** field
(format `YYYY-MM-DD`). Leave it blank for a normal run — it'll use the real
current date, exactly like the scheduled Monday run does. Fill it in and every
window in the report (prior 7 days, MTD, YTD, the trend chart) shifts to be
relative to that date instead, as if you'd run it that day — useful for
backfilling a week you missed, or spot-checking what a past week's numbers
looked like.

A couple of things worth knowing:
- This **does update the live page** — `index.html` always reflects whatever
  run happened most recently, backfill or not. If you want the live page back
  on the real current week afterward, just run the workflow again with
  `as_of_date` left blank.
- Every run — scheduled or backfilled — also saves a permanent copy to
  `history/report_<date>.html` and `data/weekly_kpi_data_<date>.json` in the
  repo, so nothing is ever lost even while the live page temporarily shows a
  past date.

## Google Sheets historical log (optional)

Separately from the HTML dashboard, each run can append one row to a Google
Sheet — building a growing table over time (every week's Net/Gross/Spend,
True ROAS, ROI, YTD figures, and annual projections) that you can pivot or
chart in Sheets in ways a static HTML page can't. This is entirely optional;
skip this section and everything else keeps working exactly as before.

**One-time setup:**

1. In Google Cloud Console, create (or reuse) a project, then go to
   **APIs & Services → Library** and enable the **Google Sheets API**.
2. Go to **APIs & Services → Credentials → Create Credentials → Service account**.
   Give it any name (e.g. `nyh-kpi-sheets-writer`), no special roles needed.
3. Open the new service account → **Keys → Add Key → Create new key → JSON**.
   This downloads a `.json` file — this is the only copy you'll get, so keep it
   safe (it's a real credential, treat it like a password).
4. Create a blank Google Sheet (or use an existing one) at sheets.google.com.
   Click **Share**, and share it with the service account's email address —
   it's the `client_email` field inside the JSON file, looks like
   `nyh-kpi-sheets-writer@your-project.iam.gserviceaccount.com` — with
   **Editor** access.
5. Copy the Sheet's ID from its URL: `https://docs.google.com/spreadsheets/d/`**`THIS_PART`**`/edit`.
6. Back in your repo, add two more secrets (Settings → Secrets and variables →
   Actions):
   - `GOOGLE_SHEETS_SERVICE_ACCOUNT_JSON` — paste the **entire contents** of
     the downloaded JSON file (GitHub secrets support multi-line values, so
     this is fine).
   - `GOOGLE_SHEET_ID` — the ID from step 5.

That's it — no code or workflow changes needed, the script already checks for
these two secrets and appends a row on every run once they're set. It creates
a tab named **"History"** automatically the first time it runs, adds a header
row, then one data row per run after that.

Note: this uses a separate Google Cloud service account from your Google Ads
OAuth credentials — they're unrelated systems (Sheets API vs. Ads API), so
this can't reuse the `GOOGLE_ADS_*` secrets already configured.
