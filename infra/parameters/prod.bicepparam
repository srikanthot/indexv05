using '../main.bicep'

param env = 'prod'
param location = 'eastus2'
param baseName = 'mmmanuals'
param pdfContainerName = 'manuals'
param aoaiChatDeployment = 'gpt-4.1'
param aoaiEmbedDeployment = 'text-embedding-ada-002'
param searchSku = 'standard'
