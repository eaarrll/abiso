variable "subscription_id" {
  description = "Azure subscription to deploy into. Leave null to use ARM_SUBSCRIPTION_ID."
  type        = string
  default     = null
}

variable "name" {
  description = "Short name used in every resource name."
  type        = string
  default     = "abiso"
}

variable "location" {
  description = "Azure region. Southeast Asia (Singapore) is the closest to the Philippines."
  type        = string
  default     = "southeastasia"
}

variable "vm_size" {
  description = "VM size. B1ms (2 GiB) is enough for the relay; dataprep's geopandas overlay is the peak."
  type        = string
  default     = "Standard_B1ms"
}

variable "admin_username" {
  description = "Linux admin user (SSH key only, no password)."
  type        = string
  default     = "abiso"
}

variable "ssh_public_key" {
  description = "SSH public key for the admin user."
  type        = string
}

variable "admin_cidr" {
  description = "The only source range allowed to reach SSH, e.g. your home IP as x.x.x.x/32."
  type        = string

  validation {
    condition     = can(cidrhost(var.admin_cidr, 0)) && var.admin_cidr != "0.0.0.0/0"
    error_message = "admin_cidr must be a valid CIDR and must not open SSH to the whole internet."
  }
}

variable "dns_label" {
  description = "DNS label for the public IP: <label>.<region>.cloudapp.azure.com. Must be unique in the region."
  type        = string
  default     = "abiso-relay"
}

variable "repo_url" {
  description = "Git repository cloud-init clones onto the VM."
  type        = string
  default     = "https://github.com/eaarrll/abiso.git"
}
