FROM rockylinux:9

# This image is a base for testing installs of kospex.
# Either via a git clone && pip install -e or
# pip install kospex

# Update the system and install necessary packages
RUN dnf -y update && dnf -y upgrade && \
    dnf -y install \
    python3.11 \
    python3.11-pip \
    git \
    && dnf clean all

# Set default to 3.11 versions of python and pip
RUN ln -s /usr/bin/pip-3.11 /usr/bin/pip
RUN ln -s /usr/bin/python3.11 /usr/bin/python

# Upgrade pip
RUN pip install --upgrade pip
