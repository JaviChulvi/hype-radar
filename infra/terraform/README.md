# Hype Radar AWS infrastructure

This Terraform root module provisions the small AWS footprint used by the course demo:

- one Ubuntu Lightsail instance bootstrapped with Docker Engine, Docker Compose, and the application images;
- one attached static IPv4 address;
- a Lightsail firewall exposing HTTP and browser-based SSH;
- a Lightsail CDN distribution with HTTPS at the edge;
- an account-wide monthly AWS budget with email alerts.

The regional provider creates the instance, IP, and firewall in `aws_region`. A second provider uses
`us-east-1` exclusively for the global Lightsail distribution API; its origin can remain in any
supported Lightsail Region.

The default plans cost approximately USD 9.50 per month before taxes and overages: USD 7 for the
1 GB instance and USD 2.50 for the 50 GB CDN plan. An attached static IP has no additional charge.
The budget sends alerts but does not cap spending or stop resources.

## CDN and security behavior

The distribution does not cache the application by default. It forwards `Authorization`, `Host`,
`Origin`, and all query strings so that Basic Auth, chat requests, audio transcription, and
WebSockets continue to work. Only Vite's content-hashed `/assets/*` files are cached.

TLS terminates at the Lightsail distribution. The distribution currently connects to the instance
over HTTP because the production Nginx image does not yet expose origin TLS. Port 80 therefore has
to remain public for the CDN origin. Use the generated HTTPS distribution URL and never submit Basic
Auth credentials directly to the static IP. End-to-end TLS and tighter origin access are sensible
hardening steps for a long-lived production system.

## Prerequisites

- Terraform 1.7 or newer;
- AWS CLI credentials with access to Lightsail and AWS Budgets;
- sufficient account quota for one instance, one static IP, and one distribution.

Authenticate with the AWS CLI before running Terraform. For an AWS Builder ID or IAM Identity Center
session, this commonly means running `aws login` or `aws sso login --profile <profile>` and exporting
the selected profile through `AWS_PROFILE`.

## Create the infrastructure

```sh
cd infra/terraform
cp terraform.tfvars.example terraform.tfvars
```

Set `budget_alert_email` in `terraform.tfvars`. Optionally add your current public IP as a `/32` to
`ssh_allowed_ipv4_cidrs` when direct SSH is needed. Browser-based Lightsail SSH is allowed by default.

```sh
terraform init
terraform fmt -check
terraform validate
terraform plan -out=tfplan
terraform apply tfplan
```

Creating the CDN can take several minutes. During bootstrap, the instance clones `repository_url` at
`repository_ref`, prepares the production environment file, builds both application images, and
installs an enabled `hype-radar.service` unit. It deliberately does not place passwords or API keys
in user data because Terraform state and cloud-init data are not appropriate secret stores.

After the instance is ready, open browser SSH using the `browser_ssh_url` output and confirm that
bootstrap completed:

```sh
sudo cloud-init status --wait
sudo test -f /opt/hype-radar/.bootstrap-complete
docker compose version
```

Edit the prepared `/opt/hype-radar/app/.env.production` file and replace every placeholder. Then
create `/opt/hype-radar/app/.secrets/htpasswd` as documented in the project README. Once both secret
files are ready, start the preinstalled service:

```sh
sudo systemctl start hype-radar.service
sudo systemctl status hype-radar.service
```

The service starts automatically on later boots after both required files exist. For application
updates, pull the desired Git ref and rebuild with the production Compose command from the project
README. Terraform ignores user-data changes for an existing instance because bootstrap runs only on
first boot; updated bootstrap logic applies when a new or replacement instance is created.

Use `terraform output -raw application_url` as `OPENROUTER_SITE_URL`. The CDN forwards HTTP traffic
to port 80 on the instance; no application container ports other than the Nginx frontend are public.

## Remove the infrastructure

```sh
terraform plan -destroy -out=destroy.tfplan
terraform apply destroy.tfplan
```

Destroying the Lightsail instance also removes data stored on its local disk. Copy any database data
that must be retained before destroying the stack. Terraform state and local `.tfvars` files are
ignored by Git; `.terraform.lock.hcl` should be committed.
