# Backend (FastAPI) — deployed as the `james-os-backend` service in
# the `bountiful-prosperity` Railway project. Repo root is the build
# context. Frontend has its own Dockerfile under web/.

FROM python:3.12-slim

# ffmpeg is required for the long-form-cutter slicer + audio extract
# pipeline. Keep the apt cache out of the layer to shrink the image.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install deps first so source edits don't bust the layer.
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir -e .

# Copy the rest (migrations, .py modules, etc.) AFTER install so the
# pip layer is cacheable across code-only changes.
COPY . .

# Railway sets $PORT. Bind 0.0.0.0 so the proxy can reach us.
ENV PORT=8000
EXPOSE 8000
CMD ["sh", "-c", "uvicorn james_os.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
