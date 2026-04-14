// Azure AI Search multimodal manual indexing — full environment.
//
// Creates (or references) every resource the pipeline needs and wires up
// RBAC so the Function App's managed identity can reach OpenAI, DI,
// Storage, and Search without secrets.
//
// Usage:
//   az deployment sub create \
//     -l eastus2 \
//     -f infra/main.bicep \
//     -p infra/parameters/dev.bicepparam
//
// The deployment is subscription-scoped; it creates the resource group.

targetScope = 'subscription'

@description('Short environment tag: dev, staging, prod.')
@allowed(['dev', 'staging', 'prod'])
param env string

@description('Azure region for all resources.')
param location string = 'eastus2'

@description('Base name — resources are named {baseName}-{env}-{kind}.')
@minLength(3)
@maxLength(12)
param baseName string

@description('Blob container that holds source PDFs.')
param pdfContainerName string = 'manuals'

@description('Azure OpenAI chat/vision deployment name (should be a gpt-4.1 deployment).')
param aoaiChatDeployment string = 'gpt-4.1'

@description('Azure OpenAI embedding deployment name.')
param aoaiEmbedDeployment string = 'text-embedding-ada-002'

@description('SKU name for the Azure AI Search service.')
param searchSku string = 'standard'

@description('Prefix for the four Azure AI Search artifact names (datasource, index, skillset, indexer). Distinct prefixes let multiple stacks share a single search service without collisions.')
param searchArtifactsPrefix string = 'mm-manuals'

@description('SKILL_VERSION app setting; bump to invalidate cached diagram records when behavior changes.')
param skillVersion string = '3.0.0'

@description('AOAI chat/vision deployment capacity (in thousands of TPM units).')
param aoaiChatCapacity int = 50

@description('AOAI embedding deployment capacity (in thousands of TPM units).')
param aoaiEmbedCapacity int = 120

var rgName = '${baseName}-${env}-rg'
var tags = {
  env: env
  project: 'mm-manuals-index'
  'managed-by': 'bicep'
}

resource rg 'Microsoft.Resources/resourceGroups@2024-03-01' = {
  name: rgName
  location: location
  tags: tags
}

module resources 'modules/resources.bicep' = {
  name: 'resources'
  scope: rg
  params: {
    env: env
    location: location
    baseName: baseName
    pdfContainerName: pdfContainerName
    aoaiChatDeployment: aoaiChatDeployment
    aoaiEmbedDeployment: aoaiEmbedDeployment
    searchSku: searchSku
    searchArtifactsPrefix: searchArtifactsPrefix
    skillVersion: skillVersion
    aoaiChatCapacity: aoaiChatCapacity
    aoaiEmbedCapacity: aoaiEmbedCapacity
    tags: tags
  }
}

output resourceGroupName string = rg.name
output functionAppName string = resources.outputs.functionAppName
output functionAppHost string = resources.outputs.functionAppHost
output searchEndpoint string = resources.outputs.searchEndpoint
output searchServiceName string = resources.outputs.searchServiceName
output aoaiEndpoint string = resources.outputs.aoaiEndpoint
output diEndpoint string = resources.outputs.diEndpoint
output storageAccountName string = resources.outputs.storageAccountName
output storageAccountId string = resources.outputs.storageAccountId
output pdfContainerName string = pdfContainerName
output aiServicesSubdomainUrl string = resources.outputs.aiServicesSubdomainUrl
output appInsightsConnectionString string = resources.outputs.appInsightsConnectionString

// Search artifact names (consumed by scripts/deploy_search.py).
output datasourceName string = '${searchArtifactsPrefix}-ds'
output indexName string = '${searchArtifactsPrefix}-index'
output skillsetName string = '${searchArtifactsPrefix}-skillset'
output indexerName string = '${searchArtifactsPrefix}-indexer'
