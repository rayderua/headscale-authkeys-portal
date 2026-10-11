# headscale-authkeys-portal (Helm chart)

Self-service portal for issuing headscale pre-auth keys. Users sign in with the
same OIDC provider headscale uses (authentik); the portal talks to headscale
only through its REST API. See the [project README](../../README.md) for the
behaviour (limits, admins, user matching).

## Install

```bash
helm repo add headscale-authkeys-portal https://rayderua.github.io/headscale-authkeys-portal/
helm repo update
helm install portal headscale-authkeys-portal/headscale-authkeys-portal -f values.yaml
# pre-release builds from the development branch:
helm install portal headscale-authkeys-portal/headscale-authkeys-portal --devel -f values.yaml
```

## Before you install

1. **Headscale API key.** Create it by hand: `headscale apikeys create --expiration 90d`
   (it can be created only on the running leader). Rotate it before it expires.
2. **Secret.** The chart never generates secrets. Create one with three keys:

   ```bash
   kubectl create secret generic portal-secrets \
     --from-literal=apiKey='<headscale api key>' \
     --from-literal=oidcClientSecret='<oidc client secret>' \
     --from-literal=sessionSecret="$(openssl rand -hex 32)"
   ```

   Key names can be changed with `secretKeys.*`.
3. **authentik.** Use headscale's provider or create a new one. Add the redirect URI
   `<config.publicUrl>/auth/callback` (Strict). With `config.admin.group` the
   provider must also release the `groups` claim (scope mapping).

## Minimal values

```yaml
existingSecret: portal-secrets

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

The chart fails to render when a required value is missing, when neither
`config.admin.group` nor `config.admin.users` is set, or when `replicas` is not 1
(locks and rate limits live in process memory).

## Values

| Key | Default | Description |
|---|---|---|
| `image.repository` | `ghcr.io/rayderua/headscale-authkeys-portal` | Image repository |
| `image.tag` | `""` | Defaults to `appVersion` (`development` for dev chart builds) |
| `image.pullPolicy` | `IfNotPresent` | |
| `replicas` | `1` | Must be 1 |
| `existingSecret` | `""` | **Required.** Secret with `apiKey`, `oidcClientSecret`, `sessionSecret` |
| `secretKeys.apiKey` / `oidcClientSecret` / `sessionSecret` | same names | Key names inside the Secret |
| `config.publicUrl` | `""` | **Required.** Public portal URL (OIDC redirect URI base) |
| `config.headscalePublicUrl` | `""` | **Required.** Headscale URL shown to users and used in commands |
| `config.headscaleApiUrl` | `""` | **Required.** Private headscale URL for API calls |
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

The ConfigMap checksum is part of the pod template, so config changes restart
the pod. Secret changes do not: restart the Deployment after rotating a secret.