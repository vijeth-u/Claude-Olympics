# syntax=docker/dockerfile:1
#
# Local smoke-test container. Mirrors the evaluation agent's base image (Ubuntu 22.04)
# and installs only the Python version declared in goldentarget_config.json.
#
#   docker build -t golden-target .
#   docker run --rm -v "$(pwd)/exam:/pack" golden-target solve.py /pack
#
# Note: the agent installs from Lilly's JFrog Artifactory, not public PyPI. This tool has
# no dependencies, so that difference cannot bite here - which is part of why it has none.

FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        python3.11 \
        python3.11-venv \
        python3-pip \
        ca-certificates \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt

COPY . .

ENTRYPOINT ["python3.11"]
CMD ["solve.py", "/pack"]
