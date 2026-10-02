# Production CI/CD

`.github/workflows/deploy-main.yml` deploys Modal inference, the VPS backend,
and the Cloudflare frontend after pushes to `main`. It can also be started with
GitHub Actions → **Deploy main** → **Run workflow**.

Add these GitHub Actions secrets before the first deployment:

| Secret | Value |
| --- | --- |
| `MODAL_TOKEN_ID` | Modal token ID with deploy access to the production environment |
| `MODAL_TOKEN_SECRET` | Matching Modal token secret |
| `CLOUDFLARE_API_TOKEN` | Cloudflare token scoped to deploy Workers for the ECHO account |
| `CLOUDFLARE_ACCOUNT_ID` | Cloudflare account ID hosting `echo-frontend` |
| `VPS_HOST` | VPS public IP or SSH hostname |
| `VPS_USER` | SSH user; the current server uses `ubuntu` |
| `VPS_SSH_PRIVATE_KEY` | Private key for that SSH user |
| `VPS_SSH_KNOWN_HOSTS` | Verified SSH host key line(s) for the VPS |

The VPS account must be able to run Docker Compose without an interactive
`sudo` prompt. The workflow keeps the VPS Modal token in `/home/ubuntu/echo/.env`
with restrictive permissions and uses [`vps/docker-compose.yml`](vps/docker-compose.yml)
as the production service definition. Uploaded audio, built-in datasets,
Redis data, and shared job artifacts stay on the VPS across deployments.

Deployments run Modal first, then update the VPS backend; the frontend deploys
in parallel. The workflow checks the VPS `/health` endpoint before succeeding.
