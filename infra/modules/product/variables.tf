variable "project_id" { type = string }
variable "product" {
  type = string
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{0,20}$", var.product))
    error_message = "Product must fit the 30-character service account name."
  }
}
variable "region" { type = string }
variable "distribution_bucket" { type = string }
variable "repository_id" { type = string }
variable "owner_id" { type = string }
variable "default_branch" { type = string }
variable "tooling_repository" {
  type    = string
  default = "TheHypertextStudio/release-engineering"
}
variable "tooling_revision" {
  type = string
  validation {
    condition     = can(regex("^[0-9a-f]{40}$", var.tooling_revision))
    error_message = "Workload identity must bind the reviewed reusable workflow commit."
  }
}
variable "credential_names" {
  type    = set(string)
  default = []
}
variable "promotion_credential_names" {
  type    = set(string)
  default = []
  validation {
    condition     = length(setsubtract(var.promotion_credential_names, var.credential_names)) == 0
    error_message = "Promotion credentials must name declared product secrets."
  }
}
variable "runtime_service_accounts" {
  type    = map(string)
  default = {}
}
variable "pin_update_credential_names" {
  type    = set(string)
  default = []
  validation {
    condition     = length(setsubtract(var.pin_update_credential_names, var.credential_names)) == 0
    error_message = "Pin credentials must name declared product secrets."
  }
}
