variable "project_name" {
  description = "Short project identifier used to name AWS resources."
  type        = string
  default     = "hype-radar"

  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9-]{1,39}$", var.project_name))
    error_message = "project_name must contain 2 to 40 lowercase letters, digits, or hyphens, and must start with a letter or digit."
  }
}

variable "environment" {
  description = "Deployment environment included in resource names and tags."
  type        = string
  default     = "demo"

  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9-]{1,19}$", var.environment))
    error_message = "environment must contain 2 to 20 lowercase letters, digits, or hyphens, and must start with a letter or digit."
  }
}

variable "aws_region" {
  description = "AWS Region where Lightsail resources are created."
  type        = string
  default     = "eu-west-1"
}

variable "availability_zone" {
  description = "Availability Zone for the Lightsail instance. It must belong to aws_region."
  type        = string
  default     = "eu-west-1a"
}

variable "instance_blueprint_id" {
  description = "Lightsail operating system blueprint."
  type        = string
  default     = "ubuntu_24_04"
}

variable "instance_bundle_id" {
  description = "Lightsail instance plan. micro_3_0 currently provides 1 GB RAM and public IPv4."
  type        = string
  default     = "micro_3_0"
}

variable "distribution_bundle_id" {
  description = "Lightsail CDN plan. small_1_0 currently includes 50 GB of monthly transfer."
  type        = string
  default     = "small_1_0"
}

variable "key_pair_name" {
  description = "Optional existing Lightsail key pair name. Leave null to use the account default key and browser SSH."
  type        = string
  default     = null
  nullable    = true
}

variable "bootstrap_user" {
  description = "Linux user prepared by the bootstrap script. Change this when using a non-Ubuntu blueprint."
  type        = string
  default     = "ubuntu"
}

variable "ssh_allowed_ipv4_cidrs" {
  description = "Additional IPv4 CIDRs allowed to connect over SSH. Browser-based Lightsail SSH is always allowed."
  type        = set(string)
  default     = []

  validation {
    condition = alltrue([
      for cidr in var.ssh_allowed_ipv4_cidrs : can(cidrhost(cidr, 0)) && !strcontains(cidr, ":")
    ])
    error_message = "Every ssh_allowed_ipv4_cidrs value must be valid IPv4 CIDR notation."
  }
}

variable "ssh_allowed_ipv6_cidrs" {
  description = "Additional IPv6 CIDRs allowed to connect over SSH."
  type        = set(string)
  default     = []

  validation {
    condition = alltrue([
      for cidr in var.ssh_allowed_ipv6_cidrs : can(cidrhost(cidr, 0)) && strcontains(cidr, ":")
    ])
    error_message = "Every ssh_allowed_ipv6_cidrs value must be valid IPv6 CIDR notation."
  }
}

variable "enable_auto_snapshots" {
  description = "Enable paid daily Lightsail snapshots for the instance. Disabled by default to preserve the demo budget."
  type        = bool
  default     = false
}

variable "snapshot_time_utc" {
  description = "UTC start time for automatic snapshots when enabled."
  type        = string
  default     = "03:00"

  validation {
    condition     = can(regex("^(?:[01][0-9]|2[0-3]):00$", var.snapshot_time_utc))
    error_message = "snapshot_time_utc must use HH:00 in 24-hour UTC format."
  }
}

variable "budget_limit_usd" {
  description = "Account-wide monthly AWS cost budget in USD. Budgets alert but do not stop resources."
  type        = number
  default     = 15

  validation {
    condition     = var.budget_limit_usd > 0
    error_message = "budget_limit_usd must be greater than zero."
  }
}

variable "budget_alert_email" {
  description = "Email address that receives AWS Budget actual and forecast alerts."
  type        = string

  validation {
    condition     = can(regex("^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$", var.budget_alert_email))
    error_message = "budget_alert_email must be a valid email address."
  }
}

variable "additional_tags" {
  description = "Additional tags applied to taggable resources."
  type        = map(string)
  default     = {}
}
