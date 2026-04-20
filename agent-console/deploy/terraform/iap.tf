# Identity-Aware Proxy for the 45black Agent Console.
#
# Wires: Global HTTPS LB → Serverless NEG → Cloud Run → IAP.
# After applying, access the console at https://console.45black.tech
# (or whatever domain you point at the LB IP).
#
# Prerequisites:
#   - main.tf already applied (APIs, service accounts)
#   - Cloud Run service 'console' already deployed
#   - A domain you own with DNS you can edit
#   - An OAuth consent screen configured in the GCP project

variable "domain" {
  description = "Domain for the console UI, e.g. console.45black.tech"
  type        = string
  default     = "console.45black.tech"
}

variable "iap_members" {
  description = "List of IAP-authorized members, e.g. ['user:jon@45black.tech']"
  type        = list(string)
  default     = []
}

# --- OAuth consent screen (required for IAP) ----------------------------

resource "google_iap_brand" "console" {
  support_email     = "jon@45black.tech"
  application_title = "45black Agent Console"
  project           = var.project_id
}

resource "google_iap_client" "console" {
  display_name = "IAP-Console"
  brand        = google_iap_brand.console.name
}

# --- Serverless NEG (points at Cloud Run service) -----------------------

resource "google_compute_region_network_endpoint_group" "console_neg" {
  name                  = "console-neg"
  network_endpoint_type = "SERVERLESS"
  region                = var.region
  cloud_run {
    service = "console"
  }
}

# --- Backend service with IAP enabled ----------------------------------

resource "google_compute_backend_service" "console" {
  name                  = "console-backend"
  protocol              = "HTTP"
  load_balancing_scheme = "EXTERNAL_MANAGED"
  timeout_sec           = 300

  backend {
    group = google_compute_region_network_endpoint_group.console_neg.id
  }

  iap {
    oauth2_client_id     = google_iap_client.console.client_id
    oauth2_client_secret = google_iap_client.console.secret
  }
}

# --- IAM: who can access through IAP -----------------------------------

resource "google_iap_web_backend_service_iam_member" "console_access" {
  for_each = toset(var.iap_members)

  web_backend_service = google_compute_backend_service.console.name
  role                = "roles/iap.httpsResourceAccessor"
  member              = each.value
}

# --- URL map + HTTPS proxy + managed cert + forwarding rule -------------

resource "google_compute_url_map" "console" {
  name            = "console-url-map"
  default_service = google_compute_backend_service.console.id
}

resource "google_compute_managed_ssl_certificate" "console" {
  name = "console-cert"
  managed {
    domains = [var.domain]
  }
}

resource "google_compute_target_https_proxy" "console" {
  name             = "console-https-proxy"
  url_map          = google_compute_url_map.console.id
  ssl_certificates = [google_compute_managed_ssl_certificate.console.id]
}

resource "google_compute_global_address" "console" {
  name = "console-ip"
}

resource "google_compute_global_forwarding_rule" "console" {
  name                  = "console-fwd"
  target                = google_compute_target_https_proxy.console.id
  port_range            = "443"
  ip_address            = google_compute_global_address.console.address
  load_balancing_scheme = "EXTERNAL_MANAGED"
}

# --- HTTP → HTTPS redirect ----------------------------------------------

resource "google_compute_url_map" "redirect" {
  name = "console-http-redirect"
  default_url_redirect {
    https_redirect = true
    strip_query    = false
  }
}

resource "google_compute_target_http_proxy" "redirect" {
  name    = "console-http-proxy"
  url_map = google_compute_url_map.redirect.id
}

resource "google_compute_global_forwarding_rule" "redirect" {
  name                  = "console-http-fwd"
  target                = google_compute_target_http_proxy.redirect.id
  port_range            = "80"
  ip_address            = google_compute_global_address.console.address
  load_balancing_scheme = "EXTERNAL_MANAGED"
}

# --- Outputs -------------------------------------------------------------

output "lb_ip" {
  description = "Point your DNS A record here: ${var.domain} → this IP"
  value       = google_compute_global_address.console.address
}

output "iap_client_id" {
  value     = google_iap_client.console.client_id
  sensitive = true
}
