#!/bin/bash
# Build the standalone PDFEdit bundle for the current platform (macOS or Linux).
# For Windows, run build.bat from Command Prompt or PowerShell.
set -e
cd "$(dirname "$0")"

# macOS bundle identifier — override with PDFEDIT_BUNDLE_ID if you fork.
BUNDLE_ID="${PDFEDIT_BUNDLE_ID:-io.github.axia-enterprises.pdfedit}"

OS="$(uname -s)"
case "$OS" in
    Darwin) PLATFORM=mac ;;
    Linux)  PLATFORM=linux ;;
    *)      echo "Unsupported OS: $OS"; exit 1 ;;
esac

# On macOS we keep the build venv outside ~/Documents so Finder-launched apps
# work (TCC blocks Finder-launched processes from reading ~/Documents).
if [ "$PLATFORM" = "mac" ]; then
    VENV_DIR="$HOME/Library/Application Support/PDFEdit/.venv"
else
    VENV_DIR="$(pwd)/.venv"
fi
PY="$VENV_DIR/bin/python"

if [ ! -x "$PY" ]; then
    echo "Setting up build venv at $VENV_DIR …"
    mkdir -p "$(dirname "$VENV_DIR")"
    python3 -m venv "$VENV_DIR"
    "$PY" -m pip install --quiet --upgrade pip
    "$PY" -m pip install --quiet -r requirements.txt pyinstaller pillow
fi

# Generate icon if missing (macOS only — uses iconutil)
if [ "$PLATFORM" = "mac" ] && [ ! -f PDFEdit.icns ]; then
    "$PY" make_icon.py
fi

rm -rf build dist PDFEdit.spec

if [ "$PLATFORM" = "mac" ]; then
    "$PY" -m PyInstaller --windowed --noconfirm \
        --name PDFEdit \
        --osx-bundle-identifier "$BUNDLE_ID" \
        --icon PDFEdit.icns \
        pdfedit.py
    rm -rf PDFEdit.app
    mv dist/PDFEdit.app PDFEdit.app
    # Register PDF file association so Finder offers "Open With → Basic PDF Editor".
    PB=/usr/libexec/PlistBuddy
    PL="PDFEdit.app/Contents/Info.plist"
    $PB -c "Add :CFBundleDocumentTypes array" "$PL" 2>/dev/null || true
    $PB -c "Add :CFBundleDocumentTypes:0 dict" "$PL"
    $PB -c "Add :CFBundleDocumentTypes:0:CFBundleTypeName string PDF Document" "$PL"
    $PB -c "Add :CFBundleDocumentTypes:0:CFBundleTypeRole string Editor" "$PL"
    $PB -c "Add :CFBundleDocumentTypes:0:LSHandlerRank string Alternate" "$PL"
    $PB -c "Add :CFBundleDocumentTypes:0:LSItemContentTypes array" "$PL"
    $PB -c "Add :CFBundleDocumentTypes:0:LSItemContentTypes:0 string com.adobe.pdf" "$PL"
    ARTIFACT="PDFEdit.app"
    ZIP_NAME="PDFEdit-macos.zip"
else
    "$PY" -m PyInstaller --windowed --noconfirm \
        --name PDFEdit \
        pdfedit.py
    rm -rf PDFEdit
    mv dist/PDFEdit PDFEdit
    ARTIFACT="PDFEdit"
    ZIP_NAME="PDFEdit-linux.zip"
fi

rm -rf dist build PDFEdit.spec
rm -f "$ZIP_NAME"
zip -qry "$ZIP_NAME" "$ARTIFACT"

echo "Built: $ARTIFACT  +  $ZIP_NAME ($(du -sh "$ZIP_NAME" | cut -f1))"
