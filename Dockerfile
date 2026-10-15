# Local smoke-test container (Python 3.11, matching velara_config.json).
# Standard-library only: there is NO pip install step, so nothing is pulled from public PyPI
# and nothing needs to resolve from Lilly's JFrog Artifactory.
#   Build:  docker build -t velara-agent .
#   Run:    docker run --rm -v "$(pwd)/my_pack:/pack" velara-agent solve.py /pack
FROM ubuntu:22.04
ENV DEBIAN_FRONTEND=noninteractive PYTHONUNBUFFERED=1
RUN apt-get update && apt-get install -y --no-install-recommends python3.11 ca-certificates \
    && apt-get clean && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY . .
ENTRYPOINT ["python3.11"]
CMD ["solve.py", "/pack"]
