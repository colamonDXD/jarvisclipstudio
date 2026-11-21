# Publishing workspace

The `/publishing` page prepares clips for posting and keeps the user's
own posting records. It **never publishes anything**: the user posts by
hand in the platform app, then records the post here.

## What it does

- **Ready-to-post board** — every finished clip with per-platform
  readiness for YouTube Shorts, Instagram Reels and TikTok. Readiness is
  judged from facts the pipeline already recorded: the rendered size
  and caption presence from the clip's QC report, its duration and its
  QC status. A fact that was never recorded shows as *Not recorded* —
  readiness never guesses. Rules encode the platforms' published
  limits (Shorts: square/vertical up to 3 minutes; Reels: shapes
  1.91:1–9:16, over 3 minutes not recommended to new audiences, hard
  limit 20 minutes; TikTok: vertical native, 10-minute in-app limit
  warns rather than blocks).
- **Bundle download** — one ZIP per clip with the existing deliverable
  bundle (`clip.mp4`, `cover.jpg`, `post.txt`, `edl.json`,
  `profile.txt`, `image_sources.json`); clips rendered before bundles
  existed download as the lone clip file.
- **Post records** — one record per clip per platform (URL, time,
  optional campaign, optional campaign-submission link). Whop rule: a
  posted record without a submission link shows a plain reminder —
  Content Rewards links are due within 30 minutes of posting — until
  the link is saved or the reminder is dismissed.
- **Campaigns** — user-created groups (name, platform, campaign URL,
  budget, notes) for posts. Deleting a campaign keeps its posts; they
  simply lose the grouping.
- **Analytics** — two clearly separated sections:
  - *Production (recorded by the app)*: clips per day, per style, QC
    and Quality Director compliance outcomes, hook outcomes, average
    stage timings and provider-reported token totals.
  - *Performance (entered by you)*: views, likes, comments and
    earnings the user typed in per post, rolled up per clip and per
    campaign. A post with no numbers typed in counts as unmeasured —
    never as zero — and totals are never estimated.

## Data

Migration `20261109_0001_publishing.sql` adds `campaigns`,
`clip_posts` and `clip_post_stats`. All rows are user-created; nothing
is scraped, fetched or inferred.

## Deliberately not built

- **Posting integrations** — no platform APIs, no connected-account
  UI. Posting is a manual user action by design.
- **YouTube channel monitoring** — would need the user's official
  account authorisation; not attempted.
- **Revenue estimates** — performance numbers exist only when typed
  in. Budget on a campaign is the user's own note, not a forecast.
