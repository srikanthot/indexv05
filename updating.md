Copilot — please GRANT Azure roles to the Jenkins service principal so the dev pipeline's bootstrap can
read resources and assign roles. I am ALREADY logged into the DEV subscription in this terminal. Run the
PowerShell below. Do NOT set $ErrorActionPreference='Stop' (az writes progress to stderr).

WHAT THIS DOES: grants 5 roles to the SP "PSEG Tech Manual - Jenkins" at the DEV SUBSCRIPTION scope
(covers every resource group in dev). This fixes the bootstrap "AuthorizationFailed on storage read"
and lets assign_roles.py grant roles to the function-app + search managed identities.

# --- 0. confirm we are on the DEV subscription (sanity check) ---
az account show --query "{name:name, id:id}" -o table
# ^ confirm this is the DEV subscription. If not: az account set --subscription <DEV_AZURE_SUBSCRIPTION_ID>

$sub   = az account show --query id -o tsv
$scope = "/subscriptions/$sub"

# --- 1. find the Jenkins robot's appId by display name ---
$sp = az ad sp list --display-name "PSEG Tech Manual - Jenkins" --query "[0].appId" -o tsv
Write-Host "SP appId = $sp"
if ([string]::IsNullOrWhiteSpace($sp)) {
    Write-Host "!! SP appId came back blank. Tell Srikanth -- we will pull the appId from Entra ID or the Jenkins azure-client-id credential instead."
}

# --- 2. grant the 5 roles at subscription scope ---
foreach ($role in @(
    "Contributor",
    "User Access Administrator",
    "Storage Blob Data Contributor",
    "Search Index Data Contributor",
    "Cognitive Services User"
)) {
    Write-Host "=== granting: $role ==="
    az role assignment create --assignee $sp --role $role --scope $scope
}

# --- 3. verify what the SP now has ---
Write-Host "=== current role assignments for the SP ==="
az role assignment list --assignee $sp --scope $scope --query "[].roleDefinitionName" -o table

# Report back to Srikanth: the SP appId, any errors from step 2, and the final role list from step 3.
# NOTE: a 6th role (Cosmos DB Built-in Data Contributor) is assigned separately by CLI later -- Srikanth
# will provide that command when the pipeline reaches the Cosmos step. After this runs, wait ~5 minutes
# for propagation, then re-run the dev pipeline (ACTION=bootstrap).
