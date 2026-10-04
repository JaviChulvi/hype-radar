locals {
  name_prefix = "${var.project_name}-${var.environment}"
  common_tags = merge(
    {
      Application = var.project_name
      Environment = var.environment
      ManagedBy   = "Terraform"
    },
    var.additional_tags,
  )
}

resource "aws_lightsail_instance" "app" {
  name              = "${local.name_prefix}-app"
  availability_zone = var.availability_zone
  blueprint_id      = var.instance_blueprint_id
  bundle_id         = var.instance_bundle_id
  ip_address_type   = "dualstack"
  key_pair_name     = var.key_pair_name

  user_data = templatefile("${path.module}/templates/bootstrap.sh.tftpl", {
    bootstrap_user = var.bootstrap_user
  })

  dynamic "add_on" {
    for_each = var.enable_auto_snapshots ? [1] : []

    content {
      type          = "AutoSnapshot"
      status        = "Enabled"
      snapshot_time = var.snapshot_time_utc
    }
  }

  lifecycle {
    precondition {
      condition     = startswith(var.availability_zone, var.aws_region)
      error_message = "availability_zone must belong to aws_region."
    }
  }
}

resource "aws_lightsail_instance_public_ports" "app" {
  instance_name = aws_lightsail_instance.app.name

  port_info {
    protocol          = "tcp"
    from_port         = 22
    to_port           = 22
    cidr_list_aliases = ["lightsail-connect"]
    cidrs             = var.ssh_allowed_ipv4_cidrs
    ipv6_cidrs        = var.ssh_allowed_ipv6_cidrs
  }

  # The CDN reaches the instance through its public HTTP origin.
  port_info {
    protocol   = "tcp"
    from_port  = 80
    to_port    = 80
    cidrs      = ["0.0.0.0/0"]
    ipv6_cidrs = ["::/0"]
  }
}

resource "aws_lightsail_static_ip" "app" {
  name = "${local.name_prefix}-ip"
}

resource "aws_lightsail_static_ip_attachment" "app" {
  static_ip_name = aws_lightsail_static_ip.app.name
  instance_name  = aws_lightsail_instance.app.name
}

resource "aws_lightsail_distribution" "app" {
  provider = aws.global

  name            = "${local.name_prefix}-cdn"
  bundle_id       = var.distribution_bundle_id
  ip_address_type = "dualstack"
  is_enabled      = true

  depends_on = [aws_lightsail_static_ip_attachment.app]

  origin {
    name            = aws_lightsail_instance.app.name
    region_name     = var.aws_region
    protocol_policy = "http-only"
  }

  # Dynamic and authenticated routes must never be shared through the CDN cache.
  default_cache_behavior {
    behavior = "dont-cache"
  }

  # Vite emits content-hashed assets that are safe to cache for a long time.
  cache_behavior {
    path     = "/assets/*"
    behavior = "cache"
  }

  cache_behavior_settings {
    allowed_http_methods = "GET,HEAD,OPTIONS,PUT,PATCH,POST,DELETE"
    cached_http_methods  = "GET,HEAD"
    minimum_ttl          = 0
    default_ttl          = 31536000
    maximum_ttl          = 31536000

    forwarded_cookies {
      option = "none"
    }

    # Forward only the viewer headers required by authentication and browser requests.
    forwarded_headers {
      option = "allow-list"
      headers_allow_list = [
        "Authorization",
        "Host",
        "Origin",
      ]
    }

    forwarded_query_strings {
      option = true
    }
  }
}

resource "aws_budgets_budget" "account_monthly" {
  name         = "${local.name_prefix}-account-monthly"
  budget_type  = "COST"
  limit_amount = tostring(var.budget_limit_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  notification {
    comparison_operator        = "GREATER_THAN"
    notification_type          = "ACTUAL"
    threshold                  = 50
    threshold_type             = "PERCENTAGE"
    subscriber_email_addresses = [var.budget_alert_email]
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    notification_type          = "FORECASTED"
    threshold                  = 80
    threshold_type             = "PERCENTAGE"
    subscriber_email_addresses = [var.budget_alert_email]
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    notification_type          = "ACTUAL"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    subscriber_email_addresses = [var.budget_alert_email]
  }
}
