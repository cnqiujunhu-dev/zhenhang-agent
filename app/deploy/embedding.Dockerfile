FROM python:3.13-slim
WORKDIR /service
RUN pip install --no-cache-dir fastapi==0.141.1 uvicorn==0.53.0 fastembed==0.8.1
COPY scripts/local_embedding_service.py /service/local_embedding_service.py
RUN useradd --uid 10002 --create-home --shell /usr/sbin/nologin embedding && mkdir -p /models/minilm-multilingual && chown -R embedding:embedding /models
USER embedding
ENV PYTHONUNBUFFERED=1 HF_HUB_DISABLE_TELEMETRY=1 TOKENIZERS_PARALLELISM=false
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "local_embedding_service:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
