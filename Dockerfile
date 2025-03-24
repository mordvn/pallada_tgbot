FROM python:3.10-slim

# Set working directory
WORKDIR /app

# Install Poetry
RUN pip install poetry==1.7.1

# Copy Poetry configuration files
COPY pyproject.toml poetry.lock* ./

# Configure Poetry to not create a virtual environment
RUN poetry config virtualenvs.create false

# Install dependencies
RUN poetry install --no-dev --no-interaction

# Copy project files
COPY . .

# Create cache directory
RUN mkdir -p cache

# Set environment variable for Python to run in unbuffered mode
ENV PYTHONUNBUFFERED=1

# Run the bot
CMD ["python", "src/__init__.py"] 