FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV DEBIAN_FRONTEND=noninteractive
ENV TZ=America/Sao_Paulo

# Set timezone
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

# Install system dependencies required by Playwright
RUN apt-get update && apt-get install -y \
    wget curl gnupg2 unzip \
    libglib2.0-0 libnss3 libgconf-2-4 libxss1 libasound2 \
    libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 libxcomposite1 \
    libxdamage1 libxrandr2 libgbm1 libgtk-3-0 libx11-xcb1 \
    fonts-liberation xdg-utils \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy project files
COPY . /app/

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright and its browser binaries
RUN pip install playwright && playwright install --with-deps

# Run the bot
CMD ["python", "python mercari_telegram_bot_playwright.py"]

