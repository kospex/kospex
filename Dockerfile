FROM ubuntu:22.04 as builder

# Need curl for downloading the mergestat-lite and scc binaries
RUN apt-get update && \
    apt-get install -y curl 

# Install mergestat-lite and the .so file for python binding to do sqlite style queries
# For the moment, we'll grab the latest CLI but there' an issue with the .so file so we have to grab an older version
# See https://github.com/mergestat/mergestat-lite/issues/372
RUN curl -SL https://github.com/mergestat/mergestat-lite/releases/download/v0.6.1/mergestat-linux-amd64.tar.gz | tar -xzC /usr/local/bin/ \
&& rm /usr/local/bin/libmergestat.so

# Currently need to use the older version (0.5.10) as the .so file is not working in the latest version
RUN curl -SL https://github.com/mergestat/mergestat-lite/releases/download/v0.5.10/mergestat-linux-amd64.tar.gz | tar -xzC /tmp/ \
&& mv /tmp/libmergestat.so /usr/local/bin/ \
&& rm /tmp/mergestat 

# As far as counting lines of code and complexity, we'll use SCC
# https://github.com/boyter/scc
# The book "software design x-rays" book mentions CLOC, 
# but SCC wasn't invented then (or widespread)
RUN curl -SL https://github.com/boyter/scc/releases/download/v3.1.0/scc_3.1.0_Linux_x86_64.tar.gz | tar -xzC /usr/local/bin/ 

FROM rockylinux:9

WORKDIR /repos

COPY requirements.txt /build/requirements.txt
COPY --from=builder /usr/local/bin /usr/local/bin 

# Build using Rocky Linux

# Need to install python and git
RUN dnf install python3-pip git -y && \
    pip3 install -r /build/requirements.txt && \
    dnf clean all

# Change the shell prompt to show current directory
RUN echo 'export PS1="\w > "' >> /root/.bashrc

# Copy the CLI app into the container
COPY . /app/

# Set the PATH variable as our main CLI app 'kospex' will be in the /app directory
ENV PATH="${PATH}:/app"


CMD [ "/bin/bash"]  