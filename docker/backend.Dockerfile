FROM python:3.12.10-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 APEX_PROJECT_ROOT=/app
WORKDIR /app
COPY backend/pyproject.toml /app/backend/pyproject.toml
RUN python -c "import tomllib; p=tomllib.load(open('backend/pyproject.toml','rb'))['project']; deps=p['dependencies']+sum((p['optional-dependencies'][g] for g in ('iceberg','graph','judge')),[]); open('/tmp/requirements.txt','w').write('\n'.join(deps))" \
    && pip install --no-cache-dir -r /tmp/requirements.txt
COPY backend /app/backend
RUN pip install --no-cache-dir --no-deps ./backend
COPY configs /app/configs
COPY data/kb_documents.json data/eval_queries.json /app/data/
COPY scripts /app/scripts
RUN mkdir -p /app/data /app/reports && useradd --uid 10001 --create-home apex && chown -R apex:apex /app
USER apex
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "apex_rag.main:app", "--host", "0.0.0.0", "--port", "8000"]
