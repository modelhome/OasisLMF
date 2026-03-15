# Build:  docker build -t model-home/oasislmf:latest .
# Run:    echo '{}' | docker run --rm -i model-home/oasislmf:latest
# With input file mounted:
#   docker run --rm -v "$PWD/run:/run" model-home/oasislmf:latest /run/hurricane_losses.output.json

FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Build tools needed for oasislmf's C extensions (gulpy, fmpy kernels)
RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc g++ && \
    rm -rf /var/lib/apt/lists/*

# Install the package from source so the image tracks this repo's version.
# Copy only what pip needs to resolve and install dependencies first
# (layer-caches the heavy dep install separately from source changes).
COPY pyproject.toml ./
COPY oasislmf ./oasislmf

RUN pip install --no-cache-dir .

# runner.py only needs numpy — install explicitly since oasislmf doesn't declare it
RUN pip install --no-cache-dir numpy

# Copy the pipeline runner last (changes most often — keeps the dep layer cached)
COPY runner.py ./

ENTRYPOINT ["python", "runner.py"]
# Default: read CLIMADA hurricane_losses JSON from stdin
CMD ["-"]
