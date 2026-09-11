#!/bin/bash
# Run this ONCE, from anywhere: ./setup_mfhome.sh
# Adds MFHOME to your ~/.zshrc (if not already there) and confirms it.
# After this, close/reopen Terminal (or run: source ~/.zshrc) and every
# new terminal session will have $MFHOME available automatically.

MFHOME_PATH="/Users/syedmohammadmansoor/Downloads/MainframeChangeAI"
ZSHRC="$HOME/.zshrc"

if grep -q "export MFHOME=" "$ZSHRC" 2>/dev/null; then
  echo "MFHOME is already set in $ZSHRC - not adding it again:"
  grep "export MFHOME=" "$ZSHRC"
else
  echo "export MFHOME=\"$MFHOME_PATH\"" >> "$ZSHRC"
  echo "Added MFHOME to $ZSHRC"
fi

# Load it into the CURRENT shell too, so it works immediately without
# needing to open a new terminal window.
export MFHOME="$MFHOME_PATH"

echo ""
echo "MFHOME is now set to: $MFHOME"
echo ""
echo "This will persist in NEW terminal windows automatically from now on."
echo "For THIS current terminal window, run this once more if \$MFHOME"
echo "doesn't show up after running this script:"
echo "    source ~/.zshrc"
