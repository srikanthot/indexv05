// All resource-group-scoped resources + RBAC.

param env string
param location string
param baseName string
param pdfContainerName string
param aoaiChatDeployment string
param aoaiEmbedDeployment string
param searchSku string
param tags object

var namePrefix = '${baseName}-${env}'

// ---------------- Storage ----------------

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: toLower(replace('${baseName}${env}st', '-', ''))
  location: location
  tags: tags
  kind: 'StorageV2'
  sku: { name: 'Standard_LRS' }
  properties: {
    minimumTlsVersion: 'TLS1_2'
    allowBlobPublicAccess: false
    publicNetworkAccess: 'Enabled'
    supportsHttpsTrafficOnly: true
    allowSharedKeyAccess: false
  }
}

resource blobServices 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: storage
  name: 'default'
}

resource pdfContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobServices
  name: pdfContainerName
  properties: { publicAccess: 'None' }
}

// ---------------- Log Analytics + App Insights ----------------

resource logs 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: '${namePrefix}-logs'
  location: location
  tags: tags
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
  }
}

resource appi 'Microsoft.Insights/components@2020-02-02' = {
  name: '${namePrefix}-appi'
  location: location
  tags: tags
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: logs.id
    IngestionMode: 'LogAnalytics'
  }
}

// ---------------- Azure OpenAI ----------------

resource aoai 'Microsoft.CognitiveServices/accounts@2024-10-01' = {
  name: '${namePrefix}-aoai'
  location: location
  tags: tags
  kind: 'OpenAI'
  sku: { name: 'S0' }
  properties: {
    customSubDomainName: '${namePrefix}-aoai'
    publicNetworkAccess: 'Enabled'
    disableLocalAuth: false
  }
  identity: { type: 'SystemAssigned' }
}

resource aoaiEmbed 'Microsoft.CognitiveServices/accounts/deployments@2024-10-01' = {
  parent: aoai
  name: aoaiEmbedDeployment
  sku: { name: 'Standard', capacity: 120 }
  properties: {
    model: { format: 'OpenAI', name: 'text-embedding-ada-002', version: '2' }
  }
}

resource aoaiChat 'Microsoft.CognitiveServices/accounts/deployments@2024-10-01' = {
  parent: aoai
  name: aoaiChatDeployment
  sku: { name: 'GlobalStandard', capacity: 50 }
  properties: {
    model: { format: 'OpenAI', name: 'gpt-4.1', version: '2025-04-14' }
  }
  dependsOn: [ aoaiEmbed ]
}

// ---------------- Document Intelligence ----------------

resource di 'Microsoft.CognitiveServices/accounts@2024-10-01' = {
  name: '${namePrefix}-di'
  location: location
  tags: tags
  kind: 'FormRecognizer'
  sku: { name: 'S0' }
  properties: {
    customSubDomainName: '${namePrefix}-di'
    publicNetworkAccess: 'Enabled'
    disableLocalAuth: false
  }
}

// ---------------- AI Services multi-service (for built-in Layout skill billing) ----------------

resource aiServices 'Microsoft.CognitiveServices/accounts@2024-10-01' = {
  name: '${namePrefix}-ais'
  location: location
  tags: tags
  kind: 'CognitiveServices'
  sku: { name: 'S0' }
  properties: {
    customSubDomainName: '${namePrefix}-ais'
    publicNetworkAccess: 'Enabled'
  }
}

// ---------------- Azure AI Search ----------------

resource search 'Microsoft.Search/searchServices@2024-03-01-preview' = {
  name: '${namePrefix}-search'
  location: location
  tags: tags
  sku: { name: searchSku }
  properties: {
    replicaCount: 1
    partitionCount: 1
    hostingMode: 'default'
    publicNetworkAccess: 'enabled'
    authOptions: {
      aadOrApiKey: { aadAuthFailureMode: 'http401WithBearerChallenge' }
    }
    semanticSearch: 'standard'
  }
  identity: { type: 'SystemAssigned' }
}

// ---------------- Function App (Linux, Python 3.11, consumption) ----------------

resource plan 'Microsoft.Web/serverfarms@2023-12-01' = {
  name: '${namePrefix}-plan'
  location: location
  tags: tags
  kind: 'linux'
  sku: { name: 'Y1', tier: 'Dynamic' }
  properties: { reserved: true }
}

resource func 'Microsoft.Web/sites@2023-12-01' = {
  name: '${namePrefix}-func'
  location: location
  tags: tags
  kind: 'functionapp,linux'
  identity: { type: 'SystemAssigned' }
  properties: {
    serverFarmId: plan.id
    httpsOnly: true
    siteConfig: {
      linuxFxVersion: 'Python|3.11'
      ftpsState: 'Disabled'
      minTlsVersion: '1.2'
      http20Enabled: true
      appSettings: [
        { name: 'AzureWebJobsStorage__accountName', value: storage.name }
        { name: 'AzureWebJobsStorage__credential', value: 'managedidentity' }
        { name: 'FUNCTIONS_EXTENSION_VERSION', value: '~4' }
        { name: 'FUNCTIONS_WORKER_RUNTIME', value: 'python' }
        { name: 'WEBSITE_RUN_FROM_PACKAGE', value: '1' }
        { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: appi.properties.ConnectionString }
        { name: 'AUTH_MODE', value: 'mi' }
        { name: 'AOAI_ENDPOINT', value: aoai.properties.endpoint }
        { name: 'AOAI_API_VERSION', value: '2024-12-01-preview' }
        { name: 'AOAI_VISION_DEPLOYMENT', value: aoaiChatDeployment }
        { name: 'AOAI_CHAT_DEPLOYMENT', value: aoaiChatDeployment }
        { name: 'DI_ENDPOINT', value: di.properties.endpoint }
        { name: 'DI_API_VERSION', value: '2024-11-30' }
        { name: 'SEARCH_ENDPOINT', value: 'https://${search.name}.search.windows.net' }
        { name: 'SEARCH_INDEX_NAME', value: 'mm-manuals-index' }
        { name: 'SKILL_VERSION', value: '3.0.0' }
      ]
    }
  }
}

// ---------------- Role assignments (all MI-based) ----------------

// Function App MI -> Storage (Blob Data Reader) for PDF fetch + functions host storage
var storageBlobDataOwnerRoleId = 'b7e6dc6d-f1e8-4753-8033-0f276bb0955b'
var storageBlobDataReaderRoleId = '2a2b9908-6ea1-4ae2-8e65-a410df84e7d1'
var cognitiveServicesOpenAIUserRoleId = '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd'
var cognitiveServicesUserRoleId = 'a97b65f3-24c7-4388-baec-2e87135dc908'
var searchIndexDataReaderRoleId = '1407120a-92aa-4202-b7e9-c0e197c71c8f'
var searchServiceContributorRoleId = '7ca78c08-252a-4471-8644-bb5ff32d4ba0'

resource funcBlobOwner 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storage.id, func.id, 'blobOwner')
  scope: storage
  properties: {
    principalId: func.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', storageBlobDataOwnerRoleId)
  }
}

resource funcAoaiUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(aoai.id, func.id, 'aoaiUser')
  scope: aoai
  properties: {
    principalId: func.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', cognitiveServicesOpenAIUserRoleId)
  }
}

resource funcDiUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(di.id, func.id, 'diUser')
  scope: di
  properties: {
    principalId: func.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', cognitiveServicesUserRoleId)
  }
}

resource funcSearchReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(search.id, func.id, 'searchReader')
  scope: search
  properties: {
    principalId: func.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', searchIndexDataReaderRoleId)
  }
}

// Search service MI -> Storage (Blob Data Reader) for the data source pull
resource searchBlobReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storage.id, search.id, 'blobReader')
  scope: storage
  properties: {
    principalId: search.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', storageBlobDataReaderRoleId)
  }
}

// Search service MI -> AOAI (OpenAI User) so the embedding skill + vectorizer can call in via identity
resource searchAoaiUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(aoai.id, search.id, 'aoaiUser')
  scope: aoai
  properties: {
    principalId: search.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', cognitiveServicesOpenAIUserRoleId)
  }
}

// Search service MI -> AI Services (Cognitive Services User) so the built-in Layout skill can bill against the AIServicesByIdentity entry
resource searchAisUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(aiServices.id, search.id, 'aisUser')
  scope: aiServices
  properties: {
    principalId: search.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', cognitiveServicesUserRoleId)
  }
}

// ---------------- Outputs ----------------

output functionAppName string = func.name
output functionAppHost string = '${func.name}.azurewebsites.net'
output functionPrincipalId string = func.identity.principalId
output searchEndpoint string = 'https://${search.name}.search.windows.net'
output searchServiceName string = search.name
output searchPrincipalId string = search.identity.principalId
output aoaiEndpoint string = aoai.properties.endpoint
output diEndpoint string = di.properties.endpoint
output aiServicesName string = aiServices.name
output aiServicesSubdomainUrl string = aiServices.properties.endpoint
output storageAccountName string = storage.name
output storageAccountId string = storage.id
output appInsightsConnectionString string = appi.properties.ConnectionString
