# Relay Setup Guide

## Quick Start

### 1. Clone and Setup Environment

```bash
git clone <repo-url>
cd relay
python3 -m venv venv
source venv/bin/activate
pip install httpx fastapi uvicorn pyyaml
```

### 2. Configure API Keys

```bash
# Copy the example environment file
cp .env.example .env

# Edit with your API keys
nano .env  # or your preferred editor
```

### 3. Required Configuration

**Minimum required:**
```bash
# Set a secure relay key (clients will use this)
RELAY_KEY=sk-relay-your-secure-key-here

# Add at least one LLM provider API key
OPENAI_API_KEY=sk-your-openai-key-here
```

**Generate a secure relay key:**
```bash
openssl rand -base64 32
```

### 4. Start Relay

```bash
# Make start script executable
chmod +x start_relay.sh

# Start relay (loads .env automatically)
./start_relay.sh
```

### 5. Test Your Setup

**Health check:**
```bash
curl http://localhost:4000/health
```

**List available models:**
```bash
curl -H "Authorization: Bearer sk-relay-your-secure-key-here" \
     http://localhost:4000/v1/models
```

**Chat completion:**
```bash
curl http://localhost:4000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer sk-relay-your-secure-key-here" \
  -d '{
    "model": "gpt-4o",
    "messages": [{"role": "user", "content": "Hello!"}],
    "max_tokens": 50
  }'
```

## Environment Variables Priority

1. **RELAY_KEY** environment variable (highest priority)
2. `config.yaml` master_key (fallback)
3. No authentication if neither set

## Security Best Practices

- ✅ **Use strong, unique keys** - Generate with `openssl rand -base64 32`
- ✅ **Never commit .env** - Already in `.gitignore`
- ✅ **Rotate keys regularly** - Update `.env` and restart
- ✅ **Use different keys** for dev/staging/production
- ✅ **Limit API key permissions** at provider level

## Local Models (No API Keys Required)

### Ollama
```bash
# Install
brew install ollama

# Start server
ollama serve

# Pull models
ollama pull llama3.2
ollama pull qwen2.5:7b

# Use in relay
curl -H "Authorization: Bearer sk-relay-your-secure-key-here" \
     -d '{"model": "llama3", "messages": [{"role": "user", "content": "Hi"}]}' \
     http://localhost:4000/v1/chat/completions
```

### vLLM
```bash
# Install
pip install vllm

# Start server
vllm serve Qwen/Qwen2.5-72B-Instruct --port 8000

# Use model: "qwen-72b" in relay
```

## Troubleshooting

### Port already in use
```bash
# Check what's running on port 4000
lsof -i :4000

# Kill existing processes
pkill -f "unillm"

# Or use different port
./start_relay.sh 4001
```

### API key errors
```bash
# Check if env vars are loaded
echo $OPENAI_API_KEY

# Test specific provider
curl -H "Authorization: sk-your-key" \
     https://api.openai.com/v1/models
```

### Dependencies missing
```bash
# Install in virtual environment
source venv/bin/activate
pip install httpx fastapi uvicorn pyyaml
```

## Production Deployment

### Docker
```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
EXPOSE 4000
CMD ["python", "-m", "unillm.cli", "--config", "config.yaml", "--port", "4000"]
```

### Environment Variables
```bash
# Production .env
RELAY_KEY=sk-relay-$(openssl rand -base64 32)
OPENAI_API_KEY=sk-your-production-key
```

### Reverse Proxy (nginx)
```nginx
server {
    listen 80;
    server_name your-domain.com;
    
    location / {
        proxy_pass http://localhost:4000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```