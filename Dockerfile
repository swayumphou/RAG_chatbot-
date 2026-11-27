# RUPP-SPKB v2.0: the Telegram bot.
# Secrets (TELEGRAM_BOT_TOKEN, KB_SALT) come from the environment at run time;
# they are never copied into the image (.env is in .dockerignore).
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# CPU-only PyTorch keeps the image small; bge-m3 runs fine on CPU for queries.
COPY requirements.txt .
RUN pip install --no-cache-dir --extra-index-url https://download.pytorch.org/whl/cpu \
        -r requirements.txt

COPY . .

# The repo has a datasets/ package with the same name as the Hugging Face
# "datasets" library that bge-m3 (FlagEmbedding) needs. Putting /app AFTER the
# installed libraries (a .pth file) and starting from another folder lets the
# real library win. Remove these lines once datasets/ is renamed.
RUN echo /app > "$(python -c 'import site; print(site.getsitepackages()[0])')/rupp_app.pth"
WORKDIR /srv

CMD ["python", "-m", "bot.main"]
