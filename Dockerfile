FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HF_HOME=/app/.cache/huggingface \
    PORT=7860

WORKDIR /app

RUN apt-get update \
    && apt-get install --no-install-recommends -y libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN python -m pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu \
    && python -m pip install --no-cache-dir -r requirements.txt

RUN python -c "from transformers import AutoFeatureExtractor, AutoModelForAudioClassification; name='garystafford/wav2vec2-deepfake-voice-detector'; revision='c66306024a7ede0be291e9c4558b37634782dc4e'; AutoFeatureExtractor.from_pretrained(name, revision=revision); AutoModelForAudioClassification.from_pretrained(name, revision=revision)"

COPY . .

RUN useradd --create-home --uid 1000 voiceguard \
    && chown -R voiceguard:voiceguard /app
USER voiceguard

EXPOSE 7860
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:7860/api/health', timeout=3)"
CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port ${PORT} --workers 1"]
