
#!/bin/bash

# Set the image name and tag
IMAGE_NAME="kospex:latest"

# Build without the layer cache by default.
#
# The Dockerfile's `dnf -y update` only actually runs when its layer is a cache
# miss. With caching on, "rebuilding" silently reproduces whatever package state
# existed when the layer was first built -- there is no signal that anything is
# stale. This image sat on seven-month-old layers; a Trivy scan of it found 214
# vulnerabilities (127 HIGH), every one with a fix already available. The
# identical build with --no-cache scanned clean (0/0).
#
# --pull also re-resolves the base image, but Docker Hub's rockylinux:9 tag is
# itself frozen at 9.3 (late 2023), so the base stays put and the refresh comes
# from dnf. The maintained image is rockylinux/rockylinux:9.
#
# Pass --cache to opt back in when iterating locally and you don't need freshness.
BUILD_ARGS="--no-cache --pull"
DOCKERFILE="Dockerfile"

for arg in "$@"; do
    case "$arg" in
        --cache)
            BUILD_ARGS=""
            echo "Layer cache ENABLED (--cache): OS packages may be stale."
            ;;
        *)
            DOCKERFILE="$arg"
            ;;
    esac
done
echo "Using Dockerfile: $DOCKERFILE"

# Build the Docker image
echo "Building Docker image: $IMAGE_NAME"
docker build $BUILD_ARGS -t $IMAGE_NAME -f $DOCKERFILE .

# Check if the build was successful
if [ $? -eq 0 ]; then
    echo "Docker image built successfully"
else
    echo "Failed to build Docker image"
    exit 1
fi

# Check if Trivy is installed
if ! command -v trivy &> /dev/null; then
    echo "Trivy is not installed. Please install Trivy to run the security scan."
    exit 1
fi

# Severities that fail the build. Override with e.g.
#   TRIVY_GATE_SEVERITY=CRITICAL ./build-image.sh
TRIVY_GATE_SEVERITY="${TRIVY_GATE_SEVERITY:-HIGH,CRITICAL}"

# Full report first, for visibility -- every severity, including findings with no
# fix available. This pass is informational and never fails the build.
echo "Running Trivy scan on $IMAGE_NAME"
trivy image "$IMAGE_NAME"

# Then the gate.
#
# `trivy image` exits 0 even when it finds vulnerabilities unless --exit-code is
# passed, so the previous version of this check could never fail: a scan full of
# CRITICALs printed its table and then reported success.
#
# --ignore-unfixed limits the gate to findings that are actually actionable. When
# this image last went stale it carried 127 HIGH vulnerabilities and every single
# one had a fix available, so a fixable-only gate would have caught it while still
# not blocking on upstream issues nobody here can resolve.
#
# Accepted findings live in .trivyignore.yaml next to this script, each with a
# reason and an expiry date. They apply to the gate only: the report above still
# lists them, so an accepted finding stays visible in every build.
IGNORE_FILE="$(cd "$(dirname "$0")" && pwd)/.trivyignore.yaml"
echo "Gating on fixable $TRIVY_GATE_SEVERITY vulnerabilities (accepted: $IGNORE_FILE)"
trivy image --quiet --exit-code 1 --ignore-unfixed \
    --severity "$TRIVY_GATE_SEVERITY" --ignorefile "$IGNORE_FILE" "$IMAGE_NAME"
TRIVY_STATUS=$?

if [ $TRIVY_STATUS -eq 0 ]; then
    echo "Trivy gate passed: no fixable $TRIVY_GATE_SEVERITY vulnerabilities"
elif [ $TRIVY_STATUS -eq 1 ]; then
    echo "FAILED: fixable $TRIVY_GATE_SEVERITY vulnerabilities found (listed above)."
    echo "If the image is simply stale, rebuild without the cache: ./build-image.sh"
    exit 1
else
    echo "FAILED: Trivy could not complete the scan (exit $TRIVY_STATUS)."
    exit "$TRIVY_STATUS"
fi
