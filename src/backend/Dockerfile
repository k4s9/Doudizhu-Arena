FROM condaforge/miniforge3:latest

WORKDIR /app

# Keep the same named Python environment as local development and CI.
RUN conda create --yes --name doudizhu-arena --override-channels --channel conda-forge python=3.12 pip
COPY src/backend/requirements.txt ./src/backend/requirements.txt
RUN /opt/conda/envs/doudizhu-arena/bin/python -I -m pip install --no-cache-dir -r src/backend/requirements.txt

# Preserve repository-relative resources and manifest inputs. Never copy .env,
# live databases, logs, or Git metadata into the image.
COPY src/backend/arena/ ./src/backend/arena/
COPY src/backend/evaluation/ ./src/backend/evaluation/
COPY src/backend/main.py src/backend/pyproject.toml ./src/backend/
COPY src/frontend/package-lock.json ./src/frontend/package-lock.json
RUN /opt/conda/envs/doudizhu-arena/bin/python -I -m pip install --no-cache-dir --no-deps -e ./src/backend

WORKDIR /app/src/backend
ENV DATABASE_URL=sqlite:////app/data/arena.db \
    LOG_DIR=/app/src/backend/logs \
    AGENTS_YAML_PATH=/app/src/backend/arena/config/agents.yaml

EXPOSE 8000

CMD ["/opt/conda/envs/doudizhu-arena/bin/python", "-I", "-B", "-m", "uvicorn", "main:app", "--app-dir", "/app/src/backend", "--host", "0.0.0.0", "--port", "8000"]
