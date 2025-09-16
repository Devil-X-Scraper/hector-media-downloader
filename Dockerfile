# Use a modern, slim Python base image
FROM python:3.11-slim

# Set the working directory inside the container
WORKDIR /app

# Upgrade pip to the latest version
RUN python -m pip install --upgrade pip

# Upgrade yt-dlp to the latest version to keep up with platform changes
RUN pip install --upgrade yt-dlp

# Install ffmpeg, which is required by yt-dlp for audio conversion
RUN apt-get update && apt-get install -y ffmpeg && rm -rf /var/lib/apt/lists/*

# Copy the requirements file first to leverage Docker layer caching
COPY requirements.txt ./

# Install the Python dependencies from your requirements file
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of your application code into the container
COPY . .

# Let Render know which port to expose
EXPOSE 10000

# The command to run your application using Uvicorn
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "10000"]
