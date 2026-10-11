# headscale-authkeys-portal

Self-service portal for issuing headscale pre-auth keys. Users sign in with the
same OIDC provider headscale uses; the portal talks to headscale only through
its REST API.

## Behaviour
- Users are matched to headscale users with `provider == oidc`, the same issuer,
  and the same username (+ email when both sides have one). `sub` is ignored.
  The portal never creates headscale users.
- A key set = N single-use keys (default 1, max 5), TTL 15 min, no tags, not
  reusable. Max 3 active sets per owner. Keys are shown once.
- Admins (`admin.group` or `admin.users`) see all sets, can revoke any and can
  create sets for any OIDC user.
- Headscale pre-auth keys have no label field, so the portal is stateless: a set
  is identified by (owner, shared expiration). Only keys that look like portal
  keys are shown or counted; manually created keys are ignored.
- Single replica only (locks and rate limits are in-process).

## Config
YAML file (`PORTAL_CONFIG`, see `config_example.yaml` and the chart `config:`) plus secrets from the
environment: `PORTAL_HEADSCALE_API_KEY`, `PORTAL_OIDC_CLIENT_SECRET`,
`PORTAL_SESSION_SECRET`. Create the headscale API key by hand
(`headscale apikeys create`) and put it into a Secret before deploying.

authentik: the portal needs its own OAuth2 provider/application, redirect URI
`<publicUrl>/auth/callback`, and the `groups` scope mapping if `admin.group` is used.
To use the same issuer as headscale, reuse headscale's provider or set
`oidc.headscaleIssuer`.

## Dev
    pip install -r requirements-dev.txt && pytest

## CI/CD
- `.github/workflows/image.yml`: tests, then multi-arch image to
  `ghcr.io/<owner>/headscale-authkeys-portal` (`main` -> `latest`,
  `development` -> `development`, tag `vX.Y.Z` -> `X.Y.Z`, plus `sha-<short>`).
- `.github/workflows/helm-release.yml`: publishes the chart to a Helm repo on
  GitHub Pages (`gh-pages` branch). `main` = stable (version from `Chart.yaml`),
  `development` = pre-release `<version>-dev.<run>+<sha>` with appVersion
  `development`.

Release flow: bump `version`/`appVersion` in `chart/headscale-authkeys-portal/Chart.yaml`,
merge to `main`, then push the git tag `v<appVersion>` so the image tag the
chart points to exists.

One-time setup (same as for the headscale chart): create an orphan `gh-pages`
branch containing an empty `index.yaml`, enable Pages from that branch, give
workflows read/write permission, and make the GHCR package public (or add an
imagePullSecret).

    helm repo add headscale-authkeys-portal https://<owner>.github.io/headscale-authkeys-portal/
    helm install portal headscale-authkeys-portal/headscale-authkeys-portal -f values.yaml
