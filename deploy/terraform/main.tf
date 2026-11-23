# One small VM running the compose stack: webhook + 5-minute worker + Caddy.
# Deliberately not serverless: SQLite needs a local disk (file locking is
# unreliable on network shares), and the design goal is one box a single
# operator can reason about during a typhoon.

locals {
  tags = {
    project    = var.name
    managed_by = "terraform"
  }
}

resource "azurerm_resource_group" "abiso" {
  name     = "rg-${var.name}"
  location = var.location
  tags     = local.tags
}

resource "azurerm_virtual_network" "abiso" {
  name                = "vnet-${var.name}"
  location            = azurerm_resource_group.abiso.location
  resource_group_name = azurerm_resource_group.abiso.name
  address_space       = ["10.40.0.0/24"]
  tags                = local.tags
}

resource "azurerm_subnet" "abiso" {
  name                 = "snet-${var.name}"
  resource_group_name  = azurerm_resource_group.abiso.name
  virtual_network_name = azurerm_virtual_network.abiso.name
  address_prefixes     = ["10.40.0.0/27"]
}

resource "azurerm_network_security_group" "abiso" {
  name                = "nsg-${var.name}"
  location            = azurerm_resource_group.abiso.location
  resource_group_name = azurerm_resource_group.abiso.name
  tags                = local.tags

  # 80 is only for the ACME challenge and the redirect to 443.
  security_rule {
    name                       = "allow-web"
    priority                   = 100
    direction                  = "Inbound"
    access                     = "Allow"
    protocol                   = "Tcp"
    source_port_range          = "*"
    destination_port_ranges    = ["80", "443"]
    source_address_prefix      = "Internet"
    destination_address_prefix = "*"
  }

  security_rule {
    name                       = "allow-ssh-admin"
    priority                   = 110
    direction                  = "Inbound"
    access                     = "Allow"
    protocol                   = "Tcp"
    source_port_range          = "*"
    destination_port_range     = "22"
    source_address_prefix      = var.admin_cidr
    destination_address_prefix = "*"
  }
}

resource "azurerm_subnet_network_security_group_association" "abiso" {
  subnet_id                 = azurerm_subnet.abiso.id
  network_security_group_id = azurerm_network_security_group.abiso.id
}

# The DNS label gives Caddy a real hostname to get a certificate for, so
# the webhook has HTTPS without buying a domain.
resource "azurerm_public_ip" "abiso" {
  name                = "pip-${var.name}"
  location            = azurerm_resource_group.abiso.location
  resource_group_name = azurerm_resource_group.abiso.name
  allocation_method   = "Static"
  sku                 = "Standard"
  domain_name_label   = var.dns_label
  tags                = local.tags
}

resource "azurerm_network_interface" "abiso" {
  name                = "nic-${var.name}"
  location            = azurerm_resource_group.abiso.location
  resource_group_name = azurerm_resource_group.abiso.name
  tags                = local.tags

  ip_configuration {
    name                          = "primary"
    subnet_id                     = azurerm_subnet.abiso.id
    private_ip_address_allocation = "Dynamic"
    public_ip_address_id          = azurerm_public_ip.abiso.id
  }
}

resource "azurerm_linux_virtual_machine" "abiso" {
  name                            = "vm-${var.name}"
  location                        = azurerm_resource_group.abiso.location
  resource_group_name             = azurerm_resource_group.abiso.name
  size                            = var.vm_size
  admin_username                  = var.admin_username
  disable_password_authentication = true
  network_interface_ids           = [azurerm_network_interface.abiso.id]
  tags                            = local.tags

  admin_ssh_key {
    username   = var.admin_username
    public_key = var.ssh_public_key
  }

  os_disk {
    caching              = "ReadWrite"
    storage_account_type = "StandardSSD_LRS"
    disk_size_gb         = 32
  }

  source_image_reference {
    publisher = "Canonical"
    offer     = "ubuntu-24_04-lts"
    sku       = "server"
    version   = "latest"
  }

  # Secrets never go in here: custom_data is readable from the Azure API
  # and lands in Terraform state. cloud-init starts the stack in dry-run;
  # the operator adds channel credentials to /opt/abiso/.env over SSH.
  custom_data = base64encode(templatefile("${path.module}/cloud-init.yaml.tftpl", {
    repo_url = var.repo_url
    domain   = azurerm_public_ip.abiso.fqdn
  }))

  boot_diagnostics {}
}
