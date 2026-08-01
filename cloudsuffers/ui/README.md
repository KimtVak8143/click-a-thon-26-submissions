# Context Compiler UI

This standalone sibling project contains two UI services connected to the FastAPI backend:

- React dashboard: `http://localhost:4173` (preview) / `http://localhost:5173` (dev)
- LibreChat: `http://localhost:3080`

The React dashboard runs the complete `/pipeline/run` orchestration in dry-run mode by default and
renders the contract, schema plan, warnings, and insights. It also checks API, ClickHouse, and LLM
health independently.

---

## 🚀 Quick Start

### Development

Keep the backend running on port 8000, then:

```bash
npm install
npm run dev
```

Open `http://localhost:5173`. The browser calls `http://localhost:8000` directly by default.

---

## 📦 Production Deployment

### Deploy to Vercel (Recommended)

**Quick Deploy**: [QUICKSTART_VERCEL.md](./QUICKSTART_VERCEL.md) (~5 minutes)

**Complete Guide**: [VERCEL_DEPLOYMENT.md](./VERCEL_DEPLOYMENT.md)

**Summary**:
1. Go to [vercel.com/new](https://vercel.com/new)
2. Import repository: `sidagarwal04/click-a-thon-26-submissions`
3. Root directory: `cloudsuffers/ui`
4. Environment variable:
   ```
   VITE_COMPILER_API_URL=https://your-backend.railway.app
   ```
5. Deploy!

**After deployment**: Update Railway backend CORS with your Vercel URL.

### Configuration

- **Build Command**: `npm run build`
- **Output Directory**: `dist`
- **Framework**: Vite (auto-detected)
- **Environment**: See [DEPLOYMENT_SUMMARY.md](./DEPLOYMENT_SUMMARY.md)

---

## 🔧 Start the Complete UI Stack

Keep the backend running on port 8000, then create LibreChat secrets and start the UI stack:

```powershell
Copy-Item .env.example .env
# Replace every replace-with-* value in .env with a random secret.
docker compose up -d --build
```

Open the dashboard, then use **Open LibreChat** to register the first account (the first account is the administrator). Provider keys are configured as `user_provided`, so LibreChat asks each user for their own key rather than storing one in Git.

---

## 🔐 Security Notes

The Vercel build contains no backend credentials. Only the public backend origin is exposed to
the browser; Langfuse, ClickHouse, and model keys remain on Railway.

Preview deployments need either their explicit preview origin or a deliberate preview-domain policy; 
do not use credentialed wildcard CORS.

---

## 📡 API Endpoints

The dashboard calls:
- `/health` - Overall health status
- `/health/clickhouse` - ClickHouse connection
- `/health/llm` - LLM provider connection
- `/pipeline/run` - Main pipeline orchestration

---

## 🔧 Environment Variables

### Development
```bash
# Optional - defaults to http://localhost:8000
VITE_COMPILER_API_URL=http://localhost:8000
```

### Production (Vercel)
```bash
# Required - your Railway backend URL
VITE_COMPILER_API_URL=https://your-backend.railway.app
```

See `.env.development.example` and `.env.production.example` for templates.

---

## 📚 Documentation

- [QUICKSTART_VERCEL.md](./QUICKSTART_VERCEL.md) - 5-minute Vercel deploy
- [VERCEL_DEPLOYMENT.md](./VERCEL_DEPLOYMENT.md) - Complete deployment guide
- [DEPLOYMENT_SUMMARY.md](./DEPLOYMENT_SUMMARY.md) - Technical details

---

## 🎯 Scope

The dashboard calls `/health`, `/health/clickhouse`, `/health/llm`, and `/pipeline/run`. LibreChat is
intentionally a separate service; it is not presented as a Context Compiler agent until an
OpenAI-compatible gateway, MCP server, or LibreChat Action exists.
