output "instance_name" {
  description = "Lightsail instance name."
  value       = aws_lightsail_instance.app.name
}

output "static_ip_address" {
  description = "Static IPv4 address attached to the Lightsail instance."
  value       = aws_lightsail_static_ip.app.ip_address
}

output "distribution_domain_name" {
  description = "AWS-generated HTTPS domain for the Lightsail CDN distribution."
  value       = aws_lightsail_distribution.app.domain_name
}

output "application_url" {
  description = "Public HTTPS URL for the application."
  value       = "https://${aws_lightsail_distribution.app.domain_name}"
}

output "browser_ssh_url" {
  description = "Lightsail console URL used to open browser-based SSH."
  value       = "https://${var.aws_region}.console.aws.amazon.com/lightsail/home?region=${var.aws_region}#/instances/${aws_lightsail_instance.app.name}/connect"
}

output "budget_name" {
  description = "Account-wide AWS monthly cost budget name."
  value       = aws_budgets_budget.account_monthly.name
}
