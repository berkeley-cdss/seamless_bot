# Use official Python image as a base
FROM python:3.10-slim

# Set environment variables to prevent Python from buffering outputs
ENV PYTHONUNBUFFERED=1

# Set the working directory in the container
WORKDIR /app

# Copy the requirements file and install dependencies
COPY requirements.txt requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy the entire bot code into the container
COPY . .

# Expose any required ports (if applicable)
EXPOSE 3000

# Command to run the Slackbot
CMD ["python", "app.py"]
