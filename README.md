# kunobi-ninja/_workflows

In-org mirror of shared reusable workflows.

## Why this exists

The `signing-runners` runner group restricts which workflows may use the
self-hosted macOS signing runners. GitHub matches that allowlist against the
workflow a job is **defined in** — not the workflow that calls it:

> Only jobs directly defined within the selected workflows will have access to
> the runner group.

and an org-owned runner group cannot reference workflows from another org:

> Organization-owned runner groups cannot access workflows from a different
> organization in the enterprise; instead, you must create an enterprise-owned
> runner group.

`kache` and `kobe` previously called `Zondax/_workflows/.github/workflows/_release-rust.yml`,
which defines the release build matrix. Because that file lives in the `Zondax`
org, it could never be added to a `kunobi-ninja` group's allowlist — no entry
exists that would authorize it. The enterprise-owned group escape hatch is not
available either: both orgs are on the `team` plan.

The practical effect was a silent outage: on 2026-08-11 the `v0.14.0` release
sat with both `Build *-apple-darwin` jobs queued for over two hours while the
runners were online and idle. GitHub reports only "waiting for a runner" in this
situation — there is no "blocked by workflow filter" signal anywhere in the UI
or the API.

Mirroring the workflow into this org makes the jobs in-org, so the allowlist can
name them and `restricted_to_workflows` can stay enabled.

## Contents

| Workflow | Mirrored from |
| --- | --- |
| `.github/workflows/_release-rust.yml` | `Zondax/_workflows@08a71c76eb0ad6011021486816bd65c7e95d9511` |

Byte-identical to its source, as every re-sync should leave it. Verify with:

```bash
diff <(gh api "repos/Zondax/_workflows/contents/.github/workflows/_release-rust.yml?ref=<upstream-sha>" -q .content | base64 -d) \
     .github/workflows/_release-rust.yml
```

Re-sync history:

| Date | Upstream | Why |
| --- | --- | --- |
| 2026-08-12 | `7f61511` (tag `v11`) | initial import |
| 2026-09-07 | `08a71c7` | `download-artifact@v7`→`@v8`, and Zondax/_workflows#132: the release job downloaded every artifact in the run, so one directory-shaped artifact aborted the upload and left kobe v0.43.0 drafted with no binaries |

## Maintenance contract

Read this before changing anything here.

**1. This is a fork, not a subscription.** Upstream fixes in `Zondax/_workflows`
do **not** arrive automatically. Re-syncing is a deliberate act: diff against the
upstream file, port what you want, and land it as a normal PR.

**2. Every consumer pins a ref, and the allowlist pins the same ref.** Callers
reference this workflow by commit SHA. The `signing-runners` allowlist entry
names that same SHA. Bumping one without the other **breaks releases silently** —
the Darwin jobs queue forever with no error. Treat the pin bump and the allowlist
update as one change:

```bash
# after merging a change here, for the new SHA:
gh api --method PATCH orgs/kunobi-ninja/actions/runner-groups/3 \
  -f 'selected_workflows[]=kunobi-ninja/_workflows/.github/workflows/_release-rust.yml@<new-sha>' \
  -f 'selected_workflows[]=kunobi-ninja/kache/.github/workflows/bench.yml@refs/heads/main' \
  -f 'selected_workflows[]=kunobi-ninja/kache/.github/workflows/ci.yml@refs/heads/main'
```

Note the API replaces the whole list, so always pass every entry you intend to
keep. Wildcards are rejected — the ref must resolve to a real branch, tag or SHA.

**3. Verify with the probe, not a release tag.** Ordinary CI proves nothing here:
no job on `main` requests the self-hosted macOS runners, so only the release path
exercises them. But a `-rc` tag is *not* a cheap test — it publishes to crates.io,
and crates.io versions are permanent. Use `_probe-macos.yml` instead:

1. Add the probe to the allowlist at its current SHA (keep every other entry —
   the API replaces the whole list).
2. In `kache`, push a branch `probe/**` containing a workflow triggered on
   `push: branches: ['probe/**']` whose only job is
   `uses: kunobi-ninja/_workflows/.github/workflows/_probe-macos.yml@<sha>`.
3. A green run means a job defined in this repo was authorized onto the group.
   A run that sits queued against idle runners means it was not.
4. Delete the branch and drop the probe entry from the allowlist.

Confirmed working this way on 2026-08-12: claimed by `mac-runner-2` in
`signing-runners` with `restricted_to_workflows: true`.

## Step-level actions are unaffected

This workflow calls `zondax/actions/*` (signing, checkout) as **step-level
actions**. Those are actions, not reusable workflows, so they play no part in
runner-group authorization and deliberately still point at `Zondax`.
