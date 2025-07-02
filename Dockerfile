# Dockerfile for Mercari Bot (Pinned ChromeDriver)

FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV DEBIAN_FRONTEND=noninteractive

# Set timezone to America/Sao_Paulo
ENV TZ=America/Sao_Paulo
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

# Install system libraries and Chrome dependencies
RUN apt-get update && apt-get install -y \
    wget curl unzip gnupg2 \
    xvfb libxi6 libgconf-2-4 libnss3 libxss1 \
    libasound2 libx11-xcb1 libappindicator3-1 \
    libgtk-3-0 libgbm1 libvulkan1 xdg-utils \
    fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

# Install Google Chrome
RUN wget https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb && \
    apt install -y ./google-chrome-stable_current_amd64.deb && \
    rm google-chrome-stable_current_amd64.deb

# Set and install a known-good ChromeDriver version
ENV CHROME_DRIVER_VERSION=114.0.5735.90

RUN curl -SL "https://chromedriver.storage.googleapis.com/${CHROME_DRIVER_VERSION}/chromedriver_linux64.zip" -o chromedriver.zip && \
    unzip chromedriver.zip && \
    mv chromedriver /usr/local/bin/ && \
    chmod +x /usr/local/bin/chromedriver && \
    rm chromedriver.zip

# Set working directory
WORKDIR /app

# Copy project files into container
COPY . /app/

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Run bot script
CMD ["python", "mercari_telegram_bot_config_improved.py"]
