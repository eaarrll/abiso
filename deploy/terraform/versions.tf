terraform {
  required_version = ">= 1.6"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.0"
    }
  }

  # Local state is fine for a one-person pilot. For a team, move it to an
  # azurerm backend (storage account + container) before the first apply.
}

provider "azurerm" {
  features {}
  # Falls back to ARM_SUBSCRIPTION_ID when null.
  subscription_id = var.subscription_id
}
