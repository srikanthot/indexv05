cd C:\index
$cfg = Get-Content deploy.config.json | ConvertFrom-Json
$storageAccount = ($cfg.storage.accountResourceId -split '/')[-1]
$cosmosAccount = ($cfg.cosmos.endpoint -replace 'https://','' -split '\.')[0]
$rg = $cfg.functionApp.resourceGroup

# Enable soft-delete on storage
az storage account blob-service-properties update --account-name $storageAccount -g $rg --enable-delete-retention true --delete-retention-days 30

# Create Cosmos database
az cosmosdb sql database create --account-name $cosmosAccount --resource-group $rg --name indexing --throughput 400

# Re-run deploy
python scripts/deploy.py --config deploy.config.json --auto-fix
