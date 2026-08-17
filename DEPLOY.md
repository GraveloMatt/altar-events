# Putting the calendar live

Written for Matt. **No terminal, no installs.** Everything here happens in a
browser, on github.com plus one stop at your domain registrar.

Total time: about 15 minutes, plus waiting for DNS.

You'll need: the `altar-events` folder unzipped on your computer, and your
Anthropic API key on the clipboard when you get to step 3.

---

## Before you start

Unzip `altar-events.zip`. You should end up with a folder called
`altar-events` containing `sources.yml`, `build.py`, a `site` folder, and so
on. **Open that folder** — the files inside it are what you'll be dragging.
Don't drag the folder itself.

One thing to know up front: **the repository has to be public.** GitHub Pages
won't serve a website from a private repository unless you're on a paid plan.
There's nothing secret in here — it's a list of public bike events — and your
API key is stored separately, not in the files. But it's a hard stop at the end
if you set it to private now, so set it to public in step 2.

---

## 1. Make the repository

1. Go to **https://github.com/new**
2. **Repository name:** `altar-events`
3. Leave the description blank (or write "WNC cycling calendar").
4. Select **Public**. ← this is the one that matters
5. **Do not** tick "Add a README file," and leave the .gitignore and license
   dropdowns on None. The folder already has these.
6. Click **Create repository**.

You'll land on a mostly empty page with some setup instructions. Ignore all of
it.

---

## 2. Upload the files

1. On that page, find the link **"uploading an existing file"** — it's in the
   grey text under the setup instructions. Click it.
2. Open your `altar-events` folder in a File Explorer window next to the
   browser.
3. Select **everything inside** the folder — click one file, then Ctrl+A — and
   drag it all onto the browser window.

   Make sure you're dragging the *contents*, not the folder. When it's right,
   GitHub lists individual files like `build.py`, `sources.yml`,
   `requirements.txt`.

4. Windows hides folders whose names start with a dot, and **two of them
   matter**: `.github` (which contains the daily build job) and `.gitignore`.
   In File Explorer, go to the **View** tab and tick **Hidden items**, then
   drag those two in as well.

   To check it worked: after uploading, the file list should include a
   `.github` folder. If it doesn't, the calendar will never build itself.

5. Scroll to the bottom, leave the commit message as-is, and click
   **Commit changes**.

---

## 3. Add your API key

This is what lets the calendar read the event pages that don't publish a proper
feed — Asheville on Bikes, DARC, and a few others.

1. In your repository, click **Settings** (the tab along the top, far right).
2. In the left sidebar: **Secrets and variables** -> **Actions**.
3. Click the green **New repository secret** button.
4. **Name:** `ANTHROPIC_API_KEY` — exactly that, all caps with underscores.
5. **Secret:** paste your key.
6. Click **Add secret**.

Once saved, GitHub will never show you the key again, and it does not appear in
the public files. That's the point — it's why the repository can be public.

---

## 4. Turn on the website

1. Still in **Settings**, click **Pages** in the left sidebar.
2. Under **Build and deployment** -> **Source**, change the dropdown from
   "Deploy from a branch" to **GitHub Actions**.
3. A **Custom domain** box appears further down. Type `calendar.altar.bike`
   and click **Save**.

GitHub will show a warning that the domain isn't verified yet. That's expected
— it'll clear once you do step 6.

---

## 5. Run the first build

1. Click the **Actions** tab at the top of the repository.
2. In the left sidebar, click **Build events calendar**.
3. On the right, click the **Run workflow** dropdown, then the green
   **Run workflow** button.
4. Wait a minute, then refresh. You'll see a run appear. Click into it.

**What to expect:** a green tick, and a summary at the top of the page reading
something like *"31 events published"* with a line for each source marked
`ok`, `stale` or `down`.

Some sources **will** say `down`, and that's fine — the notes below explain
which ones are known to be empty or blocked right now. What matters is that the
run itself finishes green and the number of events isn't zero.

From here it rebuilds itself every morning at 5:17am, before the shop opens.

---

## 6. Point the domain (the only non-GitHub step)

At whoever you registered `altar.bike` with, add one DNS record:

| field | value |
|---|---|
| Type | `CNAME` |
| Name / Host | `calendar` |
| Value / Points to | `GraveloMatt.github.io` |
| TTL | leave default |

Some registrars want the name as `calendar.altar.bike` instead of just
`calendar` — if it complains, try the other form.

DNS usually takes 10-30 minutes, occasionally a few hours. When it's live,
`calendar.altar.bike` will load the calendar.

**Then go back one last time:** Settings -> Pages, and tick **Enforce HTTPS**.
This checkbox is greyed out until the domain resolves, which is why it's last.

---

## Done. What you should see

- `calendar.altar.bike` — the calendar page
- `calendar.altar.bike/events.ics` — the subscribe link, works in Google
  Calendar, Apple Calendar and Outlook
- `calendar.altar.bike/races.ics` and `/trail-work.ics` — filtered versions
- `calendar.altar.bike/submit.html` — the "add your event" form

Hand the `.ics` link to Walsh for the altar.bike embed.

---

## Reading the first build honestly

**Expect it to look thinner than you're hoping.** Two of the biggest sources
are not contributing, for reasons that are understood and written down:

- **Blue Ridge Bicycle Club** is behind a members-only wall. They sell advance
  ride notice as a paid member benefit, so their ride calendar isn't public.
  We're not going to scrape past that. Their public events (WNC Flyer, Tour de
  Transylvania) still show up via BikeReg and RunSignup.
- **Pisgah Area SORBA's** events page hasn't been updated since February — they
  run their actual signups through a system called VolunteerHub. **If you can
  get me that VolunteerHub link** (click any "click Here!" button on their
  events page and read the address bar) it becomes a proper feed and their
  trail days start appearing automatically.

Also seasonal, not broken: the three youth/NICA sources are empty from July
through November because North Carolina is a *spring* league. They'll fill in
around November 1.

**The one thing that would most change how full it looks** is a Ride with GPS
API key — it's free and self-serve at `ridewithgps.com/api/v1/doc`. That single
key unlocks Asheville on Bikes' Thursday rides and is the realistic way to
reach BRBC's 600+ club routes. Same steps as step 3 above, but name the secret
`RWGPS_API_KEY`.

---

## If something goes wrong

**The Actions tab is empty / no workflow appears.** The `.github` folder didn't
upload. Redo step 2 part 4 — turn on Hidden items in File Explorer and drag it
in.

**The run is red.** Click into it and open the failed step. If it mentions
`ANTHROPIC_API_KEY`, the secret name is misspelled — it must be exactly
`ANTHROPIC_API_KEY`.

**Pages says "must be public to deploy."** The repository got created as
private. Settings -> scroll to the bottom -> Danger Zone -> Change visibility.

**The site 404s after DNS resolves.** Give it another 20 minutes, then check
Settings -> Pages still shows `calendar.altar.bike` in the custom domain box —
it occasionally clears itself on the first deploy. Re-enter and save.

**An event is wrong, or one you know about is missing.** Don't fight the
scraper. Open `data/manual.yml` in GitHub (click the file, then the pencil
icon), add the event by hand following the commented example at the bottom, and
commit. Hand-entered events outrank everything and a matching title+date will
overwrite a bad scrape. Saving triggers a rebuild on its own.
