# Production Deployment Guide

This guide covers production deployment of the Context Compiler backend to Railway.

## Table of Contents

- [Prerequisites](#prerequisites)
- [Environment Configuration](#environment-configuration)
- [Railway Deployment](#railway-deployment)
- [Docker Deployment](#docker-deployment)
- [Post-Deployment](#post-deployment)
- [Troubleshooting](#troubleshooting)

## Prerequisites

### Required Services

1. **ClickHouse Database**
   - Railway ClickHouse Plugin, or
   - ClickHouse Cloud (recommended for production)
   - Requires: host, port, username, password

2. **LLM Provider**
   - OpenAI API, or
   - OpenAI-compatible endpoint (OpenRouter, Together AI, etc.)
   - Requires: base URL, API key, model name

3. **GitHub Repository**
   - Code must be pushed to GitHub
   - Repository should be public or accessible to Railway

### Optional Services

- **Langfuse** - For LLM observability and tracing

## Environment Configuration

### Required Variables

```bash
# Environment
CONTEXT_COMPILER_APP_ENV=production
CONTEXT_COMPILER_LOG_LEVEL=INFO

# CORS - Replace with your frontend URL
CONTEXT_COMPILER_CORS_ALLOWED_ORIGINS=https://your-frontend.vercel.app

# ClickHouse
CONTEXT_COMPILER_CLICKHOUSE_HOST=your-host.clickhouse.cloud
CONTEXT_COMPILER_CLICKHOUSE_PORT=8443
CONTEXT_COMPILER_CLICKHOUSE_SECURE=true
CONTEXT_COMPILER_CLICKHOUSE_USERNAME=default
CONTEXT_COMPILER_CLICKHOUSE_PASSWORD=your-password
CONTEXT_COMPILER_CLICKHOUSE_DATABASE=default
CONTEXT_COMPILER_CLICKHOUSE_METADATA_DATABASE=compiler_meta

# LLM Provider
LLM_BASE_URL=https://api.openai.com/v1
LLM_API_KEY=sk-your-api-key-here
LLM_MODEL=gpt-4o-mini
LLM_STRUCTURED_OUTPUT_MODE=json_schema
```

### Optional Variables

```bash
# Langfuse (Optional)
CONTEXT_COMPILER_LANGFUSE_ENABLED=true
CONTEXT_COMPILER_LANGFUSE_PUBLIC_KEY=pk-lf-your-key
CONTEXT_COMPILER_LANGFUSE_SECRET_KEY=sk-lf-your-secret
CONTEXT_COMPILER_LANGFUSE_BASE_URL=https://cloud.langfuse.com

# Timeouts (Optional - defaults shown)
CONTEXT_COMPILER_CLICKHOUSE_CONNECT_TIMEOUT_SECONDS=10
CONTEXT_COMPILER_CLICKHOUSE_QUERY_TIMEOUT_SECONDS=30
LLM_TIMEOUT_SECONDS=30
LLM_TOTAL_GENERATION_TIMEOUT_SECONDS=600
```

## Railway Deployment

### Step 1: Create Railway Project

1. Visit [railway.app](https://railway.app)
2. Click **"New Project"** → **"Deploy from GitHub repo"**
3. Select your repository
4. Set **Root Directory**: `cloudsuffers/context-compiler`

### Step 2: Configure Build

Railway will auto-detect:
- `railway.toml` - Railway configuration
- `nixpacks.toml` - Build configuration
- `Procfile` - Start command
- `railway.json` - Alternative configuration

No manual build configuration needed.

### Step 3: Add Environment Variables

In Railway dashboard:
1. Go to your service → **Variables** tab
2. Click **"Raw Editor"**
3. Paste your environment variables (see above)
4. Click **"Save"**

### Step 4: Deploy

1. Railway will automatically deploy on push to main branch
2. Monitor deployment in **Deployments** tab
3. View logs in **Logs** tab
4. Check health: `https://your-service.railway.app/health`

### Railway Configuration Files

**railway.toml** - Railway-specific settings
```toml
[build]
builder = "nixpacks"

[deploy]
healthcheckPath = "/health"
healthcheckTimeout = 100
restartPolicyType = "on_failure"
restartPolicyMaxRetries = 3
```

**nixpacks.toml** - Build configuration
```toml
[phases.setup]
nixPkgs = ["python312", "uv"]

[phases.install]
cmds = ["uv sync --frozen --no-dev"]

[start]
cmd = "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}"
```

## Docker Deployment

### Build Image

```bash
docker build -t context-compiler:latest .
```

### Run Locally

```bash
# Create .env file
cp .env.example .env
# Edit .env with your configuration

# Run container
docker run -d \
  --name context-compiler \
  --env-file .env \
  -p 8080:8080 \
  context-compiler:latest
```

### Push to Registry

```bash
# Tag for your registry
docker tag context-compiler:latest your-registry/context-compiler:latest

# Push
docker push your-registry/context-compiler:latest
```

### Deploy to Cloud Run / ECS / etc.

Use the pushed image with your cloud provider's container deployment service.

## Post-Deployment

### 1. Verify Health

```bash
# Root endpoint
curl https://your-service.railway.app/

# Health check
curl https://your-service.railway.app/health

# ClickHouse health
curl https://your-service.railway.app/health/clickhouse

# LLM health  
curl https://your-service.railway.app/health/llm
```

Expected responses:

**Root:**
```json
{
  "service": "Context Compiler",
  "status": "running",
  "version": "0.1.0"
}
```

**Health:**
```json
{
  "status": "healthy",
  "service": "context-compiler",
  "environment": "production",
  "version": "0.1.0",
  "timestamp": "2026-08-02T12:00:00Z",
  "langfuse": "configured"
}
```

### 2. Test API Documentation

Visit: `https://your-service.railway.app/docs`

This opens the interactive Swagger UI.

### 3. Run Migrations (if needed)

If your deployment doesn't auto-run migrations:

```bash
# Using Railway CLI
railway run python -m app.clickhouse.migrations

# Or connect to your service and run manually
```

### 4. Update Frontend

Update your frontend's environment variables:

```bash
VITE_API_URL=https://your-service.railway.app
```

Redeploy frontend to Vercel.

## Troubleshooting

### Build Fails

**Issue:** `uv sync` fails
- **Solution:** Ensure `uv.lock` is committed to repository
- **Solution:** Check Python version matches (3.12)

**Issue:** Missing dependencies
- **Solution:** Run `uv lock` locally and commit changes

### Health Check Fails

**Issue:** `/health` returns 503
- **Check:** Are all environment variables set?
- **Check:** Can service reach ClickHouse?
- **Check:** Review logs for errors

**Issue:** ClickHouse connection fails
- **Check:** ClickHouse host/port/credentials correct
- **Check:** ClickHouse allows connections from Railway IPs
- **Check:** `SECURE=true` for ClickHouse Cloud (port 8443)

**Issue:** LLM health fails
- **Check:** LLM provider API key is valid
- **Check:** LLM base URL is reachable
- **Check:** Model name is correct

### CORS Errors

**Issue:** Frontend gets CORS errors
- **Solution:** Add frontend URL to `CONTEXT_COMPILER_CORS_ALLOWED_ORIGINS`
- **Solution:** Include both `https://` and `https://www.` versions
- **Solution:** Separate multiple origins with commas (no spaces)

Example:
```bash
CONTEXT_COMPILER_CORS_ALLOWED_ORIGINS=https://app.example.com,https://www.app.example.com
```

### High Latency

**Issue:** API responses slow
- **Check:** ClickHouse query performance
- **Check:** LLM timeout settings
- **Solution:** Increase `LLM_TIMEOUT_SECONDS` if needed
- **Solution:** Optimize ClickHouse queries

### Memory Issues

**Issue:** Container crashes with OOM
- **Solution:** Increase Railway service memory
- **Solution:** Reduce `CONTEXT_COMPILER_PROFILE_DISTINCT_LIMIT`
- **Solution:** Reduce `CONTEXT_COMPILER_PROFILE_MAX_UPLOAD_BYTES`

### Migration Errors

**Issue:** Migrations fail on startup
- **Check:** ClickHouse user has CREATE permissions
- **Check:** Metadata database exists
- **Solution:** Create database manually if needed

## Monitoring

### Railway Dashboard

- **Metrics**: CPU, Memory, Network usage
- **Logs**: Real-time structured JSON logs
- **Deployments**: Track deployment history
- **Usage**: Monitor costs and quotas

### Health Endpoints

Set up monitoring to check these endpoints:

- `GET /health` - Application health (200 OK)
- `GET /health/clickhouse` - Database health (200 OK)
- `GET /health/llm` - LLM provider health (200 OK)

Alert on:
- Non-200 status codes
- Response time > 5 seconds
- Consecutive failures > 3

### Langfuse (Optional)

If enabled, monitor:
- LLM generation latency
- Token usage and costs
- Generation success/failure rates
- Model performance

Access: https://cloud.langfuse.com

## Security Checklist

- [ ] All secrets in Railway environment variables (not code)
- [ ] `.env` file in `.gitignore` (never commit secrets)
- [ ] CORS origins restricted to frontend URLs only
- [ ] ClickHouse credentials use strong passwords
- [ ] LLM API keys rotated regularly
- [ ] HTTPS enforced (Railway provides automatically)
- [ ] Health checks don't expose sensitive data

## Performance Optimization

### ClickHouse

- Use connection pooling (already configured)
- Set appropriate timeouts
- Monitor query performance
- Scale instance if needed

### LLM

- Set reasonable timeout values
- Monitor token usage
- Use caching where appropriate
- Consider rate limiting

### Railway

- Start with smallest instance that works
- Monitor resource usage
- Scale up as needed
- Use horizontal scaling if available

## Support

- **Railway Docs**: https://docs.railway.app
- **ClickHouse Docs**: https://clickhouse.com/docs
- **FastAPI Docs**: https://fastapi.tiangolo.com
- **Project Issues**: GitHub Issues

---

**Last Updated:** 2026-08-02
