# Dockerfile for tec-suite
# Builds an image capable of running the Python-based tec-suite tools.

FROM python:3.12-slim

# set working directory
WORKDIR /app

# copy project sources
COPY . /app

# optional install, makes modules importable and gives entrypoints
RUN pip install --no-cache-dir .

# helper script available via module invocation
ENTRYPOINT ["python", "-m", "process_rinex"]
CMD ["-h"]
