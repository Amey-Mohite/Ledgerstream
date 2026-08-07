{{/*
=============================================================================
_helpers.tpl — reusable named templates ("functions") for the chart.

Files starting with "_" render nothing on their own; they only DEFINE named
templates that other templates pull in with `include "<name>" <context>`.
Centralising names + labels here means every object stays consistent, and a
naming change happens in ONE place. Whitespace is trimmed with {{- ... -}} so
these emit exactly the text intended, no stray newlines.
=============================================================================
*/}}

{{/*
Chart name — defaults to .Chart.Name, overridable via .Values.nameOverride.
trunc 63 + trimSuffix "-": k8s label values max out at 63 chars and can't end
in a dash. Used inside the label helpers below.
*/}}
{{- define "ledgerstream.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/*
Fully-qualified release prefix — the base for EVERY object name (Services,
Deployments, ConfigMap, Secret). Logic:
  • fullnameOverride set        → use it verbatim.
  • release name already contains the chart name (e.g. release "ledgerstream")
                                → just the release name  → "ledgerstream".
  • otherwise                   → "<release>-<chart>"    → "myrel-ledgerstream".
So with `helm install ledgerstream ...` you get clean names like
"ledgerstream-payment". This is why the computed cross-service URLs resolve.
*/}}
{{- define "ledgerstream.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- $name := default .Chart.Name .Values.nameOverride -}}
{{- if contains $name .Release.Name -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{/*
Common labels — the k8s-recommended set, stamped on every object's metadata.
Includes the stable selector labels PLUS chart/version/managed-by (which may
change over time and so must NOT be used as selectors — see below).
*/}}
{{- define "ledgerstream.labels" -}}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{ include "ledgerstream.selectorLabels" . }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end -}}

{{/*
Selector labels — the STABLE subset used to match Deployments↔Pods↔Services.
Deliberately excludes version/chart: a Deployment's selector is IMMUTABLE after
creation, so putting a changing value here would make upgrades fail.
*/}}
{{- define "ledgerstream.selectorLabels" -}}
app.kubernetes.io/name: {{ include "ledgerstream.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}
