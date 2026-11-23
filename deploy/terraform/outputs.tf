output "public_ip" {
  value = azurerm_public_ip.abiso.ip_address
}

output "fqdn" {
  value = azurerm_public_ip.abiso.fqdn
}

output "webhook_health_url" {
  value = "https://${azurerm_public_ip.abiso.fqdn}/health"
}

output "ssh" {
  value = "ssh ${var.admin_username}@${azurerm_public_ip.abiso.ip_address}"
}
