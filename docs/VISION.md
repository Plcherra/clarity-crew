# Clarity Crew — Vision

**North-star doc.** Product intent and scope only. Architecture lives in
[`ARCHITECTURE.md`](ARCHITECTURE.md); phased delivery in [`ROADMAP.md`](ROADMAP.md).

## One-liner

Clarity Crew reads any software project, tells you in plain English what it needs
to reach launch, and — with your approval — drives an AI coding agent to build and
verify each piece. You direct; it plans, builds, explains, and checks.

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

1. **Plan** — read the project's own docs + code, produce a ranked, plain-English
   list of what to build/fix for launch, each as an approval-ready prompt.
2. **Approve (human)** — you review, edit, and pick what runs. This is the trust anchor.
3. **Build** — send the approved prompt to a pluggable builder (a built-in edit
   loop, or the user's Cursor via the SDK) which writes the code.
4. **Explain** — "you asked X · the AI did Y · result Z · what could break" in plain words.
5. **Test** — run what can be verified; loop back on failure; mark done when green.

## MVP (what "v1" must do)

- Point at any local project (`--repo`) and produce a launch plan grounded in that
  project's real docs + code — no hardcoded assumptions about any one app.
- Draft approval-ready prompts for each plan item.
- Apply an approved item through at least one builder and show a plain-English diff.
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
