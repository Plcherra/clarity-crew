# Clarity Crew — Vision

**North-star doc.** Product intent and scope only. Architecture lives in
[`ARCHITECTURE.md`](ARCHITECTURE.md); phased delivery in [`ROADMAP.md`](ROADMAP.md).

## One-liner

Clarity Crew lets you build software by describing what you want in plain English.
It reads your project, turns your request into a concrete plan, explains that plan
back to you in plain, concrete language — and, once you approve, drives an AI coding
agent to build and verify each piece, then explains what it did and how your app now
works. You direct in plain English; it plans, explains, builds, and checks.

## Who it's for

Solo builders and non-coders who build *with* AI but lose time to the lossy loop:

> vague prompt → the AI builds something → you can't tell what it did → you redo it.

They don't need more raw coding speed (the AI already types fast). They need
**direction and comprehension**: the right task phrased correctly, and a clear
"here's what actually happened" afterward.

## The problem we attack

1. **"What should I even build next?"** — scope is fuzzy, so work sprawls and never ships.
2. **"How do I phrase this so the AI does the right thing?"** — vague prompts → wrong output.
3. **"What did it just do, and did it break anything?"** — no plain-English readout, so trust erodes.

## What it does — the Director Loop

You talk to it in plain English. **Explain runs throughout** — every time it shows
you something (the plan, the build, the test), it's in plain, concrete language that
teaches you your own app, never raw jargon and never vague.

1. **Intent** — you type what you want ("I want the assistant to save memory while I
   talk") or click a preset (launch-ready review · find bugs · create feature). If the
   request is too vague, it **asks clarifying questions** before going further.
2. **Plan** — it turns your intent + the project's own docs + code into a concrete plan
   of what to build/fix, each an approval-ready task.
3. **Explain the plan** — it renders that plan in plain, concrete, structural language,
   so you understand what it proposes and why.
4. **Approve (human)** — you review, edit, and pick what runs, on the plain-English
   version. This is the trust anchor.
5. **Build** — it sends an approved task to a pluggable builder (built-in edit loop by
   default, or your own Cursor via the SDK) which writes the code.
6. **Test** — it runs what can be verified; loops back on failure; marks done when
   green, else clearly **unverified**.
7. **Explain the result** — in plain words: what your app's relevant part looks like,
   what it changed and why, and whether it's verified — tied back to what you asked.

## MVP (what "v1" must do)

- Take a **plain-English request** (or a preset) and, when it's vague, **ask
  clarifying questions** before planning.
- Point at any local project (`--repo`) and turn that request + the project's real
  docs + code into a plan — no hardcoded assumptions about any one app.
- **Explain the plan in plain, concrete, structural language**, and let you **approve
  on that plain-English version** (edit or reject too).
- Apply an approved item through at least one builder and show a plain-English account
  of what changed.
- Verify Python changes with tests; clearly mark everything else as *unverified*.
- Bring-your-own-key for the model provider.

## Non-goals (explicit)

- **Not an inference provider.** We never host models or sell tokens. Users bring
  their own provider keys (BYOK).
- **Not a Cursor reseller.** Each user brings their own Cursor key; we never
  repackage Cursor access under one account. (Confirmed against Cursor's terms.)
- **Not an unattended auto-builder.** A human approves every build task. We do not
  promise "launch your app in days with no review."
- **Not a code editor replacement.** We direct builders; we don't reimplement Cursor.

## Truth principle (inherited, non-negotiable)

Never claim something was built, fixed, or verified unless it actually was and the
user can see it. "Done" from a code read means *"the code path looks right,"* never
*"verified on a device."* Unverifiable changes are labeled unverified — always.
Honesty over impressive-sounding output.

## How we prove it works

We use Clarity Crew to launch our own apps first (starting with clarity-rex, then
two more waiting apps). Those launches are the proof — and the dogfooding that makes
the tool reliably good — before it's sold to anyone else.
