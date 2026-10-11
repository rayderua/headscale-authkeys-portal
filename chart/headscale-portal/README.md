# headscale-portal (Helm chart)

Self-service portal for headscale users (pre-auth keys, account creation, nodes). Users sign in with the
same OIDC provider headscale uses (authentik); the portal talks to headscale
only through its REST API. See the [project README](../../README.md) for the
behaviour (limits, admins, user matching).

## Install

```bash
helm repo add headscale-portal https://rayderua.github.io/headscale-portal/
helm repo update
helm install portal headscale-portal/headscale-portal -f values.yaml
# pre-releases (tags like v1.2.3-rc.1):
helm install portal headscale-portal/headscale-portal --devel -f values.yaml
```

## Before you install

1. **Headscale API key.** Create it by hand: `headscale apikeys create --expiration 90d`
   (it can be created only on the running leader). Rotate it before it expires.
2. **Secrets.** The chart creates a Secret from `secretsEnv` and passes it to the
   container as environment variables. Put the values into your encrypted values
   file (helmfile/SOPS or similar), not into git in plain text:

   ```yaml
   secretsEnv:
     PORTAL_HEADSCALE_API_KEY: "<headscale api key>"
     PORTAL_OIDC_CLIENT_SECRET: "<oidc client secret>"
     PORTAL_SESSION_SECRET: "<random string, 32+ characters>"   # openssl rand -hex 32
   ```

   Prefer to manage the Secret yourself? Create it with the same three keys and
   set `existingSecret: <name>`; the chart then creates no Secret.
3. **authentik.** Use headscale's provider or create a new one. Add the redirect URI
   `<config.publicUrl>/auth/callback` (Strict). With `config.admin.group` the
   provider must also release the `groups` claim (scope mapping).

## Minimal values

```yaml
secretsEnv:
  PORTAL_HEADSCALE_API_KEY: "..."
  PORTAL_OIDC_CLIENT_SECRET: "..."
  PORTAL_SESSION_SECRET: "..."

config:
  publicUrl: https://keys.example.com
  headscalePublicUrl: https://hs.example.com
  headscaleApiUrl: http://headscale.headscale.svc:8080   # private Service address
  oidc:
    issuer: https://auth.example.com/application/o/headscale/
    clientId: headscale
  admin:
    group: admins
    users: [root]

ingress:
  enabled: true
  className: nginx
  host: keys.example.com
  tls:
    - secretName: keys-tls
      hosts: [keys.example.com]
```

The chart fails to render when a required value or secret is missing, when neither
`config.admin.group` nor `config.admin.users` is set, or when `replicas` is not 1
(locks and rate limits live in process memory).

## Values

| Key | Default | Description |
|---|---|---|
| `image.repository` | `ghcr.io/rayderua/headscale-portal` | Image repository |
| `image.tag` | `""` | Defaults to `appVersion`, which equals the release tag without `v` |
| `image.pullPolicy` | `IfNotPresent` | |
| `replicas` | `1` | Must be 1 |
| `secretsEnv` | three `PORTAL_*` keys, empty | **Required** unless `existingSecret` is set. Env var name → value; the chart creates a Secret from it and uses `envFrom`. Extra keys are allowed |
| `existingSecret` | `""` | Use your own Secret (same `PORTAL_*` keys) instead of creating one |
| `config.publicUrl` | `""` | **Required.** Public portal URL (OIDC redirect URI base) |
| `config.headscalePublicUrl` | `""` | **Required.** Headscale URL shown to users and used in commands |
| `config.headscaleApiUrl` | `""` | **Required.** Private headscale URL for API calls |
| `config.signupLink` | `true` | Show a link that creates the headscale user through headscale's OIDC login (no tailscale client needed). Relies on headscale 0.29.x behaviour, not a documented feature |
| `config.oidc.issuer` | `""` | **Required.** OIDC issuer |
| `config.oidc.clientId` | `""` | **Required.** OIDC client id |
| `config.oidc.scopes` | `[openid, profile, email, groups]` | Requested scopes |
| `config.oidc.groupsClaim` | `groups` | Claim with the user's groups |
| `config.oidc.usernameClaim` | `preferred_username` | Claim matched with the headscale user name |
| `config.oidc.headscaleIssuer` | `""` | Issuer headscale used for its users; empty = `issuer` |
| `config.admin.group` | `""` | Members of this group are admins |
| `config.admin.users` | `[]` | Usernames that are admins |
| `config.limits.maxSets` | `3` | Active key sets per owner |
| `config.limits.maxDevices` | `5` | Keys per set |
| `config.limits.defaultDevices` | `1` | Preselected number of keys |
| `config.limits.ttlMinutes` | `15` | Key lifetime |
| `config.limits.createsPerMinute` | `10` | Rate limit per acting user |
| `config.tailscale.upArgs` | `[]` | Extra arguments for the generated `tailscale up` command, one per entry (e.g. `--accept-dns=true`). `--login-server` and `--authkey` are added by the portal; only simple flags are accepted |
| `config.session.maxAgeHours` | `8` | Session lifetime |
| `config.session.cookieSecure` | `true` | Set `false` only for plain-HTTP testing |
| `extraEnv` | `{}` | Extra environment variables (map) |
| `service.type` / `service.port` | `ClusterIP` / `80` | |
| `ingress.enabled` | `false` | |
| `ingress.className` / `annotations` / `host` / `tls` | | Standard ingress settings; `host` is required when enabled |
| `securityContext` | non-root uid 1000, read-only rootfs, drop ALL | Container security context |
| `resources` | 50m / 96Mi requests, 256Mi memory limit | |
| `nodeSelector` / `tolerations` / `affinity` | empty | |
| `extraManifests` | `[]` | Extra objects, templated with `tpl` |

## Upgrading

The ConfigMap and the chart-created Secret checksums are part of the pod
template, so changes to `config` or `secretsEnv` restart the pod. With
`existingSecret` the chart cannot see changes: restart the Deployment yourself.
