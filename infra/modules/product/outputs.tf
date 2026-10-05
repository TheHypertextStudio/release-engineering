output "distribution_bucket" { value = data.google_storage_bucket.distribution.name }
output "workload_identity_providers" {
  value = { for phase, provider in google_iam_workload_identity_pool_provider.github : phase => provider.name }
}
output "service_accounts" {
  value = { for phase, identity in google_service_account.lifecycle : phase => identity.email }
}
output "credential_bindings" {
  value = { for name, secret in google_secret_manager_secret.bindings : name => "${secret.id}/versions/latest" }
}
