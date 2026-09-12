#!/bin/bash
# Sets up the local workspace for CICS GENAPP modernization work and drives
# the whole Bob IDE workflow: clone -> open Bob -> /init -> scan -> (switch
# mode) -> Data Dictionary -> doc/tool rules.
#
# IMPORTANT FIX: earlier versions asked you to click into Bob's chat, then
# come BACK to Terminal to press Enter to confirm - but pressing Enter in
# Terminal makes Terminal frontmost again right before the keystrokes fire,
# so the prompt got typed into Terminal instead of Bob. Fixed: the script
# now explicitly activates Bob IDE itself (by the exact app name it found
# in Step 2) immediately before typing, every time - you don't need to
# click anything for the script to bring the right window forward.
#
# ALSO CHANGED: no more blind 5-minute filesystem polling after each step.
# Instead, after typing a prompt, the script does one quick check and then
# asks you directly: press Enter once Bob's done to continue, or type an
# extra prompt to run first (it runs before moving to the next default
# step). This is simpler and more honest than guessing a wait time.

set -e

REPO_URL="https://github.com/cicsdev/cics-genapp.git"
WORKSPACE_DIR="./cics-genapp"
FOUND_APP=""   # set during Step 2, used later to bring Bob to the front

# ---------------------------------------------------------------------------
# Helper: activate the app by name, then type text into whatever now has
# focus (line-by-line with Shift+Return, plain Return only at the end so
# multi-line prompts don't submit early).
# ---------------------------------------------------------------------------
type_into_bob() {
  local text="$1"
  local press_enter="${2:-true}"
  echo "--- Prompt (typing this into Bob now) ---"
  echo "$text"
  echo "------------------------------------------"

  if [ -n "$FOUND_APP" ]; then
    echo "Bringing '$FOUND_APP' to the front..."
  else
    echo "WARNING: no app name known to activate - typing into whatever is"
    echo "currently frontmost. Click into Bob's chat input now if needed."
  fi

  if ! osascript <<APPLESCRIPT
on run
  $( [ -n "$FOUND_APP" ] && echo "tell application \"$FOUND_APP\" to activate" )
  delay 1.2
  set theText to "$(printf '%s' "$text" | sed 's/\\/\\\\/g; s/"/\\"/g')"
  set AppleScript's text item delimiters to linefeed
  set theLines to text items of theText
  set numLines to count of theLines
  tell application "System Events"
    repeat with i from 1 to numLines
      keystroke (item i of theLines)
      if i < numLines then
        key code 36 using {shift down}
      end if
    end repeat
    $( [ "$press_enter" = "true" ] && echo "key code 36" )
  end tell
end run
APPLESCRIPT
  then
    echo ""
    echo "Auto-type failed (likely missing Accessibility permission)."
    echo "Grant it in System Settings > Privacy & Security > Accessibility"
    echo "for Terminal, quit and reopen Terminal, then re-run this script."
    echo "For now, paste this manually into Bob's chat:"
    echo "$text"
  fi
}

# Quick, single, non-blocking check - informational only, not a wait loop.
quick_check() {
  local target="$1"
  if [ -e "$target" ]; then
    echo "(Quick check: '$target' already exists.)"
  else
    echo "(Quick check: '$target' not there yet - may still be processing.)"
  fi
}

# After typing a prompt: ask directly rather than blindly polling for up to
# 5 minutes. Blank Enter continues to the next default step. Anything typed
# runs first (via type_into_bob), then asks again - so you can chain as
# many extra prompts as you want before moving on.
confirm_or_extra() {
  local check_path="$1"
  if [ -n "$check_path" ]; then
    quick_check "$check_path"
  fi
  while true; do
    read -p "Press Enter once Bob's done with this step to continue, or type an extra prompt to run first: " EXTRA
    if [ -z "$EXTRA" ]; then
      break
    fi
    type_into_bob "$EXTRA"
    if [ -n "$check_path" ]; then
      quick_check "$check_path"
    fi
  done
}

# ---------------------------------------------------------------------------
# Step 1: clone (always fresh - confirms before deleting an existing folder)
# ---------------------------------------------------------------------------
echo "=== Step 1: cloning cics-genapp ==="
if [ -d "$WORKSPACE_DIR" ]; then
  echo "$WORKSPACE_DIR already exists."
  if [ -f "$WORKSPACE_DIR/AGENTS.md" ] || [ -d "$WORKSPACE_DIR/.bob" ] || [ -d "$WORKSPACE_DIR/docs" ]; then
    echo "NOTE: it contains generated output (AGENTS.md, .bob/rules-*/AGENTS.md,"
    echo "docs/) from a previous run - deleting will lose that."
  fi
  read -p "Delete it and start fresh? [y/N]: " DELETE_CONFIRM
  if [ "$DELETE_CONFIRM" = "y" ] || [ "$DELETE_CONFIRM" = "Y" ]; then
    rm -rf "$WORKSPACE_DIR"
    git clone "$REPO_URL" "$WORKSPACE_DIR"
    echo ""
    echo "IMPORTANT: deleting the folder removes AGENTS.md and all"
    echo ".bob/rules-*/AGENTS.md files, since they're all generated inside"
    echo "this workspace. But it does NOT reset Bob's own chat/task state -"
    echo "Bob remembers the previous task independently of the files on"
    echo "disk. Click 'New task' in Bob's panel before running /init again,"
    echo "or Bob may carry over context from the failed previous attempt."
  else
    echo "Keeping existing $WORKSPACE_DIR as-is (not re-cloning)."
  fi
else
  git clone "$REPO_URL" "$WORKSPACE_DIR"
fi
cd "$WORKSPACE_DIR"
echo ""

# ---------------------------------------------------------------------------
# Step 2: open Bob IDE (checks if already running first, opens if not)
# ---------------------------------------------------------------------------
echo "=== Step 2: opening Bob IDE ==="
BOB_IDE_CANDIDATES=("IBM Bob - Insiders" "IBM Bob Insiders" "Bob IDE" "IBM Bob" "IBM Bob IDE" "Bob")

for name in "${BOB_IDE_CANDIDATES[@]}"; do
  if [ -d "/Applications/${name}.app" ]; then
    FOUND_APP="$name"
    break
  fi
done

if [ -z "$FOUND_APP" ]; then
  echo "Could not find any of these in /Applications: ${BOB_IDE_CANDIDATES[*]}"
  echo "Trying known CLI shim names instead (code-insiders, code)..."
  if command -v code-insiders >/dev/null 2>&1; then
    code-insiders .
    FOUND_APP="Visual Studio Code - Insiders"
    echo "Opened via 'code-insiders'."
  elif command -v code >/dev/null 2>&1; then
    code .
    FOUND_APP="Visual Studio Code"
    echo "Opened via 'code'."
  else
    echo "None of those worked either. Run this to find the real app name,"
    echo "then add it to BOB_IDE_CANDIDATES near the top of this script:"
    echo "  ls /Applications | grep -i bob"
    echo "Or open this folder manually: $(pwd)"
  fi
else
  echo "Found: ${FOUND_APP}.app"
  IS_RUNNING=$(osascript -e "application \"${FOUND_APP}\" is running" 2>/dev/null || echo "false")
  if [ "$IS_RUNNING" = "true" ]; then
    echo "${FOUND_APP} is already running - bringing it to the front and opening this folder in it."
  else
    echo "${FOUND_APP} is not running - opening it now."
  fi
  open -a "$FOUND_APP" "$(pwd)"
fi
echo ""

echo "IMPORTANT - before continuing, set auto-approve permissions in Bob:"
echo "In the chat input area, click 'Permissions' and enable at least:"
echo "  Read, Edit, Execute, Mode"
echo "(Subagent is fine if already checked.) Also flip 'Auto-approve' on."
echo "Without this, Bob pauses for manual approval on every write/mode"
echo "change, which will look like the automation is stuck."
echo ""
echo "Also: if this is a retry after a previous run failed partway, click"
echo "'New task' in Bob's panel first. Re-cloning the folder resets the"
echo "files, but not Bob's own chat/task state - it can carry over context"
echo "from the failed attempt otherwise."
echo ""
read -p "Press Enter once permissions are set and Bob's chat panel is visible: " _
echo ""

# ---------------------------------------------------------------------------
# Steps 3-4: Agent mode
# ---------------------------------------------------------------------------
echo "=== Steps 3-4: mode = Agent ==="
echo ""
echo "--- Step 3: /init ---"
echo "What this does: Bob scans the cloned codebase (file structure, key"
echo "programs, conventions) and writes a distilled summary into AGENTS.md"
echo "at the workspace root. Future Bob sessions in this workspace auto-load"
echo "that file, so Bob doesn't have to re-learn the project from scratch"
echo "every time."
echo ""
echo "For this step only: typing '/in' (not the full command, not"
echo "submitted) so Bob's own autocomplete dropdown appears - pick the"
echo "correct '/init' entry yourself and press Enter in Bob to run it."
type_into_bob "/in" "false"
echo ""
read -p "Once you've selected /init from the dropdown and run it in Bob, press Enter here to continue: " _
confirm_or_extra "AGENTS.md"
echo ""

echo "--- Step 4: Scan the local workspace (per language, per Bob's own dialog) ---"
echo "What this does: builds/updates Bob's local index of the workspace"
echo "(stored under .bob/) so it can answer questions and generate"
echo "documents with better, faster context awareness of the actual code."
echo ""
echo "NOTE: Bob's own scan dialog is the authoritative source for which"
echo "languages actually get scanned here - it may NOT exactly match what"
echo "Bob says it generically supports (e.g. REXX can appear in a general"
echo "answer but not show up as an actual scan option, while something"
echo "like HLASM can appear in the dialog without being obviously named in"
echo "AGENTS.md). So this just scans off whatever Bob's dialog shows,"
echo "rather than trying to predict it beforehand."
echo ""
echo "Click directly into Bob's chat input box now."
sleep 2
type_into_bob "Scan the local workspace"
echo ""
echo "Bob should show a list of detected languages with file counts (e.g."
echo "COBOL (31 files), JCL (29 files), High Level Assembler (1 file))."
echo "Click the FIRST language in that list to scan it."
LANG_NUM=1
while true; do
  read -p "Language #$LANG_NUM finished scanning. Are there more languages in Bob's list you haven't scanned yet? [y/N]: " MORE_LANG
  if [ "$MORE_LANG" != "y" ] && [ "$MORE_LANG" != "Y" ]; then
    break
  fi
  LANG_NUM=$((LANG_NUM + 1))
  echo ""
  echo "Click directly into Bob's chat input box now - this matters, since"
  echo "focus may still be on the previous language's button/list, not the"
  echo "chat text field, which is exactly what caused a silent failure last"
  echo "time this ran."
  sleep 2
  type_into_bob "Scan the local workspace"
  echo "Click language #$LANG_NUM in Bob's list now."
done
confirm_or_extra ".bob"
echo ""

echo "--- Step 6: Create a Data Dictionary for this application ---"
echo "What this does: Bob analyzes the application's data structures"
echo "(copybooks, working storage, key fields) and produces a Data"
echo "Dictionary document, typically written under docs/, describing what"
echo "the application's data means and how it's structured."
echo ""
echo "(Switched back to an explicit chat prompt for this step - the"
echo "Workflows panel's 'Generate data dictionary' button wasn't actually"
echo "producing output.)"
echo ""
echo "Click directly into Bob's chat input box now."
sleep 2
type_into_bob "Create a Data Dictionary for this application"
confirm_or_extra "docs"
echo ""

echo "--- Step 7: create Bob rules for docs/ and tools/ conventions ---"
echo "What this does: defines standing rules for where Bob should store"
echo "future generated documents (docs/) and tools (tools/), and what"
echo "naming convention they must follow - so everything Bob produces"
echo "later in this workspace stays consistent."
RULES_PROMPT="Create the following Bob rules: - Documents must be stored in the docs/ directory, tools in tools/, - Document names must follow these conventions:
-- Prefix: program name (e.g., BNKMENU) if the document concerns a specific program, or CBSA for the application, or GLOBAL for cross-cutting documents
-- Document type: analysis, archi, docu, inv, plan, spec
-- Format: [PREFIX]-[TYPE]-[description].md"
type_into_bob "$RULES_PROMPT"
confirm_or_extra ".bob/rules"
echo ""

echo "=== Done. ==="
echo "Check for: AGENTS.md, .bob/, a Data Dictionary under docs/, and"
echo "rules reflected in .bob/rules/."