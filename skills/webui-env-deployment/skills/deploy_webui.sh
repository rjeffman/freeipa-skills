#!/bin/bash
set -e

SCENARIO=${1:-single-server}
FORCE_OVERWRITE=false

# Check if the --force flag was passed as the second argument
if [ "$2" == "--force" ]; then
    FORCE_OVERWRITE=true
fi

# --- Safety Check ---
if [ ! -f "./developer/dev-env.sh" ]; then
    echo "[ERROR] This script must be run from the root of the freeipa-webui repository."
    exit 1
fi

# --- Existing Environment Check ---
if command -v podman &> /dev/null; then
  container_list=$(podman ps -a --format "{{.Names}}" || true)
  if echo "${container_list}" | grep -Eq "^webui$|^freeipa-"; then
    echo "[INFO] An existing WebUI environment was detected."
    
    if [ "${FORCE_OVERWRITE}" = true ]; then
        echo "[INFO] Force flag detected. Proceeding to clean the current environment automatically..."
        ./skills/clean_webui.sh
    else
        # Check if running interactively (human in a terminal)
        if [ -t 0 ]; then
            read -rp "Do you want to set a new environment? (y/N): " choice
            case "${choice}" in 
              y|Y ) 
                echo "[INFO] Proceeding to clean the current environment..."
                ./skills/clean_webui.sh
                ;;
              * )
                echo "[INFO] Deployment aborted by the user."
                exit 0
                ;;
            esac
        else
            # Running non-interactively (e.g., via AI Agent like Lola or Cursor)
            echo "[ERROR] Environment already exists and script is running in non-interactive mode."
            echo "[HINT] Provide the '--force' flag to overwrite the environment automatically."
            exit 1
        fi
    fi
  fi
fi
# ------------------------------------------------

echo "=== Starting FreeIPA WebUI deployment (Scenario: ${SCENARIO}) ==="

# 1. Python Environment & Dependencies
if [ ! -d "venv" ]; then
    echo "[INFO] Creating Python virtual environment..."
    python3 -m venv venv
else
    echo "[INFO] Python venv already exists."
fi
# shellcheck source=/dev/null
source venv/bin/activate
pip install --quiet -U pip podman ansible-core podman-compose

# 2. Build and Setup the scenario
echo "[INFO] Building scenario..."
./developer/dev-env.sh -B "${SCENARIO}"
echo "[INFO] Setting up scenario..."
./developer/dev-env.sh -s "${SCENARIO}"

# 3. Modify /etc/hosts
if ! grep -q "webui.ipa.test" /etc/hosts; then
    if sudo -n true 2>/dev/null; then
        echo "192.168.56.10 webui.ipa.test" | sudo tee -a /etc/hosts
    else
        echo "[WARNING] Could not modify /etc/hosts. Sudo is required."
        echo "Please add '192.168.56.10 webui.ipa.test' manually."
    fi
fi

# 4. NPM Dependencies
echo "[INFO] Installing NPM dependencies inside Podman..."
podman exec webui npm install

# 5. Build WebUI for production
echo "[INFO] Building WebUI..."
podman exec webui npm run build

# 6. nss-tools and browser access
if sudo -n true 2>/dev/null; then
    sudo dnf install -y nss-tools
else
    echo "[WARNING] Could not install nss-tools automatically (requires sudo)."
fi

echo "=== Deployment completed ==="
echo "The WebUI should be available shortly at: https://webui.ipa.test/ipa/modern-ui"

if [ -n "${DISPLAY}" ]; then
    ./developer/open-browser.sh https://webui.ipa.test/ipa/modern-ui || true
fi