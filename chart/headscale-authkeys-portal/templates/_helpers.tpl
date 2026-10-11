{{- define "portal.fullname" -}}
{{- if contains .Chart.Name .Release.Name -}}{{ .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else -}}{{ printf "%s-%s" .Release.Name .Chart.Name | trunc 63 | trimSuffix "-" }}{{- end -}}
{{- end -}}

{{- define "portal.labels" -}}
app.kubernetes.io/name: {{ .Chart.Name }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version }}
{{- end -}}

{{- define "portal.selectorLabels" -}}
app.kubernetes.io/name: {{ .Chart.Name }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "portal.validate" -}}
{{- $c := .Values.config -}}
{{- range $k, $v := dict "config.publicUrl" $c.publicUrl "config.headscalePublicUrl" $c.headscalePublicUrl "config.headscaleApiUrl" $c.headscaleApiUrl "config.oidc.issuer" $c.oidc.issuer "config.oidc.clientId" $c.oidc.clientId "existingSecret" $.Values.existingSecret -}}
{{- if not $v }}{{ fail (printf "%s is required" $k) }}{{ end -}}
{{- end -}}
{{- if and (not $c.admin.group) (not $c.admin.users) }}{{ fail "set config.admin.group and/or config.admin.users" }}{{ end -}}
{{- if ne (int .Values.replicas) 1 }}{{ fail "replicas must be 1 (in-memory locks and rate limits)" }}{{ end -}}
{{- end -}}
