FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends tmux \
    && rm -rf /var/lib/apt/lists/*

# Claude CLI must be installed in the container or mounted from the host.
# The user must run `claude login` on the host first and mount ~/.claude.

WORKDIR /app
COPY app.py .

EXPOSE 8200

CMD ["python3", "app.py"]
