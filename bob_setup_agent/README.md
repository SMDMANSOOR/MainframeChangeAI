# GENAPP Bob Workspace Setup Agent

Sets up the local workspace for CICS GENAPP modernization work, opens Bob
IDE, and drives the whole workflow — typing each prompt into Bob directly
and checking in with you after each step, rather than either guessing a
wait time or requiring you to retype everything yourself.

---

## The focus bug from your last run, and the fix

Your screenshot showed `/init` appearing twice in the Terminal transcript
— that happened because the old design asked you to click into Bob's
chat, then come **back to Terminal** to press Enter to confirm. But
pressing Enter in Terminal makes Terminal the frontmost app again, right
before the keystrokes fired — so `/init` got typed into Terminal, not Bob.

**Fixed:** the script now explicitly brings Bob IDE to the front itself
(`activate`, using the exact app name it found in Step 2) immediately
before typing, every single time. You don't click anything for this to
work correctly.

## No more 5-minute blind polling

Instead of silently polling the filesystem for up to 5 minutes after each
step, the script now does one quick check and then asks you directly:
**press Enter once Bob's done to continue, or type something extra to run
first.** Whatever you type runs immediately (via the same auto-type
mechanism) before the script moves on to its next default step — so you
can inject your own follow-up questions or corrections mid-workflow
without breaking the automation.

---

## What each step actually does (high level)

| Step | Prompt | What it does |
|---|---|---|
| 3 | `/init` (typed as `/in`, see below) | Bob scans the cloned codebase (file structure, key programs, conventions) and writes a distilled summary into `AGENTS.md` at the workspace root. Future Bob sessions in this workspace auto-load that file, so it doesn't re-learn the project from scratch every time. |
| 4 | `Scan the local workspace` (once per language shown in Bob's own dialog, see below) | Builds/updates Bob's local index of the workspace (stored under `.bob/`) for faster, more context-aware answers and document generation. |
| 6 | `Create a Data Dictionary for this application` (chat prompt) | Bob analyzes the application's data structures (copybooks, working storage, key fields) and produces a Data Dictionary document, typically under `docs/`. |
| 7 | The doc/tool naming rules prompt | Defines standing rules for where Bob stores future generated documents (`docs/`) and tools (`tools/`), and the naming convention they must follow, so everything Bob produces later in this workspace stays consistent. |

Steps 3-4 run in **Agent** mode. Steps 6-7 both run in **Z Architect**
mode (the script pauses once, before Step 6, for you to switch).

## Step 6: back to an explicit chat prompt

An earlier version tried Bob's built-in Workflows panel ("Generate data
dictionary" → Start button) instead of a typed prompt, since it looked
like a purpose-built match. **Real usage showed it wasn't actually
producing output** (no Data Dictionary file appeared), so this reverted
to the original approach: auto-typing `Create a Data Dictionary for this
application` as a chat prompt, with the same click-into-chat-box safety
reminder used in Step 4, since retyping into Bob after any prior
click/selection has the same focus risk described there.

## Step 4: scans once per language, driven by Bob's own dialog

An earlier version of this tried to *predict* which languages exist by
asking Bob two separate questions and computing the overlap. **Real usage
showed this was wrong**: Bob's generic answer to "what languages does
scan support" included REXX, but the actual scan dialog only offered
COBOL, JCL, and HLASM — no REXX. HLASM also didn't get picked up by the
prediction, since `AGENTS.md`'s text said "IBM CICS BMS" rather than
literally "HLASM," so simple string-matching missed the connection.

**Fixed by simplifying**: rather than predicting the list, the script now
just types `Scan the local workspace` once, and Bob's own dialog — the
actual authoritative source — shows you the real language list with file
counts (e.g. `COBOL (31 files)`, `JCL (29 files)`, `High Level Assembler
(1 file)`). You click through them one at a time, and the script asks
after each one: *"Are there more languages in Bob's list you haven't
scanned yet?"* — answer `y` to continue, `N` once you're done.

**A focus bug also got fixed here**: after clicking a language button in
Bob's dialog, keyboard focus can land on that button/list rather than
back on the chat text field — so simply re-activating Bob IDE before
retyping the next `Scan the local workspace` isn't reliably enough on its
own. The script now explicitly reminds you to **click directly into Bob's
chat input box** before each retry within this loop, with a short pause
to give you time to do it, specifically because this caused a silent
failure (the prompt just didn't appear) in real testing.

I can't read Bob's chat responses or drive its language-selection dialog
myself, which is why this step stays interactive rather than fully
automatic.

## Step 3 is handled differently: types `/in`, not the full `/init`

Instead of typing the full `/init` command and submitting it
automatically, Step 3 only types `/in` and **stops there — nothing gets
submitted**. This should trigger Bob's own autocomplete dropdown showing
matching slash commands (including `/init`). You then pick the correct
one yourself and press Enter in Bob to actually run it. This exists
specifically because fully automating `/init` was causing unclear looping
behavior — letting you confirm the exact command from Bob's own UI avoids
that entirely, at the cost of one extra manual click for this one step.

---

## Restarting after a failed run: deleting the folder isn't enough

Confirmed from an actual failed run: `/init` produces 4 files, all nested
inside the workspace (`AGENTS.md`, plus `.bob/rules-agent/AGENTS.md`,
`.bob/rules-ask/AGENTS.md`, `.bob/rules-plan/AGENTS.md`) — so deleting and
re-cloning the folder (Step 1) does remove all of them correctly. But Bob
IDE has its own **chat/task state that persists independently of the
files on disk** — there's a "New task" button in its panel for exactly
this reason. Re-cloning resets the files; it does **not** reset that
task state, so Bob can carry over context from a failed previous attempt
even against a freshly re-cloned workspace.

**Fix:** click **New task** in Bob's panel before re-running `/init`
after any failed attempt. The script reminds you of this every time it
reaches the permissions checkpoint, not just after a fresh re-clone.

## One-time setup

**Accessibility permission** (required for the auto-typing to work at
all): System Settings → Privacy & Security → Accessibility → enable for
Terminal (or whatever app runs this script). Quit and reopen Terminal
after granting it.

**Auto-approve permissions in Bob**: in the chat input area, click
**Permissions** and enable at least **Read, Edit, Execute, Mode**, and
turn on **Auto-approve**. Without this, Bob pauses for manual approval on
every write/mode change, which looks like the automation is stuck even
though it's actually just waiting on an unclicked approval button.

---

## Running it

```bash
mkdir -p $MFHOME/bob_agent
cd $MFHOME/bob_agent
chmod +x setup_workspace.sh
./setup_workspace.sh
```

It will:
1. Clone `cics-genapp` — if the folder already exists, asks whether to
   delete and re-clone fresh (warns first if it contains generated output
   worth keeping)
2. Find and open Bob IDE (or bring it to front if already running)
3. Print the permissions reminder, then pause for you to confirm
4. For each of Steps 3, 4, 6, 7: print a high-level description, type the
   prompt into Bob automatically, do a quick filesystem check, then ask
   you to confirm or provide an extra prompt to run first

## Alternative for the rules step (more deterministic than a chat prompt)

Per IBM's Custom Rules documentation, you can write the rule directly as a
file instead of relying on Bob's interpretation of the prompt:
```bash
mkdir -p .bob/rules
cat > .bob/rules/doc-conventions.md << 'EOF'
# Documentation Conventions

- Documents must be stored in the `docs/` directory, tools in `tools/`.
- Document names must follow: `[PREFIX]-[TYPE]-[description].md`
  - PREFIX: the program name (e.g. `BNKMENU`) if the document concerns a
    specific program, `CBSA` for the application as a whole, or `GLOBAL`
    for cross-cutting documents.
  - TYPE: one of `analysis`, `archi`, `docu`, `inv`, `plan`, `spec`.
EOF
```

## If something doesn't work

- **A retry behaves like it still remembers the previous failed attempt,
  even after re-cloning fresh** → click **New task** in Bob's panel. File
  deletion resets the workspace; it doesn't reset Bob's own chat/task
  state.

- **Script says it couldn't find Bob IDE in `/Applications`** → run
  `ls /Applications | grep -i bob`, find the real name, and add it to
  `BOB_IDE_CANDIDATES` near the top of `setup_workspace.sh` (put it first
  in the list).
- **Auto-typing fails with an osascript error** → grant Accessibility
  permission (see One-time setup above), then re-run. The script falls
  back to printing the prompt for manual paste if this happens.
- **`/init` doesn't produce `AGENTS.md`** → confirm you're in Agent mode
  for this step specifically.
- **Z Architect isn't in your mode dropdown** → check your Bob Premium
  Package for Z license/entitlement.
- **Bob seems stuck / nothing is happening after a prompt is typed** →
  check Bob's panel for a pending approval click — this is almost always
  the Permissions setting (Edit/Mode not enabled) from One-time setup.