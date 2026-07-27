
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
# --pull also re-resolves the base image, though note the Dockerfile currently
# pins rockylinux:9 by digest, so the base itself stays put and the refresh comes
# from dnf.
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

# Run Trivy scan on the newly created image
echo "Running Trivy scan on $IMAGE_NAME"
trivy image $IMAGE_NAME

# Check Trivy scan exit code
if [ $? -eq 0 ]; then
    echo "Trivy scan completed successfully"
else
    echo "Trivy scan found vulnerabilities or encountered an error"
fi
