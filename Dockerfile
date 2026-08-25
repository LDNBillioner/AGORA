FROM python:3.11-slim

# Prevent Python from writing .pyc files and enable unbuffered logging
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy project files
COPY . .

# Set working directory to src to match local running environment
WORKDIR /app/src

# Hugging Face Spaces expects port 7860 by default
EXPOSE 7860

CMD ["sh", "-c", "uvicorn Engine:app --host 0.0.0.0 --port ${PORT:-7860}"]
