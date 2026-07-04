# Files to copy into the PUBLIC data repo (`mulligan_coach_data`)

These files are **staged here, in the private code repo, but belong in
the public `vonbeschwitz/mulligan_coach_data` repo.** They're kept here
so they get code review with the feedback feature; they are *not*
wired into anything in this repo. Copying them over is a one-time,
manual owner action — nothing in the build or CI touches them.

## Why they live in the public data repo

The overlay's "Send feedback…" entry falls back to
`https://github.com/vonbeschwitz/mulligan_coach_data/issues` until the
Google Form is set up (and Issues stays a good permanent option). For
that fallback to feel polished, the public data repo needs GitHub issue
templates. The main code repo is private, so its Issues aren't reachable
to users — the public data repo is the right home.

## What's here

```
.github/ISSUE_TEMPLATE/
├── bug_report.yml       # Structured bug form (app/data/OS + log snippet)
├── feature_request.yml  # Feature-request form
└── config.yml           # Chooser config (+ optional Google Form link slot)
```

## How to copy them over (one time)

Easiest is a local clone of the public data repo:

```bash
# From wherever you keep the public data repo checkout:
git clone https://github.com/vonbeschwitz/mulligan_coach_data.git
cd mulligan_coach_data

# Copy the staged .github tree in (adjust the source path to wherever
# this code repo lives on your machine):
mkdir -p .github/ISSUE_TEMPLATE
cp -r /path/to/Mulligan_Coach/packages/overlay/packaging/data_repo_files/.github/ISSUE_TEMPLATE/* \
      .github/ISSUE_TEMPLATE/

git add .github/ISSUE_TEMPLATE
git commit -m "Add issue templates (bug report + feature request)"
git push
```

On Windows PowerShell the copy is:

```powershell
New-Item -ItemType Directory -Force .github\ISSUE_TEMPLATE
Copy-Item -Recurse -Force `
  C:\Users\basti\Documents\Mulligan_Coach\packages\overlay\packaging\data_repo_files\.github\ISSUE_TEMPLATE\* `
  .github\ISSUE_TEMPLATE\
```

Then, in the public repo's **Settings → General → Features**, make sure
**Issues** is enabled (it is by default). Open the repo's *Issues → New
issue* page to confirm the two templates show up.

## Optional: link the Google Form here too

Once you create the feedback Google Form (see the OWNER CONFIGURATION
block in
`packages/overlay/src/mulligan_coach_overlay/feedback.py`), you can also
surface it on the "New issue" chooser by uncommenting the `contact_links`
block in `config.yml` and pasting the form URL. That's independent of
wiring the form into the app — do either, both, or neither.
