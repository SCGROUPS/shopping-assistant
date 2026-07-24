targetScope = 'subscription'

@description('Short globally unique prefix used for Azure resource names.')
@minLength(4)
@maxLength(12)
param prefix string

@description('Azure region for the application resources.')
param location string = 'eastus2'

@description('PostgreSQL region. This subscription currently restricts PostgreSQL creation in East US and East US 2.')
param postgresLocation string = 'centralus'

@description('Resource group to create.')
param resourceGroupName string = 'rg-${prefix}-poc'

@description('Container image to deploy after it has been pushed to the new registry.')
param containerImage string = 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest'

@description('Changes on each rollout to force Container Apps to pull a rebuilt image tag.')
param buildRevision string = 'bootstrap'

@secure()
@description('Initial PostgreSQL administrator password.')
param postgresAdminPassword string

@description('Existing Azure AI Services subscription.')
param aiSubscriptionId string = '840b5c5c-3f4a-459a-94fc-6bad2a969f9d'

@description('Existing Azure AI Services resource group.')
param aiResourceGroupName string = 'ml'

@description('Existing Azure AI Services account with low-cost deployments.')
param aiAccountName string = 'ai-eastus2508770413322'

param chatDeployment string = 'gpt-5.4-mini'
param intentDeployment string = 'gpt-5-nano'
param embeddingDeployment string = 'text-embedding-3-small'
param imageDeployment string = 'gpt-image-1-mini'

resource aiAccount 'Microsoft.CognitiveServices/accounts@2024-10-01' existing = {
  scope: resourceGroup(aiSubscriptionId, aiResourceGroupName)
  name: aiAccountName
}

resource rg 'Microsoft.Resources/resourceGroups@2024-03-01' = {
  name: resourceGroupName
  location: location
  tags: {
    application: 'vietra'
    environment: 'poc'
    workload: 'intelligent-tourism-commerce'
  }
}

module resources 'resources.bicep' = {
  name: 'vietra-resources'
  scope: rg
  params: {
    prefix: prefix
    location: location
    postgresLocation: postgresLocation
    containerImage: containerImage
    buildRevision: buildRevision
    postgresAdminPassword: postgresAdminPassword
    chatDeployment: chatDeployment
    intentDeployment: intentDeployment
    embeddingDeployment: embeddingDeployment
    imageDeployment: imageDeployment
    aiEndpoint: aiAccount.properties.endpoints['OpenAI Language Model Instance API']
  }
}

module aiIntegration 'ai-integration.bicep' = {
  name: 'vietra-ai-integration'
  scope: resourceGroup(aiSubscriptionId, aiResourceGroupName)
  params: {
    aiAccountName: aiAccountName
    chatDeployment: chatDeployment
    intentDeployment: intentDeployment
    embeddingDeployment: embeddingDeployment
    imageDeployment: imageDeployment
    identityPrincipalId: resources.outputs.identityPrincipalId
  }
}

output resourceGroupName string = rg.name
output registryName string = resources.outputs.registryName
output registryLoginServer string = resources.outputs.registryLoginServer
output containerAppName string = resources.outputs.containerAppName
output containerAppUrl string = resources.outputs.containerAppUrl
output postgresServerName string = resources.outputs.postgresServerName
output keyVaultName string = resources.outputs.keyVaultName
