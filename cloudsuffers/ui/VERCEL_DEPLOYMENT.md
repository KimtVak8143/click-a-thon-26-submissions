# Vercel Deployment Guide

Complete guide to deploying the Context Compiler UI to Vercel.

---

## 📋 Prerequisites

1. **Backend Deployed**: Railway backend must be deployed and running
   - Get your Railway backend URL (e.g., `https://your-backend.railway.app`)
   - Ensure health check works: `curl https://your-backend.railway.app/health`

2. **Vercel Account**: Free account at [vercel.com](https://vercel.com)

3. **GitHub Repository**: Code pushed to GitHub

---

## 🚀 Quick Deploy

### Option 1: Deploy from GitHub (Recommended)

1. **Go to Vercel**:
   - Visit [vercel.com/new](https://vercel.com/new)
   - Click "Import Git Repository"

2. **Import Repository**:
   - Select your GitHub repository: `sidagarwal04/click-a-thon-26-submissions`
   - Click "Import"

3. **Configure Project**:
   ```
   Project Name: context-compiler-ui (or your choice)
   Framework Preset: Vite
   Root Directory: cloudsuffers/ui
   Build Command: npm run build
   Output Directory: dist
   Install Command: npm install
   ```

4. **Add Environment Variables**:
   - Click "Environment Variables"
   - Add variable:
     ```
     Name: VITE_COMPILER_API_URL
     Value: https://your-backend.railway.app
     ```
   - ⚠️ **IMPORTANT**: No trailing slash on the URL!

5. **Deploy**:
   - Click "Deploy"
   - Wait 1-2 minutes for build to complete

6. **Test Deployment**:
   - Click the deployment URL
   - Should see the Context Compiler dashboard
   - Test health checks (should show all green)

---

## 🔧 Configuration Details

### Environment Variables

Set in Vercel dashboard → Project Settings → Environment Variables:

```bash
VITE_COMPILER_API_URL=https://your-backend.railway.app
```

**Notes**:
- No trailing slash
- Must be the full Railway backend URL
- Variable is embedded in build (not runtime)
- Changes require redeployment

### Build Settings

Vercel automatically detects Vite configuration from `vercel.json`:

```json
{
  "framework": "vite",
  "buildCommand": "npm run build",
  "outputDirectory": "dist"
}
```

### Root Directory

**Critical**: Set root directory to `cloudsuffers/ui`
- This tells Vercel where to find package.json
- Without this, build will fail

---

## 🔐 Update Backend CORS

After deploying to Vercel, update Railway backend CORS settings:

1. **Get Vercel URL**: e.g., `https://context-compiler-ui.vercel.app`

2. **Update Railway Environment Variables**:
   ```bash
   CONTEXT_COMPILER_CORS_ALLOWED_ORIGINS=https://context-compiler-ui.vercel.app
   ```

3. **For Multiple Origins** (optional):
   ```bash
   CONTEXT_COMPILER_CORS_ALLOWED_ORIGINS=https://context-compiler-ui.vercel.app,https://custom-domain.com
   ```

4. **Railway will auto-restart** after environment variable changes

5. **Test CORS**:
   - Open browser DevTools → Network tab
   - Load your Vercel app
   - Check API requests succeed (no CORS errors)

---

## 🌐 Custom Domain (Optional)

### Add Custom Domain in Vercel

1. **Go to Project Settings** → Domains
2. **Add Domain**: Enter your domain (e.g., `compiler.yourdomain.com`)
3. **Configure DNS**:
   - Add CNAME record pointing to `cname.vercel-dns.com`
   - Or follow Vercel's DNS instructions
4. **Update Railway CORS**: Add custom domain to `CORS_ALLOWED_ORIGINS`

---

## 🔄 Preview Deployments

Vercel creates preview deployments for every PR:

### Preview URLs
- Format: `https://context-compiler-ui-git-branch-name.vercel.app`
- Automatically created on push to non-main branches
- Useful for testing changes before merging

### CORS for Preview Deployments

**Option 1: Specific Preview URLs** (recommended)
- Add preview URL to Railway CORS: `https://specific-preview.vercel.app`

**Option 2: Wildcard** (use with caution)
- Add to Railway CORS: `https://*.vercel.app`
- ⚠️ **Security**: Only use if preview URLs don't handle sensitive data

---

## 🐛 Troubleshooting

### Build Fails

**Problem**: Build fails with "Cannot find module"
- **Fix**: Ensure `package.json` is in `cloudsuffers/ui`
- **Fix**: Verify root directory is set to `cloudsuffers/ui`

**Problem**: Build fails with TypeScript errors
- **Fix**: Run `npm run build` locally first
- **Fix**: Fix TypeScript errors before deploying

### API Connection Issues

**Problem**: Frontend loads but API calls fail
- **Fix**: Check `VITE_COMPILER_API_URL` is set correctly
- **Fix**: Verify Railway backend is running
- **Fix**: Check Railway backend CORS allows Vercel URL
- **Fix**: No trailing slash on API URL

**Problem**: CORS errors in browser console
- **Fix**: Add Vercel URL to Railway `CORS_ALLOWED_ORIGINS`
- **Fix**: Ensure no trailing slashes in CORS origins
- **Fix**: Railway must restart after CORS changes

### Health Checks Fail

**Problem**: Health checks show red/error
- **Fix**: Verify Railway backend is accessible
- **Fix**: Check Railway backend health: `curl https://your-backend.railway.app/health`
- **Fix**: Verify ClickHouse and LLM are configured on Railway

### Environment Variable Changes Not Applied

**Problem**: Updated `VITE_COMPILER_API_URL` but old URL still used
- **Fix**: Redeploy the project (Vercel → Deployments → Redeploy)
- **Reason**: Vite embeds env vars at build time, not runtime

---

## 🔍 Verification Checklist

After deployment:

- [ ] Vercel deployment successful
- [ ] Frontend loads in browser
- [ ] Health checks show green
- [ ] Can upload spec and events files
- [ ] Pipeline runs successfully
- [ ] No CORS errors in console
- [ ] Railway backend CORS updated
- [ ] Custom domain working (if configured)

---

## 📊 Deployment Status

### Health Check Endpoints

Frontend calls these backend endpoints:

```bash
GET /health              # Overall health
GET /health/clickhouse   # ClickHouse connection
GET /health/llm          # LLM provider connection
```

All should return `200 OK` with `"status": "healthy"`.

### Expected Response

```json
{
  "status": "healthy",
  "service": "Context Compiler",
  "environment": "production",
  "version": "0.1.0"
}
```

---

## 🚀 Continuous Deployment

Vercel automatically redeploys on:
- **Push to main branch** → Production deployment
- **Push to other branches** → Preview deployment
- **Pull requests** → Preview deployment with comment

No manual deployment needed after initial setup!

---

## 📝 Production Checklist

Before going live:

- [ ] Railway backend deployed and tested
- [ ] Backend health checks passing
- [ ] ClickHouse connected and migrations run
- [ ] LLM provider configured
- [ ] Frontend deployed to Vercel
- [ ] CORS configured with Vercel URL
- [ ] Environment variables set
- [ ] Custom domain configured (optional)
- [ ] SSL certificate active (automatic)
- [ ] End-to-end pipeline test successful

---

## 🎯 Next Steps

1. ✅ Deploy to Vercel
2. ✅ Update Railway CORS
3. ✅ Test end-to-end
4. 🔜 Monitor with Vercel Analytics (optional)
5. 🔜 Set up custom domain (optional)

---

## 📞 Support

- **Vercel Docs**: https://vercel.com/docs
- **Vite Docs**: https://vitejs.dev
- **Railway Docs**: https://docs.railway.app

---

**Estimated Deployment Time**: 5 minutes
