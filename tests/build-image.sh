
#!/bin/bash

# Set the image name and tag
IMAGE_NAME="kospex:latest"

# Check if a Dockerfile is provided as an argument
if [ $# -eq 1 ]; then
    DOCKERFILE=$1
    echo "Using provided Dockerfile: $DOCKERFILE"
else
    DOCKERFILE="Dockerfile"
    echo "Using default Dockerfile"
fi

# Build the Docker image
echo "Building Docker image: $IMAGE_NAME"
docker build -t $IMAGE_NAME -f $DOCKERFILE .

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
