FROM rockylinux:9

# This image is a base for testing installs of kospex.
# Either via a git clone && pip install -e or
# pip install kospex

# Update the system and install necessary packages
RUN dnf -y update && dnf -y upgrade && \
    dnf -y install \
    python3.12 \
    python3.12-pip \
    which \
    git \
    && dnf clean all

# Set default to 3.12 versions of python and pip
RUN ln -s /usr/bin/pip-3.12 /usr/bin/pip
RUN ln -s /usr/bin/python3.12 /usr/bin/python

# Upgrade pip
RUN pip install --upgrade pip
