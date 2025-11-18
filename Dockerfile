# Use the official Python 3.13 slim-bookworm image as the base
FROM python:3.13-slim-bookworm

# Install prerequisites for the uv installer
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates && \
    rm -rf /var/lib/apt/lists/*

# Download and run the uv installer to install uv (and uvx)
ADD https://astral.sh/uv/install.sh /uv-installer.sh
RUN sh /uv-installer.sh && rm /uv-installer.sh

# Ensure the installed uv binary (and tools) are on the PATH
ENV PATH="/root/.local/bin:$PATH"

# Set the working directory for your project
WORKDIR /app

# Copy over your dependency files first (to leverage Docker caching)
# If you use a lockfile (e.g. uv.lock), include it here as well.
COPY pyproject.toml uv.lock* ./

# Install your project’s dependencies (this will read the pyproject.toml)
RUN uv sync --frozen

# Now copy the rest of your project files
COPY . .

# Run your application using uv.
# (Replace 'my_app' with the command provided by your project.)
CMD ["uv", "run", "python3", "app/main.py"]
